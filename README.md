# Dispatch Intelligence Platform

Dispatch Intelligence Platform adalah aplikasi analitik operasional distribusi BBM. Platform ini dibangun bertahap dari Phase 0 sampai Phase 6 untuk mengubah:

Master Data + Loading Order + GPS Operational Data + Historical Dispatch

menjadi trusted operational intelligence yang nanti dapat menjadi input untuk optimasi rute berbasis Google OR-Tools. Optimasi rute belum menjadi scope Phase 0-6.

Prinsip utama: jangan lanjut ke phase berikutnya sebelum phase berjalan benar, diuji, tervalidasi visual, terdokumentasi, dan usable.

## Run

```bash
cp .env.example .env
docker compose up --build
```

Services:

- API: `http://localhost:8000/api/v1/health`
- Web: `http://localhost:3000`
- PostgreSQL/PostGIS: `localhost:5432`

Catatan lokal: jika port `3000` sudah terpakai, jalankan web dengan port lain, misalnya:

```bash
WEB_PORT=3001 docker compose up -d web
```

## Current UI

- Dashboard visualisasi: `/`
- Master Data Management: `/master-data`
- Tag Consistency Analysis: `/tag-consistency`
- Phase 2 - Depot Departure Time Intelligence: `/departure-intelligence`
- Phase 3 - SPBU Pairing Probability Intelligence: `/pairing-intelligence`
- Phase 4 - SPBU–MT Historical Affinity & Stability Intelligence: `/affinity-intelligence`
- Phase 5 - Machine Learning Intelligence: `/machine-learning-intelligence`
- Phase 6 - Shipment & MT Assignment Prediction: `/prediction-assignment`

Tema UI saat ini diselaraskan dengan aplikasi `vrp_planner` dan palet Petrofin:

- Petrofin blue `#0b73bf` untuk primary action, chart, dan aksen navigasi.
- Petrofin lime `#b8d211` untuk active navigation highlight dan focus state.
- Petrofin ink `#15385b` untuk teks utama.
- Petrofin red `#ea4a43` untuk error, warning kritikal, dan destructive action.
- Background memakai warm paper gradient dan panel putih dengan shadow halus agar senada dengan tampilan Petrofin planner.

Dashboard saat ini dibuat clean untuk visualisasi:

- filter dashboard by depot
- KPI cards
- Phase 0 charts
- data quality explorer
- placeholder untuk feature analitik lanjutan, tetapi belum menjadi scope aktif Phase 0

Halaman Master Data berisi operasional data-management:

- import file lokal `.xlsx` atau `.csv`
- export template
- export data per depot
- import sample data
- import history
- CRUD master data
- filter CRUD by search, depot, status
- pagination CRUD per `10`, `50`, `100`, atau `All records`
- checkbox select all dan select per row pada tabel CRUD
- sort pada masing-masing kolom tabel CRUD
- dynamic tag-type columns pada Master MT dan Master SPBU
- edit tag MT/SPBU melalui modal edit CRUD
- Sync Depot/Product/Tag dari canonical source yang masih aktif saja
- SPBU coordinate parsing dari format `latitude longitude`, termasuk decimal comma seperti `5,19182389869645 96,4368560343681`

Halaman Phase 2 - Depot Departure Time Intelligence berisi analisa deskriptif historis keberangkatan Mobil Tangki dari depot:

- filter utama Depot, Start Date, End Date, bucket 30/60 menit, dan Apply
- tombol Apply menjalankan departure analysis dan Operational Shift Intelligence secara terpadu
- tidak menjalankan analisa otomatis saat halaman dibuka
- unit observasi adalah `shipment_id + spbu_id`, sehingga beberapa line Loading Order untuk produk berbeda tidak menggandakan observasi SPBU yang sama
- timestamp analitik `departure_datetime_used` memprioritaskan GPS depot-exit yang reliabel bila tersedia, lalu fallback ke `fact_shipment.gate_out_datetime`
- lineage timestamp tetap ditampilkan sebagai LO gate-out, GPS depot exit, timestamp yang dipakai, dan source (`GPS` atau `LO_GATE_OUT`)
- output meliputi KPI, source coverage, 24-hour distribution, weekday heatmap, SPBU box plot, profile table, confidence, Operational Shift Intelligence, dan source-lineage explorer
- date picker menandai tanggal yang memiliki data departure berbeda dari tanggal tanpa data
- SPBU box plot menggunakan circular-time scale agar pola melewati midnight tidak terlihat sebagai outlier 24 jam palsu
- table pagination `10`, `25`, `50`, dan `100` rows tersinkron dengan box plot dan SPBU Shift Affinity Heatmap

Phase 2 bersifat descriptive intelligence. Halaman ini tidak menghitung arrival SPBU, ETA, route sequence, travel time, route optimization, atau rekomendasi jadwal dispatch.

Halaman Phase 3 - SPBU Pairing Probability Intelligence berisi relationship intelligence antar-SPBU:

- filter utama Depot, Date Range `7 Days` / `14 Days` / `30 Days` / `Custom Range`, Product, Search, dan Apply
- tidak menjalankan analisa otomatis saat halaman dibuka
- unit analitik utama adalah `shipment_id`
- pairing berarti dua SPBU berada dalam shipment yang sama: `A - B`
- pair diolah secara canonical unordered, tetapi conditional probability tetap directional
- `P(B|A) = shipment(A and B) / shipment(A)`
- `P(A|B) = shipment(A and B) / shipment(B)`
- `Support(A,B) = shipment(A and B) / total eligible shipment`
- `Lift(A,B) = P(B|A) / P(B)`, dengan zero denominator ditangani sebagai `0`
- confidence adalah evidence confidence, bukan prediction probability
- default confidence rule: `INSUFFICIENT_DATA` jika `min(shipment_a_count, shipment_b_count) < 5` atau `pair_count < 3`; `LOW` jika `pair_count < 10`; `MEDIUM` jika `10 <= pair_count < 30`; `HIGH` jika `pair_count >= 30`
- KPI, Data Quality panel, Top Pairing Explorer, Pairing Matrix, Pairing Network Prototype, SPBU Pairing Detail, dan Historical Evidence drill-down

Product segmentation:

- `All Products` menggunakan canonical `fact_shipment_spbu`
- product-specific analysis menggunakan distinct `shipment_id + spbu_id` dari Loading Order line yang memiliki product terpilih
- shipment multi-produk tetap dihitung sebagai shipment yang sama
- repeated LO/product/compartment lines tidak menggandakan membership atau pair count

GPS transition distinction:

- `A - B` berarti same-shipment pairing
- `A -> B` berarti actual consecutive visit berdasarkan `fact_shipment_stop.stop_sequence`
- shipment dengan sequence `A -> C -> B` menghasilkan pairing `A - C`, `A - B`, `C - B`, tetapi transition hanya `A -> C` dan `C -> B`
- Phase 3 tidak memakai transition sebagai edge thickness pairing dan tidak melakukan route optimization

