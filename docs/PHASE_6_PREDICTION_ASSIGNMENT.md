# Phase 6 — Prediction & Assignment

## Boundary

Phase 6 mengubah snapshot model behavioral Phase 5, Loading Order, dan availability MT menjadi shipment, assignment, preliminary route, serta rolling next availability yang dapat diaudit. Phase ini tidak melatih model dan tidak menjalankan fleet-wide VRP; optimasi rute global tetap menjadi scope Phase 7.

Setiap run terikat pada satu depot dan satu model `SAVED`/`ACTIVE`. Input, parameter, model/config snapshot, prediction asli, perubahan dispatcher, dan hasil akhir dipertahankan secara immutable/auditable.

## Input dan Data Demo

Loading Order workbook memuat `loading_order_no`, `shipment_start_datetime`, `spbu_no`, `product`, dan `order_quantity_kl`. Setiap LO wajib tepat 8 KL; total demo harus habis dibagi 8 dan sistem menghasilkan `total KL / 8` baris LO. Product yang diisi dipetakan terhadap `master_product` dan `product_alias`; identifier yang tidak dikenal atau product nonaktif menjadi blocking error. File legacy tanpa product tetap dapat dibaca, sedangkan Data Demo dan LO baru dari management selalu mengisi product canonical aktif.

Setelah upload manual atau pembuatan Data Demo divalidasi, UI membuka **Loading Order Management**. Tabel ini mendukung Add, Edit, Delete, pagination, filter per kolom—termasuk Product—dan sort per header. Pada form Add/Edit, nomor SPBU wajib dipilih dari dropdown Master SPBU aktif milik depot run dan Product wajib dipilih dari dropdown Master Product aktif; keduanya bukan free text. Mutation tidak hanya mengubah preview: backend membentuk ulang workbook LO aktif dan memvalidasinya kembali, sehingga snapshot yang dikirim ke Run Prediction selalu sama dengan isi terakhir tabel. Error hasil perubahan tetap memblokir Run Prediction melalui kontrak validator yang sama.

Data Demo LO tidak memakai sembarang SPBU aktif. Kandidat harus berada pada assignment model yang dipilih dan memenuhi seluruh syarat berikut:

- SPBU master aktif pada depot run;
- bukan noise;
- `coverage_source=BEHAVIORAL_HISTORY`;
- `history_eligible=true`.

Cold-start, no-history, insufficient-history, inactive, dan unseen SPBU tidak digunakan. Timestamp dibentuk dalam batch cluster/dominant-shift agar pairing multi-SPBU dapat diuji tanpa mengubah evidence model.

MT Availability tidak lagi diunggah manual atau dibuat melalui Data Demo. User menekan **Import Data from Master Data** untuk mengambil seluruh MT canonical aktif pada depot terpilih. Card **MT Management** menampilkan No MT, MT Tag Class, Status, dan ETA on Depot. User tidak dapat Add/Delete MT; perubahan hanya berupa status operasional Active/Deactive dan ETA. Status tersebut tidak mengubah `master_mt`. Semua MT mendapat ETA pada tanggal operasi dan `start_time` Shift 1 dari snapshot model. MT dengan profil kapasitas 8 KL valid default Active; MT yang profilnya tidak valid tetap ditampilkan tetapi default Deactive beserta alasan validasinya. Hanya MT Active yang dibentuk menjadi workbook internal `vehicle_registration_no` + `initial_available_datetime` dan divalidasi backend. ETA on Depot berarti waktu MT mulai available di depot.

## Grouping dan Exact Full-Load Assignment

Algoritma `phase6.iterative_exact_capacity_assignment.v9` menjalankan empat tier secara berurutan:

```text
32 KL = 4 LO = MT 4 kompartemen
24 KL = 3 LO = MT 3 kompartemen
16 KL = 2 LO = MT 2 kompartemen
 8 KL = 1 LO = MT 1 kompartemen
```

