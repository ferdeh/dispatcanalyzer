# Phase 10 — AMT Scheduler Connector

Phase 10 menjadikan Dispatcher Optimizer sebagai provider REST API read-only bagi AMT Scheduler. Phase ini tidak membangun AMT Scheduler, tidak melakukan scheduling atau roster, tidak memasangkan driver/helper, tidak mengubah route/trip/LO, dan tidak menghitung rekomendasi waktu kedatangan AMT.

## Boundary dan arah integrasi

```text
AMT Scheduler → authenticated GET → Phase 10 canonical adapter → Dispatcher Optimizer database
```

AMT Scheduler tidak menerima akses langsung ke PostgreSQL. Master terminal/MT, historical operation, Phase 7 Route Version, dan Phase 8 Manual Dispatch tetap dimiliki Dispatcher Optimizer. Phase 10 hanya memetakan source tersebut ke contract canonical.

Base path eksternal:

```text
/api/v1/integration/amt-scheduler
```

## Authentication dan RBAC

- `/health` tidak membutuhkan authentication dan tidak mengembalikan operational data.
- Endpoint eksternal lain membutuhkan `Authorization: Bearer <token>`.
- Token dihasilkan dengan CSPRNG. Backend menyimpan SHA-256 hash dan hint, bukan raw token.
- Rotation mengubah hash secara atomik; token lama langsung menghasilkan `401 UNAUTHORIZED`.
- Rate limiter per-process default `600` request/client/menit diatur melalui `PHASE10_RATE_LIMIT_PER_MINUTE`. Multi-replica production perlu shared gateway/rate limiter.
- Console mengikuti seam auth aplikasi existing melalui `X-User` dan `X-Permissions`: `phase10.view`, `phase10.manage_credentials`, `phase10.view_logs`, `phase10.try_api`. Sebelum mengirim request dari endpoint explorer, UI memvalidasi `phase10.try_api` melalui internal `GET /console/try-permission`. Ketika header permission tidak ada, local-development actor memperoleh semua permission, sama seperti modul auth internal existing. Production harus mengganti seam tersebut dengan identity provider.

Full token hanya dikembalikan sekali ketika generate/rotate. Console menyimpan token baru hanya dalam React state untuk Try API dan tidak menulisnya ke local storage. Setelah reload, user harus menggunakan token yang disimpan secara aman oleh consumer atau melakukan rotation.

## External endpoints

```text
GET /health
GET /metadata
GET /terminals
GET /terminals/{terminal_id}
GET /vehicles
GET /vehicles/{mt_id}
GET /historical-operations
GET /routes
GET /routes/{route_id}
GET /routes/{route_id}/vehicles
GET /routes/{route_id}/shift-availability
GET /routes/{route_id}/availability-after
GET /shifts
```

Tidak ada POST/PUT/PATCH/DELETE pada external connector contract. Endpoint internal `/console/access/regenerate` adalah credential administration bagi operator Dispatcher Optimizer, bukan write API bagi AMT Scheduler.

## Canonical mapping

### Terminal

Source `master_depot` dipetakan ke stable `terminal_id`, code/name, coordinate, timezone, active flag, dan updated timestamp. Schema existing tidak mempunyai authoritative `terminal_type` dan `address`, sehingga kedua field dikirim `null`; `region` tidak disamarkan menjadi alamat.

### Mobil Tangki

Source `master_mt` dipetakan ke stable `mt_id`, terminal, registration, vehicle class/capacity, compartment count, tag, lifecycle status, dan timestamp. Compartment detail hanya dikirim jika authoritative configuration tersedia pada operational state; sistem tidak membagi kapasitas secara artifisial. Operational status master adalah `UNKNOWN` bila tidak ada current authoritative state dan tidak di-default menjadi `AVAILABLE`.

### Historical operation

Unit utama adalah satu `fact_shipment` sebagai satu canonical trip. LO dan SPBU dikumpulkan setelah shipment-level pagination, sehingga satu shipment dengan beberapa LO tetap hanya menghasilkan satu record. `trip_id` menggunakan stable `TRIP:{shipment_id}` karena schema historical existing belum memiliki independent historical trip ID/number. `trip_number` saat ini `1`. `amt1_id` dan `amt2_id` tetap `null` agar Phase 10 tidak mengambil ownership crew dari future AMT Scheduler.

### Phase 7 adapter

`Phase7RouteAdapter` membaca seluruh `route_version`, bukan hanya `optimization_job.current_route_version_id`. Canonical route ID adalah `P7:{route_version_id}`. Trip berasal dari `route_version_trip`, LO/SPBU dari `route_version_lo_assignment`, fleet dari `route_version_vehicle_assignment`, dan initial availability dari earliest `vehicle_ready_at_depot` atau exact operational-state snapshot route.

### Phase 8 adapter

`Phase8RouteAdapter` membaca setiap versioned `manual_dispatch_job`. Canonical route ID adalah `P8:{manual_dispatch_job.id}`. Timeline berasal dari `manual_dispatch_vehicle`, `manual_dispatch_trip`, `manual_dispatch_trip_lo`, dan `manual_dispatch_loading_order`. Phase 8 source rows tidak dimutasi.

## Route availability

Availability selalu dihitung dari canonical `route_id` terpilih. MT yang sama boleh mempunyai hasil berbeda pada `P7:...` dan `P8:...`.

```text
ON_TRIP     jika departure_at <= reference_at < return_to_depot_at
AVAILABLE   jika tidak ada active trip dan initial_available_at <= reference_at
UNAVAILABLE jika initial_available_at > reference_at
UNKNOWN     jika initial availability atau timeline tidak lengkap/invalid
```