Phase 3 tetap independen dari Phase 2. Departure profile dapat menjadi konteks di fase berikutnya, tetapi Phase 3 tidak mengubah departure window atau shift assignment Phase 2.

Halaman Phase 4 mengukur historical vehicle-assignment behavior:

- filter Depot, Search SPBU, Date Range, Product, Minimum Observations, Confidence, Temporal Bucket, Recent Period, Top-N, dan network edge metric
- unit observasi unik `depot_id + shipment_id + spbu_id + mt_id`; product filtering dilakukan sebelum deduplikasi final
- `P(MT|SPBU)`, `P(SPBU|MT)`, dominant MT, Top-3 Share, Fleet Affinity Vector, HHI, normalized HHI, dan normalized entropy
- consistency, variability, evidence confidence, dan temporal stability tetap merupakan metric terpisah
- temporal stability memakai consecutive-bucket Jensen–Shannon similarity dan dominant-MT persistence
- output profile, probability chart, time series, recent comparison, scatter, pattern matrix, rankings, reverse MT detail, bipartite network, dan shipment evidence
- hasil hanya mendeskripsikan historical dispatch; tidak menghasilkan future assignment recommendation atau optimization

### Panduan membaca halaman Phase 4

#### Filter dan tombol Apply

Card paling atas menentukan scope analisis. Perubahan filter belum mengubah hasil sampai tombol **Apply** ditekan.

| Filter | Fungsi | Cara membaca atau menggunakan |
|---|---|---|
| Depot | Membatasi shipment, SPBU, dan MT pada satu depot. | Data antardepot tidak dicampur. Depot wajib dipilih. |
| Search SPBU | Memilih SPBU aktif berdasarkan kode atau nama dari saran pencarian. | Field aktif setelah depot dipilih. Pilih hasil yang sesuai lalu tekan **Apply**; profile, probability chart, time series, recent comparison, network highlight, dan evidence akan memakai SPBU tersebut. Jika dikosongkan, sistem memakai SPBU eligible pertama pada scope aktif. |
| Start Date / End Date | Membatasi tanggal operasi shipment. | Teks `Available` menunjukkan rentang data yang benar-benar tersedia untuk depot tersebut. |
| Product | Memilih All Products atau satu produk. | Product filtering dilakukan sebelum deduplikasi. Shipment–SPBU–MT yang muncul pada beberapa LO untuk produk yang sama tetap dihitung satu kali. |
| Minimum Observations | Menentukan minimum historical shipment sebuah SPBU agar masuk hasil. | Naikkan nilai ini untuk mengurangi profile dengan evidence sangat sedikit. |
| Confidence | Menampilkan semua profile, Medium+, atau hanya High. | Filter ini menyaring kekuatan evidence; tidak mengubah Consistency atau Variability Score. |
| Temporal Bucket | Memilih Daily, Weekly, Monthly, atau Auto. | Bucket yang benar-benar digunakan selalu ditampilkan pada Data Quality card sehingga aggregation tidak tersembunyi. |
| Recent Period | Menentukan recent window 7, 14, atau 30 hari. | Dipakai untuk membandingkan recent distribution dengan full-period distribution, tanpa hidden recency weighting. |
| Top MT | Menentukan Top 5, Top 10, atau seluruh MT pada probability chart. | Mengubah jumlah bar yang ditampilkan, bukan denominator probability. |
| Network Edge | Memilih bobot edge berupa Shipment Count atau Affinity Probability. | Pilihan ini mengubah nilai edge network; edge width tetap membantu melihat kekuatan evidence historis. |

Saat halaman pertama dibuka, KPI dan visualisasi sengaja kosong. Empty state tersebut menandakan analysis scope belum diterapkan, bukan menandakan data tidak tersedia.

SPBU aktif ditentukan dengan urutan berikut:

1. SPBU yang dipilih melalui **Search SPBU** saat Apply;
2. SPBU yang diklik dari ranking, scatter plot, pattern matrix, reverse chart, atau network;
3. SPBU eligible pertama dari hasil analisis jika belum ada pilihan eksplisit.

Pilihan dari grafik atau tabel disinkronkan kembali ke field Search SPBU. Search SPBU memilih fokus detail dan tidak membatasi KPI maupun Data Quality Summary; kedua bagian tersebut tetap merangkum seluruh SPBU yang lolos scope dan filter aktif.

Urutan kelompok card utama pada layout saat ini adalah:

1. KPI Summary dan Data Quality Summary;
2. SPBU Consistency Scatter Plot, Historical Pattern Matrix, ranking cards, dan Historical SPBU–MT Bipartite Network;
3. Historical Evidence Drill-Down;
4. SPBU–MT Historical Profile;
5. MT Historical Probability dan Historical MT Affinity Over Time;
6. Recent vs Full-Period Pattern dan MT Reverse Historical Affinity;
7. Methodology & Guardrails.

#### KPI summary cards

Seluruh KPI mengikuti depot, tanggal, produk, minimum observation, dan confidence filter yang sudah di-Apply.

| Card | Arti | Cara membaca |
|---|---|---|
| Eligible Shipments | Jumlah distinct shipment yang mempunyai minimal satu observation SPBU–MT valid. | Bandingkan dengan Source Shipments pada Data Quality card. Selisihnya adalah shipment yang tidak dapat dianalisis. |
| SPBU Analyzed | Jumlah SPBU yang lolos minimum observation dan confidence filter. | Nilai dapat turun saat Minimum Observations dinaikkan atau Confidence diperketat. |
| MT Observed | Jumlah MT unik yang muncul pada relationship milik SPBU yang lolos filter. | Ini historical observed fleet, bukan jumlah MT yang tersedia pada master. |
| Unique SPBU–MT Pairs | Jumlah relationship unik SPBU–MT yang benar-benar pernah terjadi. | Satu pair dapat dibentuk oleh satu atau banyak shipment. Duplicate LO tidak menambah jumlah pair. |
| Avg MT / SPBU | Rata-rata jumlah MT unik yang pernah melayani satu SPBU. | Nilai tinggi menunjukkan fleet footprint rata-rata lebih lebar, tetapi belum otomatis berarti pola tidak konsisten. |
| Median MT / SPBU | Nilai tengah jumlah MT unik per SPBU. | Lebih tahan terhadap SPBU ekstrem daripada average. Bandingkan Average dengan Median untuk melihat pengaruh outlier. |
| High Consistency | Jumlah SPBU dengan Consistency Score minimal 65. | Menunjukkan banyaknya SPBU dengan assignment distribution terkonsentrasi. Tetap periksa Confidence. |
| High Variability | Jumlah SPBU dengan Variability Score minimal 65. | Menunjukkan banyaknya SPBU dengan distribution relatif tersebar pada banyak MT. |
| Low Stability | Jumlah SPBU dengan Temporal Stability di bawah 50. | Menunjukkan banyaknya pola SPBU–MT yang berubah kuat antar-bucket. Ini berbeda dari variability keseluruhan periode. |
| Pattern Shifts | Jumlah SPBU dengan flag selain `STABLE`. | Mencakup `MINOR SHIFT`, `MODERATE SHIFT`, dan `MAJOR SHIFT`; aplikasi tidak menyimpulkan penyebab perubahan. |

