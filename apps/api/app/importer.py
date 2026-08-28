from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    BridgeMTTag,
    BridgeSPBUTag,
    DataQualityIssue,
    DepotGeofence,
    DepotIdentifierAlias,
    FactLoadingOrderLine,
    FactShipment,
    FactShipmentSPBU,
    ImportAudit,
    MasterDepot,
    MasterMT,
    MasterPersonnel,
    MasterProduct,
    MasterSPBU,
    MasterTag,
    MasterTagType,
    ProductAlias,
    SpbuGeofence,
    SpbuIdentifierAlias,
    StgGPSData,
    StgLoadingOrder,
    StgMT,
    StgSPBU,
    TagAlias,
)
from .normalization import (
    clean_str,
    combine_datetime,
    dataframe_records,
    file_sha256,
    infer_tag_type,
    make_id,
    normalize_key,
    normalize_product,
    parse_coordinate,
    parse_mt_name,
    resolve_sheet_name,
    source_int,
    source_number,
    source_time,
    split_project_tags,
)

TAG_TYPES = {
    "PROGRAM": "Program",
    "ACCESS": "Access",
    "REGION": "Region",
    "GEOGRAPHY": "Geography",
    "PROJECT": "Project",
    "VEHICLE_CLASS": "Vehicle Class",
    "SPECIAL_RESTRICTION": "Special Restriction",
    "PRODUCT_COMPATIBILITY": "Product Compatibility",
    "UNKNOWN": "Unknown",
}