Boundary bersifat departure-inclusive dan return-exclusive: exact departure adalah `ON_TRIP`; exact return adalah `AVAILABLE`. Saat `ON_TRIP`, `next_available_at` sama dengan return active trip. Saat `AVAILABLE`, `next_available_at` sama dengan reference time. `handover_window_minutes` adalah selisih sampai next departure dan hanya informational; Phase 10 tidak menilai kecukupan window.

Route timestamp yang return-nya lebih kecil dari departure atau kehilangan salah satu timestamp menghasilkan `UNKNOWN` bagi MT tersebut, bukan silent availability.

## Shift source

Untuk route, shift definition pertama-tama mengikuti exact source Phase 5 model melalui lineage Route → Phase 7 Job/Phase 8 source run → Prediction Run → model snapshot. Generic `/shifts` memakai saved Phase 2 shift configuration yang mencakup operation date, lalu latest depot Phase 5 model sebagai fallback authoritative. Sistem tidak hardcode empat shift. Bila source tidak tersedia, `/shifts` mengembalikan record kosong dan route shift-availability menghasilkan explicit `SHIFT_CONFIGURATION_NOT_FOUND`.

## Pagination dan incremental sync

- Default list page size: `100`; historical: `500`; maximum: `5000`.
- `terminals`, `vehicles`, dan `historical-operations` menerima timezone-aware `updated_since`.
- Historical filter/pagination dilakukan pada shipment di database; LO hanya dimuat untuk shipment pada page aktif.
- `routes` memakai SQL union Phase 7/8 dengan database-side date/source/status filter, count, sort, offset, dan limit.
- Inactive terminal/MT tetap dapat diambil; `active=false` tidak menghapus historical reference.

## Dataset versions

`/metadata` dan list response mengekspos logical version yang berasal dari dataset name, source record count, maximum authoritative update timestamp, dan historical cutoff. Snapshot terakhir disimpan pada `integration_dataset_version` untuk observability. Version bukan mutable business record dan tidak mengubah source dataset.

## Correlation, error, dan request logs

Setiap request Phase 10 memperoleh `X-Request-ID`. Error eksternal mempunyai bentuk:

```json
{
  "error": {
    "code": "INVALID_DATE_RANGE",
    "message": "date_from must not be greater than date_to",
    "request_id": "REQ-..."
  }
}
```

Middleware mencatat traffic eksternal ke `integration_api_log`: client, method, path, sanitized query, timestamp, response status/time, record count, IP, dan error. Authorization/token/secret tidak dibaca ke log. Console traffic tidak dihitung sebagai external connector traffic.

## Console UI

Route `/phase10/amt-scheduler-connector` mempunyai empat tab:

- Overview: status, API/base URL, authentication mode, dataset versions/freshness, request KPI, Test Connector.
- API Access: client, masked token, one-time Show/Copy, Regenerate, Copy Header.
- Endpoints: grouped contract docs, parameters, status codes, example response, Copy cURL, Try API, request ID/time/count, formatted JSON, serta availability summary table.
- API Logs: date range/status/endpoint/client filters, request table, dan sanitized row detail.

## Example credential creation

Local-development internal auth:

```bash
curl -X POST 'http://localhost:8000/api/v1/integration/amt-scheduler/console/access/regenerate' \
  -H 'X-User: connector-admin' \
  -H 'X-Permissions: phase10.manage_credentials'
```

Simpan `bearer_token` dari response di secret manager consumer. Jangan commit atau menaruhnya di URL.

## Example requests

```bash
BASE_URL='http://localhost:8000/api/v1/integration/amt-scheduler'
TOKEN='YOUR_API_TOKEN'

curl "$BASE_URL/health"
curl "$BASE_URL/metadata" -H "Authorization: Bearer $TOKEN"
curl "$BASE_URL/terminals?page=1&page_size=100" -H "Authorization: Bearer $TOKEN"
curl "$BASE_URL/vehicles?terminal_id=TBBM-001&active=true" -H "Authorization: Bearer $TOKEN"
curl "$BASE_URL/historical-operations?terminal_id=TBBM-001&date_from=2026-09-01&date_to=2026-09-06" -H "Authorization: Bearer $TOKEN"
curl "$BASE_URL/routes?operation_date=2026-09-07&terminal_id=TBBM-001" -H "Authorization: Bearer $TOKEN"
curl "$BASE_URL/routes/P7%3ASTABLE_ID" -H "Authorization: Bearer $TOKEN"
curl "$BASE_URL/routes/P7%3ASTABLE_ID/shift-availability?shift_id=SHIFT-2" -H "Authorization: Bearer $TOKEN"
curl "$BASE_URL/routes/P8%3ASTABLE_ID/availability-after?timestamp=2026-09-07T08%3A00%3A00%2B07%3A00" -H "Authorization: Bearer $TOKEN"
```

Encode tanda `+` pada timezone offset sebagai `%2B` saat menyusun raw URL. Browser `URLSearchParams` dan normal HTTP clients melakukannya otomatis.

## Known source limitations

- `master_depot` tidak mempunyai authoritative address/terminal type.
- Historical facts belum mempunyai explicit multi-trip identifier/number terpisah dari shipment.
- Master MT hanya menyimpan compartment count; per-compartment capacity hanya tersedia bila operational snapshot menyediakannya.
- Source Phase 7 `route_version` tidak mempunyai `updated_at`; immutable `created_at` dipakai untuk keduanya.
- Shift availability membutuhkan saved/model shift definition yang valid dan lengkap 24 jam.
- In-process rate limiting tidak dibagi antar API replica.