Contoh interpretasi gabungan:

```text
High Variability tinggi + Low Stability rendah
→ Banyak MT digunakan, tetapi proporsi penggunaannya relatif konsisten dari waktu ke waktu.

High Consistency tinggi + Pattern Shifts tinggi
→ Overall distribution dapat terlihat terkonsentrasi, tetapi dominant fleet atau distribusinya berubah pada bagian tertentu dari periode.
```

#### SPBU–MT Historical Profile card

Card ini menjelaskan SPBU yang sedang dipilih. SPBU dapat dipilih melalui Search SPBU, ranking, scatter plot, pattern matrix, reverse chart, atau network.

| Sub-card / metric | Arti | Cara membaca |
|---|---|---|
| SPBU | Kode SPBU aktif. | Semua affinity chart, temporal chart, recent comparison, dan evidence mengacu pada SPBU ini. |
| Historical Shipments | Distinct shipment yang melayani SPBU pada scope aktif. | Ini denominator `P(MT|SPBU)`. |
| Operating Days | Jumlah tanggal operasi unik yang mempunyai evidence. | Dua SPBU dengan shipment count sama dapat mempunyai temporal evidence berbeda jika operating days berbeda. |
| Unique MT Used | Jumlah MT berbeda yang pernah melayani SPBU. | Baca bersama probability distribution, consistency, variability, dan confidence; jangan digunakan sendirian. |
| Dominant Historical MT | MT dengan historical shipment count terbesar untuk SPBU. | `Dominant` hanya berarti paling sering terjadi secara historis, bukan MT yang harus digunakan berikutnya. |
| Dominant Probability | Proporsi shipment SPBU yang menggunakan dominant MT. | Contoh 54% berarti 54% shipment SPBU pada scope aktif menggunakan MT tersebut. |
| Top-3 MT Share | Total probability tiga MT teratas. | Nilai tinggi menunjukkan mayoritas assignment terkonsentrasi pada tiga MT utama. |
| Historical Pattern | Label `DEDICATED-LIKE`, `PREFERRED-FLEET`, atau `FLEXIBLE`. | Label bersifat analytical. `DEDICATED-LIKE` bukan status dedicated kontraktual. |

Empat horizontal score tracks dibaca dari 0 di kiri ke 100 di kanan:

- **MT Consistency**: normalized HHI. Nilai tinggi berarti assignment terkonsentrasi pada sedikit MT. Untuk satu MT, nilainya deterministik 100.
- **Historical Variability**: normalized entropy. Nilai tinggi berarti assignment lebih merata atau tersebar. Untuk satu MT, nilainya deterministik 0.
- **Temporal Stability**: persistence pola antar-bucket. Nilai tinggi berarti fleet distribution relatif bertahan dari waktu ke waktu.
- **Evidence Confidence**: kekuatan evidence berdasarkan shipment count, operating days, date coverage, recency, dan temporal coverage. Nilai ini tidak dikalikan dengan score pola.

Badges di bawah score tracks mempunyai arti:

- **Confidence HIGH / MEDIUM / LOW**: tingkat kekuatan evidence.
- **STABLE / MINOR SHIFT / MODERATE SHIFT / MAJOR SHIFT**: besarnya perubahan distribution yang terukur.
- **VERY HIGH CONSISTENCY sampai VERY HIGH VARIABILITY**: klasifikasi user-facing dari concentration score.
- **Dominant persistence**: persentase bucket yang dominant MT-nya sama dengan dominant mode seluruh bucket. Nilai 100% berarti dominant MT tidak berganti.

#### Data Quality Summary card

| Metric | Arti | Cara membaca |
|---|---|---|
| Source Shipments | Seluruh shipment pada depot dan date range sebelum eligibility check. | Menjadi baseline kualitas data. |
| Eligible Shipments | Shipment yang menghasilkan observation valid. | Harus sama dengan KPI Eligible Shipments. |
| Excluded Shipments | Shipment yang tidak menghasilkan observation valid. | Periksa daftar exclusion reasons untuk mengetahui masalah pemetaan atau key wajib. |
| Eligible % | `Eligible Shipments / Source Shipments × 100`. | Nilai tinggi berarti coverage analitik lebih baik. |
| Duplicate Observations Removed | Baris tambahan yang mempunyai key shipment–SPBU–MT sama setelah product filter. | Nilai lebih dari nol adalah bukti deduplikasi berjalan, bukan otomatis data error. |
| Bucket used | Daily, Weekly, atau Monthly yang benar-benar digunakan. | Khusus pilihan Auto, field ini menjelaskan hasil resolusi bucket. |
| Algorithm | Versi formula yang menghasilkan output. | Digunakan untuk reproducibility dan audit. |

Exclusion reason dapat mencakup missing/unmapped MT, unmapped SPBU, invalid analytical keys, atau shipment tanpa observation yang eligible.

### Cara membaca grafik Phase 4

#### 1. MT Historical Probability

Horizontal bar chart ini menampilkan historical MT distribution untuk SPBU aktif.

- Sumbu Y berisi MT, diurutkan dari historical shipment count terbesar.
- Sumbu X berisi `P(MT|SPBU)` dalam persen.
- Bar pertama berwarna lime untuk menandai dominant historical MT; bar lain berwarna biru.
- Panjang bar menunjukkan seberapa sering MT tersebut digunakan relatif terhadap seluruh shipment SPBU.
- Hover menampilkan MT, shipment count, probability, first observed, dan last observed.
- Klik bar untuk memilih MT tersebut, memperbarui reverse detail dan evidence relationship.

Contoh:

```text
T01 = 54%, T02 = 24%, T03 = 11%

Artinya 54% historical shipment SPBU menggunakan T01.
Ini tidak berarti T01 direkomendasikan untuk shipment berikutnya.
```

#### 2. Historical MT Affinity Over Time

Line chart ini menunjukkan perubahan `P(MT|SPBU)` pada setiap temporal bucket.

- Sumbu X adalah period start sesuai Daily, Weekly, atau Monthly bucket.
- Sumbu Y adalah probability MT pada bucket tersebut, dari 0% sampai 100%.
- Setiap garis mewakili satu MT; chart membatasi garis pada MT utama agar tetap terbaca.
- Garis relatif datar menunjukkan probability MT yang stabil.
- Garis turun pada MT lama bersamaan dengan garis naik pada MT lain menunjukkan historical pattern shift.
- Pergantian garis tertinggi menunjukkan perubahan dominant MT.
- Karena hanya beberapa MT utama yang ditampilkan, garis yang terlihat tidak harus berjumlah 100%.

Jangan hanya melihat dominant line. Perubahan material pada seluruh distribution dapat terjadi walaupun dominant MT tetap sama.

#### 3. Recent vs Full-Period Pattern

