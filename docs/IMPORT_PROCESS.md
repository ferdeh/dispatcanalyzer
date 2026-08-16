# Import Process

The import lifecycle is:

RAW FILE -> STAGING -> VALIDATION -> NORMALIZATION -> REFERENCE MAPPING -> QUALITY REPORT -> USER REVIEW -> PUBLISH -> CANONICAL DATA

Phase 0 APIs:

- `POST /api/v1/imports/sample`
- `POST /api/v1/imports?domain=...&sheet_name=...`
- `GET /api/v1/imports`
- `GET /api/v1/imports/{id}`
- `GET /api/v1/exports/template?domain=...&file_format=xlsx|csv`
- `GET /api/v1/exports/data?domain=...&depot_id=...&file_format=xlsx|csv`

Every staged row keeps `raw_payload`, `normalized_payload`, `validation_status`, and `validation_messages`.

Template export supports `MOBIL_TANGKI`, `SPBU`, `LOADING_ORDER`, and `GPS`.

Data export is filtered by depot from `master_depot` and supports:

- `ALL` as XLSX multi-sheet.
- `MOBIL_TANGKI`
- `SPBU`
- `SHIPMENT`
- `LOADING_ORDER`

CSV export supports one domain per file. Use XLSX for `ALL`.

Dashboard filter:

- `GET /api/v1/foundation/overview?depot_id=...`
- `GET /api/v1/foundation/charts?depot_id=...`
- `GET /api/v1/data-quality/issues?depot_id=...`
- `GET /api/v1/master/compatibility/summary?depot_id=...`

Master data CRUD:

- `GET /api/v1/master-crud/{domain}?limit=25&offset=0&search=...&depot_id=...&active_status=...`
- `POST /api/v1/master-crud/{domain}`
- `PUT /api/v1/master-crud/{domain}/{record_id}`
- `DELETE /api/v1/master-crud/{domain}/{record_id}`

Supported CRUD domains are `MOBIL_TANGKI`, `SPBU`, `DEPOT`, `PRODUCT`, `TAG`, and `TAG_TYPE`. Deletes are soft deletes through `active_status=DELETED` where the table supports status.
