# Shipment Model

Loading Order rows are not trips. Rows are grouped by source `shipment_id` into `fact_shipment`. The imported `shipment_id` value is preserved as-is; the app must not replace it with a generated id.

Each row remains a separate `fact_loading_order_line`, because a shipment can contain several loading orders in the same truck. In the canonical model, `loading_order_number` plus `source_depot_name`/`tbbm` is the primary key for a loading-order line.

- One `loading_order_number` represents one SPBU destination and one truck compartment assignment.
- Source `shipment_id` may repeat across loading-order rows because one shipment can contain multiple loading orders.
- A shipment can therefore contain multiple SPBU destinations and multiple truck compartments, but still use the same MT.
- `loading_order_number` must be unique and non-empty within the same `tbbm`; the same number may appear in another depot.

Shipment-SPBU membership is stored in `fact_shipment_spbu`. Actual sequence must come from GPS-derived visits when GPS is available.