Card perbandingan ini menampilkan dua ranked distributions:

- **Full selected period**: distribusi sepanjang date range yang dipilih.
- **Recent period**: distribusi hanya pada recent window 7, 14, atau 30 hari.

Cara membaca:

- Bandingkan urutan MT dan persentasenya pada kedua kolom.
- MT yang naik tajam di recent period menunjukkan peningkatan historical usage terbaru.
- MT yang dominan di full period tetapi turun di recent period dapat mengindikasikan pattern shift.
- Klik salah satu MT untuk membuka reverse view dan evidence.

Recent distribution ditampilkan apa adanya. Tidak ada hidden weighting yang memperbesar pengaruh observation terbaru.

#### 4. MT Reverse Historical Affinity

Horizontal bar chart ini membalik orientasi menjadi `MT → SPBU`.

- Header menampilkan MT aktif, historical shipment, unique SPBU, operating days, concentration, temporal stability, dominant-SPBU persistence, dan pattern-shift level.
- Sumbu Y berisi SPBU yang pernah dilayani MT.
- Sumbu X berisi `P(SPBU|MT)`.
- Bar panjang berarti SPBU tersebut muncul pada proporsi besar shipment MT aktif.
- Klik bar SPBU untuk menjadikannya SPBU aktif pada seluruh dashboard.

Satu shipment MT dapat melayani beberapa SPBU. Karena itu total `P(SPBU|MT)` seluruh SPBU dapat melebihi 100%; setiap probability memakai denominator distinct shipment MT, bukan total pair observation.

#### 5. SPBU Consistency Scatter Plot

Setiap titik mewakili satu SPBU.

- Sumbu X: **Unique MT Count**.
- Sumbu Y: **Consistency Score**.
- Ukuran titik: historical shipment count; titik besar mempunyai evidence shipment lebih banyak.
- Warna titik: biru untuk High Confidence, lime untuk Medium, dan merah untuk Low.
- Legend di bagian atas grafik menjelaskan ketiga warna confidence tersebut.

Cara membaca area plot:

- Kiri atas: sedikit MT dan assignment sangat terkonsentrasi.
- Kanan atas: banyak MT pernah digunakan, tetapi sebagian kecil MT tetap mendominasi.
- Kiri bawah: sedikit MT dengan distribution lebih seimbang.
- Kanan bawah: banyak MT dengan distribution tersebar; pola paling flexible.

Hover menampilkan SPBU, shipment count, unique MT, dominant MT dan probability, consistency, variability, stability, serta confidence. Klik titik untuk membuka SPBU profile dan popup persisten berisi nama, kode, serta confidence SPBU. Titik terpilih diberi border gelap; popup dapat ditutup dengan tombol `×`.

#### 6. Historical Pattern Matrix

Matrix ini memberi operational overview berdasarkan dua dimensi:

- Sumbu X: Unique MT Count.
- Sumbu Y: Dominant MT Affinity.
- Garis horizontal putus-putus: dominant affinity 60%.
- Garis vertikal putus-putus: median Unique MT Count dari SPBU yang lolos filter.

Empat quadrant dibaca sebagai berikut:

| Posisi | Label | Interpretasi historis |
|---|---|---|
| Kiri atas | DEDICATED-LIKE | Sedikit MT dan satu MT sangat dominan. |
| Kanan atas | PREFERRED-FLEET | Banyak MT pernah digunakan, tetapi dominant MT tetap kuat. |
| Kiri bawah | LIMITED BALANCED | Jumlah MT sedikit dan usage relatif terbagi. |
| Kanan bawah | HIGHLY FLEXIBLE | Banyak MT dan dominant affinity rendah. |

Warna titik mengikuti quadrant. Hover menampilkan SPBU, quadrant, unique MT, dominant affinity, dan shipment count. Klik titik untuk memilih SPBU.

Matrix adalah ringkasan dua dimensi. Historical Pattern pada profile juga mempertimbangkan consistency dan Top-3 share, sehingga label detail dapat memberi konteks tambahan.

#### 7. Ranking cards

Tiga ranking bukan recommendation list; semuanya ranking descriptive historical behavior.

- **Most Historically Consistent SPBU** diurutkan berdasarkan Consistency Score. Kolom Unique MT dan Top-3 Share membantu membedakan concentration dengan ukuran fleet.
- **Most Historically Variable SPBU** diurutkan berdasarkan Variability Score. Kolom Temporal Stability menunjukkan apakah variasi fleet tersebut tetap konsisten atau berubah antarperiode.
- **Highest Historical Pattern Change** diurutkan dari Temporal Stability terendah. Pattern Shift serta Previous MT dan Recent MT menunjukkan arah perubahan dominant pattern.

Klik nama/header kolom untuk mengubah sorting, gunakan Filter SPBU untuk menyaring ranking, dan klik row untuk membuka SPBU profile terkait. Selalu baca ranking bersama Shipment dan Confidence agar profile dengan evidence kecil tidak disamakan dengan profile ber-evidence kuat.

#### 8. Historical SPBU–MT Bipartite Network

Network menghubungkan dua jenis node:

- Node biru: SPBU.
- Node lime: MT.
- Border merah: node yang sedang dipilih.
- Edge: relationship historis SPBU–MT.
- Edge yang terkait node aktif dibuat lebih tebal dan lebih jelas.

Cara membaca:

- SPBU dengan banyak edge pernah dilayani oleh banyak MT.
- MT dengan banyak edge mempunyai historical service footprint ke banyak SPBU.
- Ketebalan edge menunjukkan historical shipment evidence. Pilihan Network Edge mengubah analytical edge value menjadi Shipment Count atau Affinity Probability, sedangkan tooltip tetap menampilkan kedua probability agar arah relationship dapat dibandingkan.
- Hover edge menampilkan shipment count, `P(MT|SPBU)`, `P(SPBU|MT)`, first/last observed, operating days, dan confidence.
- Klik node SPBU untuk highlight seluruh MT yang pernah melayaninya.
- Klik node MT untuk membuka reverse service footprint MT tersebut.

Network dibatasi pada relationship teratas agar tetap usable. Gunakan probability chart, reverse chart, dan evidence table untuk audit angka detail.

#### 9. Historical Evidence Drill-Down

Card ini adalah audit table untuk relationship SPBU–MT aktif. Setiap row adalah shipment pembentuk relationship dan menampilkan:

- Date dan Shipment ID;
- Depot dan Gate Out;
- MT dan SPBU;
- Products dan Quantity;
- Other SPBU dalam shipment yang sama.

Jumlah `distinct shipments` pada header harus sama dengan shipment count relationship aktif. Jika satu shipment mempunyai beberapa LO atau produk, detail dapat memuat beberapa product tetapi relationship tetap dihitung satu observation.

#### 10. Methodology & Guardrails

Card terakhir merangkum formula aktif untuk Consistency, Variability, Temporal Stability, dan Confidence beserta algorithm version. Gunakan card ini untuk memastikan interpretasi score konsisten saat membandingkan export, API response, atau periode analisis berbeda.