class ImportProcessor:
    def __init__(self, db: Session):
        self.db = db

    def upsert_active(self, model, primary_key: str, values: dict):
        record = self.db.get(model, primary_key)
        if record is None:
            record = model(**values)
            self.db.add(record)
            return record
        for field, value in values.items():
            setattr(record, field, value)
        return record

    def validate_required_columns(self, rows: list[dict[str, Any]], domain: str, required_columns: tuple[str, ...]) -> None:
        columns = set(rows[0].keys()) if rows else set()
        missing = [column for column in required_columns if column not in columns]
        if missing:
            raise ValueError(f"{domain} import columns do not match the selected domain. Missing columns: {', '.join(missing)}.")

    def import_examples(self, example_dir: Path) -> dict[str, str]:
        results = {
            "mt": self.import_master_mt(example_dir / "master data MT.xlsx"),
            "spbu": self.import_master_spbu(example_dir / "master data spbu.xlsx"),
            "loading_order": self.import_loading_order(example_dir / "masterdata_LO.xlsx"),
        }
        self.ensure_geofences()
        self.db.commit()
        return results

    def create_import(self, domain: str, path: Path, sheet_name: str, uploaded_by: str = "system", filename: str | None = None) -> ImportAudit:
        import_id = make_id("imp", domain, path.name, file_sha256(path), sheet_name, datetime.now(UTC).isoformat())
        audit = ImportAudit(
            import_id=import_id,
            domain=domain,
            filename=filename or path.name,
            file_checksum=file_sha256(path),
            sheet_name=sheet_name,
            uploaded_by=uploaded_by,
            status="STAGED",
            mapping_version="phase0.v1",
        )
        self.db.add(audit)
        self.db.flush()
        return audit

    def issue(self, entity_type: str, entity_id: str | None, import_id: str | None, rule_code: str, severity: str, description: str) -> None:
        self.db.merge(
            DataQualityIssue(
                issue_id=make_id("dqi", entity_type, entity_id, import_id, rule_code, description),
                entity_type=entity_type,
                entity_id=entity_id,
                source_import_id=import_id,
                rule_code=rule_code,
                severity=severity,
                description=description,
                status="OPEN",
            )
        )

    def ensure_tag_types(self) -> None:
        for code, name in TAG_TYPES.items():
            self.db.merge(MasterTagType(tag_type_id=make_id("tagtype", code), code=code, name=name, admin_editable=True))
        self.db.flush()

    def resolve_tag(self, value: str, source_domain: str) -> MasterTag:
        self.ensure_tag_types()
        normalized = normalize_key(value) or "UNKNOWN"
        alias = self.db.scalar(select(TagAlias).where(TagAlias.normalized_alias == normalized))
        if alias:
            tag = self.db.get(MasterTag, alias.canonical_tag_id)
            if tag:
                return tag
        tag_type_code = infer_tag_type(value)
        tag = MasterTag(
            tag_id=make_id("tag", normalized),
            tag_type_id=make_id("tagtype", tag_type_code),
            tag_value=value.strip(),
            normalized_tag=normalized,
            active_status="ACTIVE",
        )
        self.db.merge(tag)
        self.db.flush()
        aliases = {value}
        if normalized == "ALLIN":
            aliases.update({"All In", "ALL IN", "ALLIN"})
        seen_alias_ids: set[str] = set()
        for alias_value in aliases:
            alias_id = make_id("tagalias", normalize_key(alias_value), tag.tag_id, source_domain)
            if alias_id in seen_alias_ids:
                continue
            seen_alias_ids.add(alias_id)
            self.db.merge(
                TagAlias(
                    tag_alias_id=alias_id,
                    alias_value=alias_value,
                    normalized_alias=normalize_key(alias_value) or normalized,
                    canonical_tag_id=tag.tag_id,
                    source_domain=source_domain,
                )
            )
        return tag

    def resolve_depot(self, name: Any, code: Any, source_import_id: str, source_system: str) -> MasterDepot | None:
        depot_name = clean_str(name) or clean_str(code)
        if not depot_name:
            return None
        normalized_name = normalize_key(depot_name)
        depot_id = make_id("depot", normalized_name)
        depot = MasterDepot(depot_id=depot_id, depot_code=clean_str(code) or normalized_name, depot_name=depot_name, source_import_id=source_import_id)
        self.db.merge(depot)
        self.db.flush()
        for alias_type, alias_value in (("DEPOT_NAME", depot_name), ("DEPOT_CODE", clean_str(code))):
            if alias_value:
                self.db.merge(
                    DepotIdentifierAlias(
                        depot_identifier_alias_id=make_id("depotalias", depot_id, alias_type, alias_value),
                        depot_id=depot_id,
                        identifier_type=alias_type,
                        identifier_value=alias_value,
                        normalized_identifier=normalize_key(alias_value) or alias_value,
                        source_system=source_system,
                    )
                )
        return depot

    def import_master_mt(self, path: Path, sheet_name: str = "Mobil Tangki", filename: str | None = None) -> str:
        actual_sheet_name = resolve_sheet_name(path, sheet_name, ("Mobil Tangki", "MOBIL_TANGKI"))
        audit = self.create_import("MOBIL_TANGKI", path, actual_sheet_name, filename=filename)
        rows = dataframe_records(path, actual_sheet_name)
        self.validate_required_columns(rows, "MOBIL_TANGKI", ("name",))
        valid = warnings = rejected = 0
        seen_registrations: set[str] = set()
        for row_number, row in enumerate(rows, start=2):
            registration, capacity, parse_messages = parse_mt_name(row.get("name"))
            messages = list(parse_messages)
            if not registration:
                messages.append("missing normalized registration")
            if registration in seen_registrations:
                messages.append("duplicate normalized registration in source import")
            seen_registrations.add(registration or f"row-{row_number}")
            depot = self.resolve_depot(row.get("Depot"), row.get("hubId"), audit.import_id, "MASTER_MT")
            normalized = {
                "source_mt_id": clean_str(row.get("id")),
                "vehicle_name_raw": clean_str(row.get("name")),
                "vehicle_registration": registration,
                "capacity_label": capacity,
                "vehicle_type_tag": source_int(row.get("vehicleType tag")),
                "project_tags": split_project_tags(row.get("project_tag")),
                "number_of_compartments": source_int(row.get("numberOfCompartments")),
                "depot_id": depot.depot_id if depot else None,
            }
            status = "WARNING" if messages else "VALID"
            warnings += int(status == "WARNING")
            valid += int(status == "VALID")
            self.db.add(StgMT(staging_id=make_id("stgmt", audit.import_id, row_number), import_id=audit.import_id, source_row_number=row_number, raw_payload=row, normalized_payload=normalized, validation_status=status, validation_messages=messages))
            if messages:
                self.issue("MT", registration, audit.import_id, "MT_NAME_PARSE", "WARNING", "; ".join(messages))
            mt_id = make_id("mt", registration or clean_str(row.get("name")) or row_number)
            self.upsert_active(
                MasterMT,
                mt_id,
                {
                    "mt_id": mt_id,
                    "source_mt_id": clean_str(row.get("id")),
                    "vehicle_name_raw": clean_str(row.get("name")) or "",
                    "vehicle_registration": registration,
                    "capacity_label": capacity,
                    "vehicle_type_tag": source_int(row.get("vehicleType tag")),
                    "project_tag_raw": clean_str(row.get("project_tag")),
                    "number_of_compartments": source_int(row.get("numberOfCompartments")),
                    "depot_id": depot.depot_id if depot else None,
                    "source_hub_id": clean_str(row.get("hubId")),
                    "assignee": clean_str(row.get("assignee")),
                    "active_status": "ACTIVE",
                    "source_import_id": audit.import_id,
                },
            )
            self.db.flush()
            for tag_value in split_project_tags(row.get("project_tag")):
                tag = self.resolve_tag(tag_value, "MASTER_MT")
                self.db.merge(BridgeMTTag(mt_id=mt_id, tag_id=tag.tag_id, source_import_id=audit.import_id))
        audit.total_rows = len(rows)
        audit.valid_rows = valid
        audit.warning_rows = warnings
        audit.rejected_rows = rejected
        audit.status = "PUBLISHED"
        audit.published_at = datetime.now(UTC)
        self.db.commit()
        return audit.import_id

    def import_master_spbu(self, path: Path, sheet_name: str = "Lembaga Penyalur", filename: str | None = None) -> str:
        actual_sheet_name = resolve_sheet_name(path, sheet_name, ("SPBU", "Lembaga Penyalur"))
        audit = self.create_import("SPBU", path, actual_sheet_name, filename=filename)
        rows = dataframe_records(path, actual_sheet_name)
        self.validate_required_columns(rows, "SPBU", ("Nama SPBU",))
        valid = warnings = rejected = 0
        for row_number, row in enumerate(rows, start=2):
            code = clean_str(row.get("Nama SPBU"))
            lat, lon, coordinate_messages = parse_coordinate(row.get("Coordinate"))
            messages = list(coordinate_messages)
            if not code:
                messages.append("missing SPBU code")
            depot = self.resolve_depot(row.get("Depot"), None, audit.import_id, "MASTER_SPBU")
            if not depot:
                messages.append("unknown depot")
            normalized = {
                "spbu_code": code,
                "latitude": lat,
                "longitude": lon,
                "vehicle_type_tag": source_int(row.get("Vehicle Type tag")),
                "project_tags": split_project_tags(row.get("Project tag")),
                "depot_id": depot.depot_id if depot else None,
                "official_window_start": source_time(row.get("Official Window Start"), time(0, 0)).isoformat(timespec="minutes"),
                "official_window_end": source_time(row.get("Official Window End"), time(23, 59)).isoformat(timespec="minutes"),
            }
            status = "WARNING" if messages else "VALID"
            warnings += int(status == "WARNING")
            valid += int(status == "VALID")
            self.db.add(StgSPBU(staging_id=make_id("stgspbu", audit.import_id, row_number), import_id=audit.import_id, source_row_number=row_number, raw_payload=row, normalized_payload=normalized, validation_status=status, validation_messages=messages))
            if messages:
                self.issue("SPBU", code, audit.import_id, "SPBU_SOURCE_VALIDATION", "WARNING", "; ".join(messages))
            if not code:
                rejected += 1
                continue
            spbu_id = make_id("spbu", code)
            self.upsert_active(
                MasterSPBU,
                spbu_id,
                {
                    "spbu_id": spbu_id,
                    "spbu_code": code,
                    "spbu_name": code,
                    "address": clean_str(row.get("Address")),
                    "city": clean_str(row.get("Kota")),
                    "latitude": lat,
                    "longitude": lon,
                    "source_coordinate": clean_str(row.get("Coordinate")),
                    "master_distance_km": source_number(row.get("jarak_km")),
                    "master_travel_time_min": source_number(row.get("waktu_menit")),
                    "vehicle_type_tag": source_int(row.get("Vehicle Type tag")),
                    "project_tag_raw": clean_str(row.get("Project tag")),
                    "primary_depot_id": depot.depot_id if depot else None,
                    "active_status": "ACTIVE",
                    "official_window_start": source_time(row.get("Official Window Start"), time(0, 0)),
                    "official_window_end": source_time(row.get("Official Window End"), time(23, 59)),
                    "source_import_id": audit.import_id,
                },
            )
            self.db.flush()
            self.db.merge(SpbuIdentifierAlias(spbu_identifier_alias_id=make_id("spbualias", spbu_id, "SPBU_CODE", code), spbu_id=spbu_id, identifier_type="SPBU_CODE", identifier_value=code, normalized_identifier=normalize_key(code) or code, source_system="MASTER_SPBU"))
            for tag_value in split_project_tags(row.get("Project tag")):
                tag = self.resolve_tag(tag_value, "MASTER_SPBU")
                self.db.merge(BridgeSPBUTag(spbu_id=spbu_id, tag_id=tag.tag_id, source_import_id=audit.import_id))
        audit.total_rows = len(rows)
        audit.valid_rows = valid
        audit.warning_rows = warnings
        audit.rejected_rows = rejected
        audit.status = "PUBLISHED"
        audit.published_at = datetime.now(UTC)
        self.db.commit()
        return audit.import_id

    def resolve_product(self, raw_product: Any, source_import_id: str) -> MasterProduct | None:
        product_name = clean_str(raw_product)
        normalized = normalize_product(raw_product)
        if not product_name or not normalized:
            return None
        product_id = make_id("product", normalized)
        self.db.merge(MasterProduct(product_id=product_id, product_name=product_name, normalized_product=normalized, source_import_id=source_import_id))
        self.db.flush()
        self.db.merge(ProductAlias(product_alias_id=make_id("productalias", normalized, "LO"), product_id=product_id, alias_value=product_name, normalized_alias=normalized, source_system="LO"))
        return self.db.get(MasterProduct, product_id) or MasterProduct(product_id=product_id, product_name=product_name, normalized_product=normalized)

    def resolve_personnel(self, parent_id: Any, name: Any, nip: Any, role: str, source_import_id: str) -> str | None:
        parent = clean_str(parent_id)
        person_name = clean_str(name)
        if not parent and not person_name:
            return None
        personnel_id = make_id("person", role, parent or person_name)
        self.db.merge(MasterPersonnel(personnel_id=personnel_id, source_parent_id=parent, name=person_name, nip=clean_str(nip), role=role, source_import_id=source_import_id))
        self.db.flush()
        return personnel_id

    def import_loading_order(self, path: Path, sheet_name: str = "Data Medan Mei", filename: str | None = None) -> str:
        actual_sheet_name = resolve_sheet_name(path, sheet_name, ("Data Medan Mei", "Loading Orders", "LOADING_ORDER"))
        audit = self.create_import("LOADING_ORDER", path, actual_sheet_name, filename=filename)
        rows = dataframe_records(path, actual_sheet_name)
        self.validate_required_columns(rows, "LOADING_ORDER", ("shipment_id", "loading_order_number", "tbbm"))
        lo_depot_counts = Counter(
            (clean_str(row.get("loading_order_number")), clean_str(row.get("tbbm")))
            for row in rows
            if clean_str(row.get("loading_order_number")) and clean_str(row.get("tbbm"))
        )
        by_shipment: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row_number, row in enumerate(rows, start=2):
            source_shipment_id = clean_str(row.get("shipment_id")) or f"row-{row_number}"
            by_shipment[source_shipment_id].append({"row_number": row_number, "row": row})
        mt_by_registration = {mt.vehicle_registration: mt for mt in self.db.scalars(select(MasterMT)).all() if mt.vehicle_registration}
        spbu_by_code = {spbu.spbu_code: spbu for spbu in self.db.scalars(select(MasterSPBU)).all()}
        valid = warnings = rejected = 0
        for source_shipment_id, grouped in by_shipment.items():
            first = grouped[0]["row"]
            registrations = {normalize_key(item["row"].get("nopol")) for item in grouped if normalize_key(item["row"].get("nopol"))}
            vehicle_registration = next(iter(registrations)) if registrations else None
            messages: list[str] = []
            if len(registrations) > 1:
                messages.append("shipment contains multiple nopol values")
            mt = mt_by_registration.get(vehicle_registration) if vehicle_registration else None
            vehicle_status = "MATCHED" if mt else "UNMATCHED"
            if not mt:
                messages.append("unknown MT")
            depot = self.resolve_depot(first.get("tbbm"), first.get("kode_depot"), audit.import_id, "LO")
            validation_dt = combine_datetime(first.get("date_validasi"), first.get("Jam Validasi"))
            gate_out_dt = combine_datetime(first.get("date_gate_out"), first.get("Jam_gateout"))
            end_dt = combine_datetime(first.get("date_end_shipment"), first.get("jam_end_shipment"))
            if gate_out_dt and end_dt and end_dt < gate_out_dt:
                messages.append("shipment_end before gate_out")
            if validation_dt and gate_out_dt and gate_out_dt < validation_dt:
                messages.append("gate_out before validation")
            driver_id = self.resolve_personnel(first.get("supir_parent_id"), first.get("supir"), first.get("nip_supir"), "DRIVER", audit.import_id)
            assistant_id = self.resolve_personnel(first.get("kernet_parent_id"), first.get("kernet"), first.get("nip_kernet"), "ASSISTANT", audit.import_id)
            shipment_pk = source_shipment_id
            self.db.merge(
                FactShipment(
                    shipment_id=shipment_pk,
                    source_shipment_id=source_shipment_id,
                    operating_date=combine_datetime(first.get("date"), None).date() if combine_datetime(first.get("date"), None) else None,
                    area_id=clean_str(first.get("area_id")),
                    area=clean_str(first.get("area")),
                    depot_id=depot.depot_id if depot else None,
                    mt_id=mt.mt_id if mt else None,
                    vehicle_registration=vehicle_registration,
                    vehicle_mapping_status=vehicle_status,
                    vehicle_type_tag_observed=clean_str(first.get("Vehicle Type tag")),
                    project_tag_raw=clean_str(first.get("Project tag")),
                    validation_datetime=validation_dt,
                    gate_out_datetime=gate_out_dt,
                    shipment_end_datetime=end_dt,
                    driver_id=driver_id,
                    assistant_id=assistant_id,
                    status=clean_str(first.get("status")),
                    source_import_id=audit.import_id,
                )
            )
            self.db.flush()
            if messages:
                self.issue("SHIPMENT", source_shipment_id, audit.import_id, "LO_SHIPMENT_VALIDATION", "SEVERE" if len(registrations) > 1 else "WARNING", "; ".join(messages))
            for item in grouped:
                row = item["row"]
                row_number = item["row_number"]
                lo_number = clean_str(row.get("loading_order_number"))
                source_depot_name = clean_str(row.get("tbbm"))
                spbu_code = clean_str(row.get("nama_spbu"))
                spbu = spbu_by_code.get(spbu_code) if spbu_code else None
                product = self.resolve_product(row.get("produk"), audit.import_id)
                row_messages = []
                row_rejected = False
                if not lo_number:
                    row_rejected = True
                    row_messages.append("missing loading_order_number")
                    self.issue("LO_LINE", f"row-{row_number}", audit.import_id, "MISSING_LOADING_ORDER_NUMBER", "SEVERE", "Loading order number is required as the canonical primary key.")
                if not source_depot_name:
                    row_rejected = True
                    row_messages.append("missing tbbm")
                    self.issue("LO_LINE", lo_number or f"row-{row_number}", audit.import_id, "MISSING_TBBM", "SEVERE", "Depot name tbbm is required because loading-order uniqueness is scoped by depot.")
                elif lo_depot_counts[(lo_number, source_depot_name)] > 1:
                    row_rejected = True
                    row_messages.append("duplicate loading_order_number for tbbm")
                    self.issue("LO_LINE", lo_number, audit.import_id, "DUPLICATE_LOADING_ORDER_NUMBER_DEPOT", "SEVERE", "Loading order number must be unique within the same tbbm/depot.")
                if not spbu:
                    row_messages.append("unknown SPBU")
                    self.issue("LO_LINE", lo_number, audit.import_id, "UNKNOWN_SPBU", "WARNING", f"LO SPBU {spbu_code} is not in master_spbu")
                if source_number(row.get("quantity")) is None or (source_number(row.get("quantity")) or 0) <= 0:
                    row_messages.append("invalid quantity")
                normalized = {
                    "shipment_id": source_shipment_id,
                    "source_depot_name": source_depot_name,
                    "vehicle_registration": vehicle_registration,
                    "vehicle_mapping_status": vehicle_status,
                    "spbu_code": spbu_code,
                    "spbu_mapping_status": "MATCHED" if spbu else "UNMATCHED",
                    "product": normalize_product(row.get("produk")),
                    "quantity": source_number(row.get("quantity")),
                }
                status = "REJECTED" if row_rejected else "WARNING" if row_messages or messages else "VALID"
                rejected += int(status == "REJECTED")
                warnings += int(status == "WARNING")
                valid += int(status == "VALID")
                self.db.add(StgLoadingOrder(staging_id=make_id("stglo", audit.import_id, row_number), import_id=audit.import_id, source_row_number=row_number, raw_payload=row, normalized_payload=normalized, validation_status=status, validation_messages=row_messages + messages))
                if row_rejected:
                    continue
                self.db.merge(
                    FactLoadingOrderLine(
                        loading_order_number=lo_number,
                        source_depot_name=source_depot_name,
                        shipment_id=shipment_pk,
                        spbu_id=spbu.spbu_id if spbu else None,
                        spbu_mapping_status="MATCHED" if spbu else "UNMATCHED",
                        source_spbu_code=spbu_code,
                        shipto=clean_str(row.get("shipto")),
                        product_id=product.product_id if product else None,
                        source_product_name=clean_str(row.get("produk")),
                        quantity=source_number(row.get("quantity")),
                        status=clean_str(row.get("status")),
                        source_distance_km=source_number(row.get("jarak_spbu")),
                        actual_km=source_number(row.get("km_aktual")),
                        source_import_id=audit.import_id,
                    )
                )
                if spbu:
                    self.db.merge(FactShipmentSPBU(shipment_id=shipment_pk, spbu_id=spbu.spbu_id, assignment_source="LO", source_import_id=audit.import_id))
                    shipto = clean_str(row.get("shipto"))
                    if shipto:
                        self.db.merge(SpbuIdentifierAlias(spbu_identifier_alias_id=make_id("spbualias", spbu.spbu_id, "SHIPTO", shipto), spbu_id=spbu.spbu_id, identifier_type="SHIPTO", identifier_value=shipto, normalized_identifier=normalize_key(shipto) or shipto, source_system="LO"))
        audit.total_rows = len(rows)
        audit.valid_rows = valid
        audit.warning_rows = warnings
        audit.rejected_rows = rejected
        audit.status = "PUBLISHED"
        audit.published_at = datetime.now(UTC)
        self.db.commit()
        return audit.import_id

    def stage_gps_file(self, path: Path, sheet_name: str, filename: str | None = None) -> str:
        actual_sheet_name = resolve_sheet_name(path, sheet_name, ("GPS", "GPS Data"))
        audit = self.create_import("GPS", path, actual_sheet_name, filename=filename)
        rows = dataframe_records(path, actual_sheet_name)
        self.validate_required_columns(rows, "GPS", ("vehicle_registration", "event_datetime"))
        for row_number, row in enumerate(rows, start=2):
            self.db.add(StgGPSData(staging_id=make_id("stggps", audit.import_id, row_number), import_id=audit.import_id, source_row_number=row_number, raw_payload=row, normalized_payload={}, validation_status="PENDING_MAPPING", validation_messages=["GPS physical schema requires source mapping review"]))
        audit.total_rows = len(rows)
        audit.status = "STAGED"
        self.db.commit()
        return audit.import_id

    def ensure_geofences(self) -> None:
        for spbu in self.db.scalars(select(MasterSPBU).where(MasterSPBU.latitude.is_not(None), MasterSPBU.longitude.is_not(None))).all():
            self.db.merge(SpbuGeofence(spbu_geofence_id=make_id("spbugeofence", spbu.spbu_id), spbu_id=spbu.spbu_id, radius_m=125.0))
        for depot in self.db.scalars(select(MasterDepot)).all():
            self.db.merge(DepotGeofence(depot_geofence_id=make_id("depotgeofence", depot.depot_id), depot_id=depot.depot_id, radius_m=300.0))

    def summary(self) -> dict[str, int]:
        tables = {
            "imports": ImportAudit,
            "mt": MasterMT,
            "spbu": MasterSPBU,
            "depots": MasterDepot,
            "products": MasterProduct,
            "tags": MasterTag,
            "tag_types": MasterTagType,
            "loading_order_lines": FactLoadingOrderLine,
            "shipments": FactShipment,
            "gps_events": StgGPSData,
            "gps_visits": SpbuGeofence,
            "data_quality_issues": DataQualityIssue,
        }
        return {name: self.db.scalar(select(func.count()).select_from(model)) or 0 for name, model in tables.items()}
