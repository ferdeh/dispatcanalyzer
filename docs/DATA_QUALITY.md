# Data Quality

Issues are stored in `data_quality_issue`.

Phase 0 implemented checks include:

- Missing or weak MT name parsing.
- Duplicate MT registration within an import.
- Missing or invalid SPBU coordinates.
- Unknown depot on source rows.
- Unknown MT in Loading Order.
- Unknown SPBU in Loading Order.
- Shipment with multiple `nopol` values.
- Invalid datetime order.
- Invalid quantity.

Data-quality issues are visible through `GET /api/v1/data-quality/issues` and the frontend quality explorer.