Aturan interpretasi utama Phase 4:

```text
Affinity menjawab: MT mana yang historically melayani SPBU dan seberapa sering?
Stability menjawab: apakah distribution tersebut bertahan dari waktu ke waktu?
Confidence menjawab: seberapa kuat evidence yang mendukung kedua pembacaan tersebut?
```

Ketiga konsep tersebut tidak boleh dicampur. Historical affinity tinggi tidak membuktikan future suitability, dan confidence tinggi tidak menjadikan pola sebagai rekomendasi assignment.

Operational Shift Intelligence di Phase 2:

- konfigurasi shift operasional per depot melalui Add/Remove Shift, start/end time, Save, dan Load
- validasi konfigurasi shift: format waktu valid, nama tidak duplikat, tidak overlap, dan menutup 24 jam penuh
- tiga metode assignment: Dominant Shift, Median-Based, dan Hybrid / Confidence-Aware
- Operational Shift Summary interaktif: klik shift/status untuk memfilter tabel SPBU Departure Profiles
- Confidence Mix interaktif: klik HIGH/MEDIUM/LOW untuk memfilter tabel profile
- tabel SPBU Departure Profiles menggabungkan profile departure dan shift assignment dalam satu tabel paginated
- SPBU Shift Affinity Heatmap menampilkan SPBU current page yang sama dengan tabel dan box plot
- warna heatmap memakai skala kontras berdasarkan `Shift Affinity %`, bukan jumlah observasi
- box plot memiliki legend dinamis untuk pilihan highlight Primary Historical Shift, Assignment Status, atau Confidence

Operational Shift Intelligence tetap berbasis perilaku historis. Output ini tidak memaksa jadwal dispatch masa depan dan tidak melakukan multi-feature SPBU clustering; advanced clustering tetap menjadi tanggung jawab fase lanjutan.

Catatan scope saat ini: fondasi Phase 0 tetap menjadi dasar data utama, dan Phase 2 departure intelligence sudah tersedia sebagai read-only derived analysis. Analisa di luar scope seperti SPBU arrival, ETA, route intelligence, route optimization, dan recommendation workflow belum dikerjakan sebagai fitur aktif.

Load sample data:

```bash
curl -X POST http://localhost:8000/api/v1/imports/sample
```

## Local Tests

```bash
cd apps/api
python -m pip install -r requirements.txt
pytest
```

Jika menggunakan container:

```bash
docker compose exec -T api pytest
```

## Architecture Status

Implemented stack:

- PostgreSQL/PostGIS
- FastAPI
- SQLAlchemy 2.x
- Alembic
- React/Vite
- TypeScript
- Tailwind CSS
- Apache ECharts
- Docker Compose

Monorepo layout:

- `apps/api`: backend API, importer, models, tests
- `apps/web`: frontend dashboard dan master-data UI
- `db/migrations`: Alembic migrations
- `docs`: architecture, data model, phase status, source mapping, future integrations
- `example data`: source workbook Phase 0

Phase 2 departure intelligence details are documented in `docs/PHASE_2_DEPOT_DEPARTURE_INTELLIGENCE.md`.
Phase 3 pairing intelligence implementation is summarized in `PHASE_3_COMPLETION_REPORT.md`.
Phase 4 methodology is documented in `docs/PHASE_4_SPBU_MT_AFFINITY.md`, with implementation status in `PHASE_4_COMPLETION_REPORT.md`.

## Phase Roadmap

### Phase 0: Master Data Strengthening and Operational Data Foundation

Tujuan:

- membangun canonical master data dan operational fact foundation
- memisahkan source instruction dari observed operational evidence
- membuat import pipeline yang auditable
- membuat data-quality issue tracking
- membuat CRUD dan export/import foundation untuk data master dan operational data
- menyiapkan struktur data yang nanti dapat dipakai oleh fase analitik, tanpa menjalankan analisa relasi sebagai fitur aktif

Scope data:

- Mobil Tangki
- SPBU
- Depot
- Product
- Tags dan tag aliases
- Loading Order
- Shipment
- Shipment-SPBU membership sebagai data foundation dari source Loading Order, bukan analisa relasi
- GPS staging foundation
- SPBU visit model foundation
- actual stop sequence foundation
- data quality
- master compatibility placeholder untuk fase lanjutan

Status saat ini: `IN PROGRESS`.

Progress yang sudah dibuat:

- Docker Compose untuk PostGIS, FastAPI, dan web frontend.
- Alembic schema untuk staging, canonical masters, loading-order facts, shipment facts, GPS foundation, geofence, visits, stop sequence, dan data-quality tables.
- Import sample untuk:
  - `master data MT.xlsx`: 162 rows
  - `master data spbu.xlsx`: 583 rows
  - `masterdata_LO.xlsx`: 4,462 rows, 1,876 grouped shipments
- Upload import file lokal `.xlsx` atau `.csv`.
- Export template.
- Export data per depot.
- Import audit dan import history.
- RAW -> STAGING -> VALIDATION -> NORMALIZATION -> PUBLISH -> CANONICAL flow.
- Canonical master MT dengan registration normalization.
- Canonical master SPBU dengan coordinate parsing dari `source_coordinate`; format `lat long` dengan decimal comma diparse ke kolom `latitude` dan `longitude`.
- Canonical depot dari source alias.
- Product bootstrap dari Loading Order.
- Tag splitting, tag master, tag type, MT tag bridge, SPBU tag bridge.
- Tag Vehicle Class pada MT/SPBU disimpan sebagai integer dan ditampilkan sebagai kolom `TAG VEHICLE CLASS`.
- Loading Order grouping menjadi shipment header dan loading-order lines.
- Kombinasi `loading_order_number` + nama depot `tbbm`/`source_depot_name` menjadi primary key canonical untuk `fact_loading_order_line`; `loading_order_number` boleh sama di depot berbeda, sedangkan source `shipment_id` boleh duplikat karena satu shipment bisa multi loading order, multi SPBU, dan multi kompartemen dalam MT yang sama.
- Shipment-SPBU membership.
- Driver/kernet preservation.
- Mapping status untuk MT/SPBU masih bersifat internal import diagnostic dan tidak ditampilkan sebagai analisa relasi pada CRUD Phase 0.
- Data-quality issues persisted.
- Compatibility/reconciliation endpoint masih placeholder atau foundation, belum menjadi fitur analitik aktif.
- Dashboard filter by depot.
- Phase 0 KPI cards dan chart categories.
- Master Data CRUD untuk MT, SPBU, Loading Order, Depot, Product, Tag, dan Tag Type.
- CRUD supports add, view, update, soft delete, column search, sort, status filter, depot filter, pagination, select all, select per row, dan multi-row add/edit/delete.
- Deleted master data disembunyikan dari list aktif dan unique key dapat dipakai kembali setelah soft-delete karena create path mereaktivasi record yang sudah deleted.
- Sync master Depot/Product/Tag hanya membaca source aktif: active MT, active SPBU, dan active Loading Order. Data yang sudah `DELETED` tidak dipakai sebagai candidate sync.
- Sync dapat mereaktivasi master Depot/Product/Tag yang deleted bila nilai tersebut masih direferensikan oleh source aktif.
- Frontend theme sudah diselaraskan dengan `vrp_planner` menggunakan palet Petrofin untuk background, header, navigation, panel, focus state, dan chart.
- Phase 6 inference, global MT assignment, audit/override/history/export API dan UI tersedia sebagai extension modular.

