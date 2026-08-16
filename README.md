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

Halaman Phase 2 - Depot Departure Time Intelligence berisi analisa deskriptif historis keberangkatan Mobil Tangki dari depot:

- filter utama Depot, Start Date, End Date, bucket 30/60 menit, dan Apply
- tidak menjalankan analisa otomatis saat halaman dibuka
- unit observasi adalah `shipment_id + spbu_id`, sehingga beberapa line Loading Order untuk produk berbeda tidak menggandakan observasi SPBU yang sama
- timestamp analitik `departure_datetime_used` memprioritaskan GPS depot-exit yang reliabel bila tersedia, lalu fallback ke `fact_shipment.gate_out_datetime`
- lineage timestamp tetap ditampilkan sebagai LO gate-out, GPS depot exit, timestamp yang dipakai, dan source (`GPS` atau `LO_GATE_OUT`)
- output meliputi KPI, source coverage, 24-hour distribution, weekday heatmap, SPBU box plot, profile table, confidence, dan source-lineage explorer

Phase 2 bersifat descriptive intelligence. Halaman ini tidak menghitung arrival SPBU, ETA, route sequence, travel time, route optimization, atau rekomendasi jadwal dispatch.

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
- Canonical master SPBU dengan coordinate parsing.
- Canonical depot dari source alias.
- Product bootstrap dari Loading Order.
- Tag splitting, tag master, tag type, MT tag bridge, SPBU tag bridge.
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
- CRUD supports add, view, update, soft delete, search, status filter, depot filter, pagination, select all, select per row.
- Frontend theme sudah diselaraskan dengan `vrp_planner` menggunakan palet Petrofin untuk background, header, navigation, panel, focus state, dan chart.
- Phase gating placeholder untuk Phase 1 dan Phase 6 endpoints.

Progress validasi:

- Container stack healthy.
- API tests passing: `16 passed`.
- Frontend build passing.
- Browser smoke tests passing untuk dashboard, master-data page, CRUD pagination, select all, dan All records.
- Web container rebuilt dan healthy setelah update tema Petrofin.

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

### Phase 4: Shipment-Set and Route-Pattern Intelligence

Tujuan:

- menemukan recurring shipment set pattern
- menemukan ordered route pattern dari GPS-confirmed sequence
- membedakan `{A,B,C}` sebagai shipment set dari `Depot -> A -> C -> B -> Depot` sebagai route sequence

Target output:

- `fact_shipment_set_pattern`
- `fact_route_pattern`
- `fact_route_pattern_stop`
- route occurrence count
- share of comparable trips
- typical gate-out time
- median duration
- Route Pattern Explorer

Status saat ini: `NOT STARTED`.

Gating:

- membutuhkan Phase 3 pair/edge intelligence dan GPS-confirmed route sequence.

### Phase 5: Operational Cluster Intelligence

Tujuan:

- membangun cluster SPBU operasional yang explainable
- tidak hanya berdasarkan geografi, tetapi juga hard constraints dan historical operation signals

Target signals:

- depot
- vehicle/product restrictions
- tag similarity
- geography
- master travel time
- actual travel time
- same-shipment pair strength
- directed transition strength
- route-pattern similarity
- time-window similarity

Target output:

- `dim_operational_cluster`
- `bridge_cluster_spbu`
- `fact_cluster_profile`
- Cluster Explorer
- cluster map/network
- strongest internal pairs
- bridge SPBU
- inter-cluster connections

Status saat ini: `NOT STARTED`.

Gating:

- membutuhkan Phase 2, Phase 3, dan Phase 4 analytics.

### Phase 6: Interactive Network Intelligence Explorer

Tujuan:

- membangun aplikasi analitik network utama
- node = SPBU
- edges = co-shipment relationship dan actual directed transition
- memberikan side panel intelligence ketika SPBU atau edge diklik

Target frontend:

- Cytoscape.js atau graph library yang setara
- Graph View
- Map View
- synchronized filters
- ego network
- node click interaction
- edge click interaction
- cluster highlighting
- SPBU intelligence side panel

Target node insight:

- SPBU code
- depot
- tags
- vehicle type
- cluster
- shipment count
- GPS visit count
- compatible MT count
- historically observed MT count
- official/observed/preferred time window
- top paired SPBU
- incoming/outgoing edges
- route-pattern participation
- data-quality status

Status saat ini: `NOT STARTED`.

Gating:

- membutuhkan seluruh output Phase 1-5.

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

Phase 1 dan Phase 6 endpoints masih gated dan mengembalikan status `NOT_STARTED`.

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
- `docs/FUTURE_VRP_INTEGRATION.md`
- `docs/FUTURE_AI_ASSISTANT.md`
