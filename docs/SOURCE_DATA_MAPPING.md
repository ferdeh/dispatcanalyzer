# Source Data Mapping

## master data MT.xlsx

Sheet: `Mobil Tangki`

| Source column | Canonical target |
| --- | --- |
| `id` | `master_mt.source_mt_id` |
| `name` | `master_mt.vehicle_name_raw`, parsed to `vehicle_registration` and `capacity_label` |
| `assignee` | `master_mt.assignee` |
| `hubId` | `master_mt.source_hub_id`, `depot_identifier_alias.identifier_value` |
| `vehicleType tag` | `master_mt.vehicle_type_tag` |
| `project_tag` | `master_mt.project_tag_raw`, split to `master_tag` and `bridge_mt_tag` |
| `numberOfCompartments` | `master_mt.number_of_compartments` |
| `Depot` | `master_depot.depot_name`, `master_mt.depot_id`, depot alias |

## master data spbu.xlsx

Sheet: `Lembaga Penyalur`

| Source column | Canonical target |
| --- | --- |
| `Nama SPBU` | `master_spbu.spbu_code`, `spbu_identifier_alias` as `SPBU_CODE` |
| `Address` | `master_spbu.address` |
| `Kota` | `master_spbu.city` |
| `Coordinate` | `master_spbu.source_coordinate`, parsed to `latitude`, `longitude`, PostGIS `location` in migration |
| `jarak_km` | `master_spbu.master_distance_km` |
| `waktu_menit` | `master_spbu.master_travel_time_min` |
| `Vehicle Type tag` | `master_spbu.vehicle_type_tag` |
| `Project tag` | `master_spbu.project_tag_raw`, split to `master_tag` and `bridge_spbu_tag` |
| `Depot` | `master_depot.depot_name`, `master_spbu.primary_depot_id`, depot alias |

`Nama SPBU` is stored as string. It is not cast to integer.

## masterdata_LO.xlsx

Sheet: `Data Medan Mei`

| Source column | Canonical target |
| --- | --- |
| `area_id` | `fact_shipment.area_id` |
| `area` | `fact_shipment.area` |
| `kode_depot` | `depot_identifier_alias`, `fact_shipment.depot_id` |
| `tbbm` | `master_depot.depot_name`, `fact_shipment.depot_id`, and `fact_loading_order_line.source_depot_name` |
| `shipment_id` | `fact_shipment.shipment_id` and `fact_shipment.source_shipment_id`; kept exactly as imported and used as the grouped shipment key |
| `date` | `fact_shipment.operating_date` |
| `date_validasi` + `Jam Validasi` | `fact_shipment.validation_datetime` |
| `date_gate_out` + `Jam_gateout` | `fact_shipment.gate_out_datetime` |
| `date_end_shipment` + `jam_end_shipment` | `fact_shipment.shipment_end_datetime` |
| `nopol` | normalized and mapped to `master_mt.vehicle_registration`; stored on shipment |
| `Vehicle Type tag` | `fact_shipment.vehicle_type_tag_observed` |
| `Project tag` | `fact_shipment.project_tag_raw` |
| `nama_spbu` | mapped to `master_spbu.spbu_code`; stored as `source_spbu_code` |
| `shipto` | `fact_loading_order_line.shipto`, `spbu_identifier_alias` as `SHIPTO` |
| `loading_order_number` | part of `fact_loading_order_line` composite primary key with `source_depot_name`; unique within the same `tbbm` |
| `produk` | `master_product.product_name`, `fact_loading_order_line.source_product_name` |
| `quantity` | `fact_loading_order_line.quantity` |
| `supir_parent_id`, `supir`, `nip_supir` | `master_personnel` with role `DRIVER` |
| `status` | `fact_shipment.status`, `fact_loading_order_line.status` |
| `jarak_spbu` | `fact_loading_order_line.source_distance_km` |
| `km_aktual` | `fact_loading_order_line.actual_km` |
| `kernet_parent_id`, `kernet`, `nip_kernet` | `master_personnel` with role `ASSISTANT` |

Product names are preserved as full cell values. `PERTAMAX,BULK` is not split.

## GPS_data

The physical schema is not available. Phase 0 includes `stg_gps_data`, `fact_gps_event`, geofence tables, visit tables, and stop sequence tables. Mapping must be completed only after the actual source columns are inspected.