Progress validasi:

- Container stack healthy.
- Relevant API/import/normalization tests passing: `18 passed`.
- Frontend build passing.
- Browser smoke tests passing untuk dashboard, master-data page, CRUD pagination, select all, dan All records.
- API container rebuilt dan healthy setelah update sync dan SPBU coordinate backfill.
- Web container rebuilt dan healthy setelah update tema Petrofin dan kolom coordinate SPBU.

Belum selesai:

- Real GPS mapping menunggu file/source schema `GPS_data`.
- GPS visit detection belum tervalidasi dengan GPS real atau synthetic acceptance fixture.
- LO vs GPS reconciliation belum real.
- Actual stop sequence masih placeholder karena belum ada GPS evidence.
- Phase 0 belum boleh ditutup sampai GPS staging/visit/reconstruction divalidasi.

Ready for Phase 1: `NO`.

### Phase 1: Historical Tag Intelligence

Tujuan:

- membandingkan master compatibility dengan actual historical MT-SPBU relationship
- mendeteksi anomaly ketika master data tidak sesuai historical evidence
- menghasilkan recommendation workflow tanpa otomatis mengubah master data

Target output:

- `fact_mt_spbu_observation`
- anomaly classification: green/yellow/red-review
- tag change recommendation workflow
- Historical Tag Intelligence Dashboard
- drill-down evidence dari Loading Order dan GPS-confirmed shipment activity

Status saat ini: `NOT STARTED`.

Gating:

- menunggu Phase 0 selesai dan GPS/operational foundation usable.

### Phase 2: Depot Departure Time Intelligence

Tujuan:

- membangun historical Depot Departure Profile untuk setiap SPBU berdasarkan waktu keberangkatan Mobil Tangki dari depot
- menjaga source lineage antara LO gate-out dan GPS actual depot exit
- menghitung pola historis keberangkatan secara circular-time aware, termasuk distribusi yang melewati midnight

Target output:

- departure distribution per SPBU
- P20, P25/Q1, P50, P75/Q3, P80, P90, P95
- peak departure bucket dengan midpoint sebagai `peak_departure_time`
- Preferred Historical Departure Window berbasis P20-P80 circular-time
- weekday/weekend segmentation
- product/depot/vehicle type segmentation jika berguna
- source coverage dan GPS-vs-LO difference sebagai metrik kualitas data
- SPBU Departure Profile Explorer

Status saat ini: `IMPLEMENTED AS READ-ONLY DERIVED API/UI`.

Progress yang sudah dibuat:

- API `GET /api/v1/departure-intelligence/analysis` dengan pagination, sorting, confidence filter, dan filter daftar SPBU.
- API `GET /api/v1/departure-intelligence/available-dates` untuk date availability.
- API `POST /api/v1/departure-intelligence/shift-analysis` untuk Operational Shift Intelligence.
- Circular-time percentile profile: P20, P25/Q1, P50, P75/Q3, P80, P90, P95, IQR, outlier count, dan Preferred Historical Departure Window.
- Box plot current table page dengan optional highlight by Primary Historical Shift, Assignment Status, atau Confidence.
- Operational shift configuration UI dengan validation, save/load per depot, dan help popup.
- Operational Shift Summary, Shift Affinity Heatmap, dan shift assignment fields digabung ke SPBU Departure Profiles.
- Interactive filter dari Confidence Mix dan Operational Shift Summary ke tabel, box plot, dan heatmap.
- Source Lineage explorer dengan sample observasi per visible SPBU profile.

Gating:

- menggunakan canonical shipment foundation dari Phase 0; GPS depot-exit akan dipakai bila `fact_gps_event.event_type` tersedia sebagai depot-exit event reliabel.

### Phase 3: SPBU Pairing and Directed Edge Intelligence

Tujuan:

- menganalisis SPBU yang sering berada dalam shipment yang sama
- menganalisis actual directed transition dari GPS-confirmed stop sequence
- menghitung actual inter-SPBU travel time

Target output:

- `fact_spbu_pair`
- support, confidence, lift, P(B|A), P(A|B)
- `fact_spbu_edge`
- average/median/P80/P90/P95 travel time
- time-dependent edge profile
- Pairing Intelligence Dashboard
- network prototype

Status saat ini: `NOT STARTED`.

Gating:

- membutuhkan shipment membership dan GPS-confirmed sequence.

### Phase 4: SPBU–MT Historical Affinity & Stability Intelligence

Tujuan:

- mengukur historical affinity `P(MT|SPBU)` dan reverse footprint `P(SPBU|MT)`
- mengukur concentration, variability, evidence confidence, dan temporal stability secara terpisah
- mendeteksi historical distribution shift tanpa memberi causal explanation
- menyediakan Fleet Affinity Vector untuk feature Phase 5

Target output:

- `fact_spbu_mt_pair`
- `fact_spbu_mt_profile`
- `fact_spbu_mt_temporal_profile`
- API `/api/v1/affinity-intelligence/analysis`
- UI `/affinity-intelligence`
- algorithm `spbu_mt_affinity.jsd_v1`

Status saat ini: `COMPLETE`.

Gating:

- acceptance tests, UI build, documentation, dan visual validation harus tetap lulus sebelum Phase 5.

### Phase 5 — Machine Learning Intelligence

Status saat ini: `IMPLEMENTED`.

Tujuan Phase 5 adalah historical pattern discovery dan reusable behavioral clustering. Phase ini tidak melakukan route optimization, tidak merekomendasikan MT untuk assignment berikutnya, tidak menilai dispatcher salah, dan tidak mengubah master data.

Readiness gate:

- scope selalu satu `depot_id`; data antardepot tidak pernah dicampur
- seluruh active MT × active SPBU master assignment space pada depot dievaluasi oleh rule source yang sama dengan API compatibility, yaitu `app.compatibility.evaluate_compatibility_entities`
- execution hanya diizinkan jika `master_compatibility_pass_percentage == 100.00` dan numerator benar-benar sama dengan denominator
- gate tidak dapat dibypass melalui UI maupun API

Engine A — Historical MT–SPBU Concentration Anomaly:

- memakai istilah **Baseline Period**, bukan training period
- tidak memakai train/test split
- memakai canonical observation unik `depot_id + shipment_id + spbu_id + mt_id` dari Phase 4; repeated LO, product, atau compartment line tidak menambah count
- menghitung compatible MT count, historically used compatible MT count, utilization breadth, dominant MT/share, HHI, Shannon entropy, normalized entropy, dan shipment observation count
- SPBU di bawah minimum observation disimpan sebagai `INSUFFICIENT_DATA` dan tidak ikut model scoring
- fitur cukup-data distandardisasi lalu dianalisis dengan Isolation Forest
- raw severity adalah negatif `IsolationForest.score_samples`; 0–100 score memakai deterministic within-run min-max transform. Run dengan semua raw score sama mendapat score 0 karena model tidak menemukan relative anomaly evidence
- peer statistics memakai deterministic log2 band dari compatible fleet count
- hasil tersimpan pada `ml_concentration_analysis_run` dan `ml_spbu_concentration_profile`, dapat dibuka kembali tanpa recompute
- Engine A mendeteksi unusual historical concentration, bukan future assignment deviation

Engine B — SPBU Behavioral Clustering:

- memakai workflow `Prepare Dataset → Validate → Configure → Train → Review → Save`
- tag features mempertahankan tag-type boundary melalui typed multi-hot encoding; Vehicle Class disimpan sebagai feature ordinal
- shift feature memakai seluruh historical shift distribution dan menyimpan exact shift-definition snapshot pada training run/model
- Phase 3 same-shipment relationship dibentuk sebagai weighted graph; edge weight adalah mean dari dua directional conditional probabilities
- pairing graph diubah menjadi embedding dengan Node2Vec memakai seed dan single worker; isolated nodes menerima zero pairing vector, dan graph tanpa edge menghasilkan zero vector untuk seluruh node
- feature group Tag, Shift, Pairing distandardisasi sendiri-sendiri, dikalikan `sqrt(weight / group_dimension)`, lalu digabung agar group lebar tidak dominan hanya karena memiliki lebih banyak kolom
- pipeline utama adalah **Node2Vec + UMAP + HDBSCAN**. HDBSCAN noise tetap noise dan ditampilkan sebagai `Noise / Unique Behavioral Pattern`
- training result harus direview sebelum diberi nama dan disimpan; training tidak otomatis membuat registry model atau mengaktifkannya
- saved package berisi encoder/configuration, scaler metadata, Node2Vec embeddings, internal/visualization UMAP model, HDBSCAN model, vectors, assignments, profiles, dependency versions, manifest, dan checksum
- binary package berada pada persistent `ML_ARTIFACT_DIR`; relational table hanya menyimpan artifact metadata/path/checksum

Model lifecycle:

- status: `SAVED`, `ACTIVE`, `ARCHIVED`
- hanya satu model `ACTIVE` per depot; aktivasi menurunkan model aktif lama menjadi `SAVED`
- retraining tidak overwrite version lama; nama yang sama pada depot yang sama menghasilkan `v1`, `v2`, dan seterusnya
- Duplicate hanya menyalin configuration ke training draft baru, bukan trained artifact
- Compare mengabaikan arbitrary HDBSCAN cluster IDs. Cluster antar-model dipasangkan secara optimal berdasarkan Jaccard similarity membership set menggunakan Hungarian assignment
- active-model API menyediakan version, period, assignments, membership probability, dan cluster profiles untuk phase berikutnya

Local ML setup di luar Docker:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r apps/api/requirements.txt
export PYTHONPATH="$PWD/apps/api"
export ML_ARTIFACT_DIR="$PWD/ml_artifacts"
```

Dependency penting dipin pada `apps/api/requirements.txt`: NumPy, SciPy, scikit-learn (termasuk maintained HDBSCAN implementation), umap-learn, NetworkX, node2vec, gensim, dan joblib. Docker Compose membuat named volume `ml_artifacts` sehingga package model tetap ada setelah API container direstart.

Detail teknis lengkap: `docs/PHASE_5_MACHINE_LEARNING_INTELLIGENCE.md`.

### Phase 6 — Shipment & MT Assignment Prediction

Status saat ini: `IMPLEMENTED`.

Phase 6 adalah inference, bukan training dan bukan route optimization. Satu run menggabungkan saved Phase 5 model, Loading Order aktual, dan daftar MT available untuk menghasilkan struktur `Shift → Predicted Shipment → Loading Order/SPBU → Assigned MT` yang auditable dan dapat menjadi warm start Phase 7.

Input workbook:

- Loading Order: `loading_order_no`, `shift_gate_out`, `spbu_no`
- MT Availability: `shift`, `vehicle_registration_no`
- template dapat diunduh dari page atau API; `.xlsx` dibatasi 10 MB
- shift divalidasi terhadap exact shift-definition snapshot model, bukan daftar Shift 1–4 yang di-hardcode
- SPBU/MT harus ada di canonical master dan compatible dengan depot run

Prediction flow:

```text
Phase 5 Saved Model
        +
Loading Order
        +
MT Availability
        ↓
Shipment Prediction per Shift
        ↓
Phase 4 MT Candidate Score
        ↓
Phase 1 Master Compatibility Hard Filter
        ↓
Global Maximum-Weight Assignment
        ↓
Predicted Dispatch Structure
        ↓