Dalam setiap tier, `CAPACITY_TIME_ROUTE_SET_PACKING` menilai derived shift yang sama, maximum pairing gap, cluster dan historical pairing evidence, minimum confidence, master/tag compatibility, dan approximate route feasibility. MT harus lulus compatibility untuk seluruh SPBU dalam shipment.

Hanya shipment berstatus `ASSIGNED` atau `ASSIGNED_WITH_DELAY` yang mengonsumsi LO. Jika kandidat 32 KL tidak menemukan MT 32 KL yang compatible dan available, grup dibongkar dan LO dicoba kembali pada tier 24 KL, kemudian 16 KL, lalu 8 KL. MT yang lebih besar tidak boleh menjalankan partial load.

## Rolling Multi-Trip dan Routing

State awal MT berasal dari `initial_available_datetime`. Setelah assignment, sistem menghitung departure, cycle, return, turnaround buffer, dan `next_available_datetime`. MT yang sama dapat menjalankan beberapa trip selama timeline tidak overlap.

```text
total cycle
= depot processing
+ seluruh travel leg depot → SPBU → ... → depot
+ service time per stop
+ return processing

next available = estimated return + turnaround buffer
```

Mode `STRICT_START` mewajibkan MT tersedia pada planned start. `ALLOW_DELAY` dapat menggeser departure sampai batas delay dan memberi status `ASSIGNED_WITH_DELAY`.

Indonesia memakai Google Routes mode `DRIVE`; Large Vehicle/TRUCK dimatikan. Rute memakai cache/config-aware Google overview geometry bila tersedia, kemudian fallback historical/cluster/default yang ditandai jelas. Marker map selalu memakai koordinat Master Depot/SPBU; urutan stop adalah SPBU terdekat menuju terjauh dari depot.

## Worker, Queue, dan Recovery

`POST /api/v1/phase6/predictions` menyimpan snapshot serta durable job lalu mengembalikan `202 Accepted`. Worker terpisah mengklaim task FIFO sehingga UI tetap responsif dan user dapat mengantrekan run lain. Lease token, heartbeat, execution timeout, maksimum attempt, dan stale-job recovery mencegah satu worker macet mengunci aplikasi. Run yang melewati retry limit menjadi `FAILED` dengan diagnostic yang dapat diaudit dan dapat diulang dari saved input.

## Dispatcher Override dan UI

- Detail shipment menampilkan nomor cluster model di samping setiap nomor SPBU.
- Dropdown `Move to…` menampilkan shipment ID, daftar SPBU, dan cluster target pada shift yang sama.
- Penggantian MT atau grouping manual memicu ulang exact-capacity rolling assignment, route/cycle, multi-trip timeline, assigned KL per jam dan kumulatif, serta geographic route.
- Card 7–8 memakai pagination shipment; MT Multi-Trip Timeline memakai pagination MT.
- Geographic Route per MT menyediakan text search untuk mempersempit pilihan select nomor MT.
- Prediction Run History memakai pagination client-side 10/25/50 row, indikator rentang, Previous/page/Next, Refresh feedback, View, Export, dan Re-run.

## Output dan Audit

Prediction Summary membedakan LO, volume, shipment, assigned, assigned-with-delay, unassigned, multi-trip, fallback, dan confidence. Expanded shipment menyediakan structured explanation, candidate MT yang lulus/gagal, alasan exclusion, serta preliminary route estimate. Export workbook berisi Summary, Shipment Result, Trip Timeline, MT Assignment, MT Candidates, dan Validation.

Phase 6 mempertahankan `original_model_prediction` dan `final_dispatch_prediction`. Perubahan dispatcher tidak melatih ulang Phase 5 dan selalu dapat dibandingkan dengan hasil awal.

## Verifikasi

- migration head: `0016_phase5_evidence_coverage`;
- 65 backend tests dan 24 focused Phase 6 tests lulus;
- TypeScript dan Vite production build lulus;
- API, worker, web, dan PostgreSQL berjalan sehat pada Docker Compose.