Phase 7 Warm Start
```

Shipment inference memakai immutable Phase 5 artifact/normalized model registry: cluster membership probability, same-cluster evidence, model feature weights, dominant-shift match, dan saved historical pairing strength. Score adalah normalized combination dari metric yang benar-benar ada. Pairing di bawah configurable `minimum_prediction_confidence` tetap menjadi valid single-SPBU shipment; LOW confidence ditampilkan dan tidak otomatis dihapus kecuali optional blocking threshold disetel backend.

MT ranking memakai Phase 4 historical `P(MT|SPBU)` dengan deterministic Laplace smoothing. Historical affinity hanya score/ranking; rule Phase 1 dari `app.compatibility.evaluate_compatibility_entities` tetap hard filter terpisah. Untuk multi-SPBU shipment, MT harus lulus rule untuk seluruh SPBU (intersection). Candidate yang gagal disimpan sebagai diagnostic `MASTER_COMPATIBILITY_FAIL` tetapi tidak masuk optimizer.

Assignment memakai exact global maximum-weight bipartite matching per shift melalui NetworkX. Satu MT hanya dapat dipakai satu shipment dalam shift dan satu shipment maksimal mendapat satu MT. Run tidak memaksa assignment: `NO_AVAILABLE_MT`, `NO_COMPATIBLE_MT`, `ALL_COMPATIBLE_MT_ALLOCATED`, dan `LOW_CONFIDENCE` disimpan sebagai reason code.

Dispatcher dapat:

- mengganti MT hanya ke candidate yang available dan compatible
- move LO/SPBU ke shipment same-shift, membuat shipment baru/single, atau combine same-shift shipment
- memicu ulang candidate scoring, compatibility filter, dan global assignment untuk shift terkait tanpa training ulang
- membandingkan immutable model prediction dengan final dispatch plan melalui original snapshot dan `model_predicted_shipment_id`

Persistence migration `0010_phase6_prediction` menambah `prediction_run`, `prediction_shipment`, `prediction_shipment_line`, `prediction_mt_candidate`, dan `prediction_assignment`. Run menyimpan normalized input, validation, model/configuration/algorithm snapshots, duration metrics, original model result, user, timestamps, candidates, exclusions, optimizer result, dan override audit.

History menyediakan View, Download, dan Duplicate/Re-run. Export `.xlsx` berisi sheets Summary, Shipment Result, MT Assignment, MT Candidates, dan Validation. UI juga menyediakan shipment network (bukan route map), MT assignment matrix, alternative candidate ranking, excluded diagnostics, structured explanation, dynamic shift tabs, dan per-shift KPI.

Authorization mengikuti seam existing melalui `X-User` dan `X-Permissions`: `phase6:view`, `phase6:run`, `phase6:export`, `phase6:override`. Local requests tetap permissive sampai identity provider production menggantikan dependency ini.

Phase 6 secara eksplisit tidak menghitung route sequence, distance, travel time, VRP, cost, driver hours, congestion, atau multi-trip feasibility. Semua tanggung jawab tersebut tetap Phase 7.

Verification terakhir:

- migration PostgreSQL berhasil sampai revision `0010_phase6_prediction`
- seluruh **44 backend tests** lulus pada deployment image
- **6 focused Phase 6 tests** lulus pada local test runtime
- TypeScript type checking dan Vite production build lulus
- API health, 12 Phase 6 route contracts, template `.xlsx`, dan lima tabel persistence telah di-smoke-test
- page `/prediction-assignment` telah diverifikasi melalui browser tanpa console error atau warning
- Vite masih memberi non-blocking warning untuk application chunk sekitar 1.52 MB; code splitting ECharts/page modules menjadi technical debt performance
- host Python tanpa dependency ML lengkap tidak dapat menjalankan dua training tests Phase 5; deployment image adalah verification environment canonical dan membutuhkan `NUMBA_CPU_NAME=generic` serta `NUMBA_DISABLE_JIT=1` pada ARM untuk menghindari illegal-instruction dari Numba/UMAP

## Phase Quality Gate

Setiap phase harus melewati gate berikut sebelum phase berikutnya dimulai:

1. migration berhasil
2. import/seed berhasil
3. analytics jobs berhasil
4. unit tests pass
5. API tests pass
6. integration tests pass
7. Docker stack healthy
8. API health verified
9. frontend screen dibuka
10. visual validation pass
11. acceptance scenarios pass
12. blocking issues fixed
13. documentation updated
14. phase completion report dibuat

## Current API Highlights

- `GET /api/v1/health`
- `POST /api/v1/imports/sample`
- `POST /api/v1/imports?domain=...&sheet_name=...`
- `GET /api/v1/imports`
- `GET /api/v1/imports/{id}`
- `GET /api/v1/exports/template?domain=...&file_format=xlsx|csv`
- `GET /api/v1/exports/data?domain=...&depot_id=...&file_format=xlsx|csv`
- `GET /api/v1/foundation/overview?depot_id=...`
- `GET /api/v1/foundation/charts?depot_id=...`
- `GET /api/v1/master-crud/{domain}?limit=...&offset=...&search=...&depot_id=...&active_status=...`
- `POST /api/v1/master-crud/{domain}`
- `PUT /api/v1/master-crud/{domain}/{record_id}`
- `DELETE /api/v1/master-crud/{domain}/{record_id}`
- `POST /api/v1/master/compatibility/check`
- `GET /api/v1/master/compatibility/summary?depot_id=...`
- `GET /api/v1/data-quality/issues?depot_id=...`
- `GET /api/v1/tag-intelligence/anomalies`
- `GET /api/v1/tag-intelligence/recommendations`
- `GET /api/v1/network/nodes`
- `GET /api/v1/network/edges`
- `GET /api/v1/affinity-intelligence/analysis?depot_id=...&start_date=...&end_date=...`
- `GET /api/v1/affinity-intelligence/available-dates?depot_id=...`
- `GET /api/v1/phase5/readiness?depot_id=...`
- `POST /api/v1/phase5/engine-a/analyze`
- `GET /api/v1/phase5/engine-a/runs`
- `GET /api/v1/phase5/engine-a/runs/{run_id}`
- `POST /api/v1/phase5/engine-b/prepare-dataset`
- `POST /api/v1/phase5/engine-b/training-runs/{run_id}/train`
- `POST /api/v1/phase5/engine-b/training-runs/{run_id}/save`
- `GET /api/v1/phase5/models`
- `GET /api/v1/phase5/models/active?depot_id=...`
- `POST /api/v1/phase5/models/compare`
- `GET /api/v1/phase6/models?depot_id=...`
- `GET /api/v1/phase6/templates/loading-order`
- `GET /api/v1/phase6/templates/mt-availability`
- `POST /api/v1/phase6/validate/loading-order`
- `POST /api/v1/phase6/validate/mt-availability`
- `POST /api/v1/phase6/predictions`
- `GET /api/v1/phase6/predictions`
- `GET /api/v1/phase6/predictions/{run_id}`
- `POST /api/v1/phase6/predictions/{run_id}/recalculate`
- `PATCH /api/v1/phase6/predictions/{run_id}/shipments/{shipment_id}`
- `PATCH /api/v1/phase6/predictions/{run_id}/assignments/{assignment_id}`
- `GET /api/v1/phase6/predictions/{run_id}/export`

Route optimization / VRP endpoints belum diimplementasikan dan tetap menjadi scope Phase 7.

## Important Design Principles

- Jangan overwrite master data secara diam-diam dari historical evidence.
- Pisahkan official master rule, operational instruction, observed behavior, analytical finding, recommendation, dan approved master-data change.
- Semua canonical dan derived records harus menjaga lineage jika memungkinkan.
- Jangan menggunakan LLM untuk deterministic analytics.
- Jangan implement route optimization sebelum Phase 6 selesai.
- Jangan tampilkan uncertainty sebagai fakta pasti; gunakan status seperti `UNKNOWN`, `UNMAPPED`, `AMBIGUOUS`, `LOW CONFIDENCE`, `PARTIAL`, atau `INSUFFICIENT DATA`.

## Documentation

Dokumen pendukung:

- `docs/ARCHITECTURE.md`
- `docs/DATA_MODEL.md`
- `docs/SOURCE_DATA_MAPPING.md`
- `docs/IMPORT_PROCESS.md`
- `docs/MASTER_DATA.md`
- `docs/TAG_MODEL.md`
- `docs/TAG_COMPATIBILITY.md`
- `docs/DATA_QUALITY.md`
- `docs/SHIPMENT_MODEL.md`
- `docs/GPS_MODEL.md`
- `docs/PHASES.md`
- `docs/PHASE_0_STATUS.md`
- `docs/PHASE_4_SPBU_MT_AFFINITY.md`
- `docs/PHASE_5_MACHINE_LEARNING_INTELLIGENCE.md`
- `docs/FUTURE_VRP_INTEGRATION.md`
- `docs/FUTURE_AI_ASSISTANT.md`
