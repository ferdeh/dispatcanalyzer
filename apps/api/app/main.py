from __future__ import annotations

import csv
import base64
import json
import logging
import re
import shutil
import tempfile
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime
from io import BytesIO, StringIO
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel
from sqlalchemy import String, Time, cast, delete, desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .compatibility import evaluate_mt_spbu_compatibility
from .affinity_intelligence import build_affinity_date_availability, build_affinity_intelligence_payload
from .config import get_settings
from .database import SessionLocal, get_db
from .departure_intelligence import build_departure_date_availability, build_departure_intelligence_payload, build_shift_intelligence_payload
from .importer import ImportProcessor
from .models import (
    BridgeMTTag,
    BridgeSPBUTag,
    DataQualityIssue,
    DepotIdentifierAlias,
    FactLoadingOrderLine,
    FactShipment,
    FactShipmentSPBU,
    ImportAudit,
    MasterDepot,
    MasterMT,
    MasterProduct,
    MasterSPBU,
    MasterTag,
    MasterTagType,
    ProductAlias,
    StgGPSData,
    StgLoadingOrder,
    StgMT,
    StgSPBU,
    TagAlias,
)
from .normalization import clean_str, infer_tag_type, make_id, normalize_key, normalize_product, parse_coordinate, parse_mt_name, source_int, source_number, split_project_tags
from .pairing_intelligence import build_pairing_date_availability, build_pairing_intelligence_payload
from .phase5_behavioral import recover_interrupted_behavioral_training_runs
from .phase5_routes import router as phase5_router
from .phase6_routes import router as phase6_router
from .google_routes_settings_routes import router as google_routes_settings_router
from .tag_consistency import build_tag_consistency_payload, get_tag_consistency_detail

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        with SessionLocal() as db:
            recovered = recover_interrupted_behavioral_training_runs(db)
        if recovered:
            logger.warning("Recovered %s interrupted Phase 5 behavioral training run(s).", recovered)
    except Exception:
        # Recovery must never turn a diagnostic cleanup into another app-load
        # failure. Normal endpoint database errors remain visible independently.
        logger.exception("Could not recover interrupted Phase 5 behavioral training runs during startup.")
    yield


app = FastAPI(title="Dispatch Intelligence Platform", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(phase5_router)
app.include_router(phase6_router)
app.include_router(google_routes_settings_router)


class CompatibilityRequest(BaseModel):
    mt_id: str
    spbu_id: str
    product_id: str | None = None


def sync_spbu_coordinates_from_source(db: Session, rows: list[MasterSPBU]) -> int:
    updated = 0
    for spbu in rows:
        if not spbu.source_coordinate:
            continue
        latitude, longitude, messages = parse_coordinate(spbu.source_coordinate)
        if messages:
            continue
        if spbu.latitude == latitude and spbu.longitude == longitude:
            continue
        spbu.latitude = latitude
        spbu.longitude = longitude
        updated += 1
    if updated:
        db.commit()
    return updated


class OperationalShiftRequest(BaseModel):
    shift_id: str | None = None
    name: str
    start_time: str
    end_time: str


class ShiftAnalysisRequest(BaseModel):
    depot_id: str
    start_date: str
    end_date: str
    bucket_minutes: int = 30
    shifts: list[OperationalShiftRequest]
    assignment_method: str
    search: str | None = None
    sort_column: str = "observation_count"
    sort_direction: str = "desc"


IMPORT_TEMPLATE_COLUMNS = {
    "MOBIL_TANGKI": ["id", "name", "assignee", "hubId", "vehicleType tag", "project_tag", "numberOfCompartments", "Depot"],
    "SPBU": ["Nama SPBU", "Address", "Kota", "Coordinate", "jarak_km", "waktu_menit", "Vehicle Type tag", "Project tag", "Depot"],
    "LOADING_ORDER": [
        "area_id",
        "area",
        "kode_depot",
        "tbbm",
        "shipment_id",
        "date",
        "date_validasi",
        "Jam Validasi",
        "date_gate_out",
        "Jam_gateout",
        "date_end_shipment",
        "jam_end_shipment",
        "nopol",
        "Vehicle Type tag",
        "Project tag",
        "nama_spbu",
        "shipto",
        "loading_order_number",
        "produk",
        "quantity",
        "supir_parent_id",
        "supir",
        "nip_supir",
        "status",
        "jarak_spbu",
        "km_aktual",
        "kernet_parent_id",
        "kernet",
        "nip_kernet",
    ],
    "GPS": ["vehicle_registration", "event_datetime", "latitude", "longitude", "speed", "heading", "source_device_id"],
}

EXPORT_DOMAIN_LABELS = {
    "MOBIL_TANGKI": "Mobil Tangki",
    "SPBU": "SPBU",
    "LOADING_ORDER": "Loading Orders",
    "SHIPMENT": "Shipments",
    "ALL": "All Data",
}


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "0", "service": "dispatch-intelligence-api"}


@app.post("/api/v1/imports/sample")
def import_sample_data(db: Session = Depends(get_db)) -> dict:
    if not settings.example_data_dir.exists():
        raise HTTPException(status_code=404, detail=f"Example data dir not found: {settings.example_data_dir}")
    results = ImportProcessor(db).import_examples(settings.example_data_dir)
    return {"status": "PUBLISHED", "imports": results}


@app.post("/api/v1/imports")
def upload_import(domain: str, sheet_name: str, file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".xlsx", ".csv"}:
        raise HTTPException(status_code=400, detail="Only XLSX and CSV are supported in Phase 0.")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        shutil.copyfileobj(file.file, handle)
        temp_path = Path(handle.name)
    processor = ImportProcessor(db)
    normalized_domain = domain.upper()
    original_filename = file.filename or temp_path.name
    try:
        if normalized_domain in {"MT", "MOBIL_TANGKI"}:
            import_id = processor.import_master_mt(temp_path, sheet_name, filename=original_filename)
        elif normalized_domain == "SPBU":
            import_id = processor.import_master_spbu(temp_path, sheet_name, filename=original_filename)
        elif normalized_domain in {"LO", "LOADING_ORDER"}:
            import_id = processor.import_loading_order(temp_path, sheet_name, filename=original_filename)
        elif normalized_domain == "GPS":
            import_id = processor.stage_gps_file(temp_path, sheet_name, filename=original_filename)
        else:
            raise HTTPException(status_code=400, detail="Unknown import domain.")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"import_id": import_id, "domain": normalized_domain}


@app.get("/api/v1/imports")
def list_imports(db: Session = Depends(get_db)) -> list[dict]:
    audits = db.scalars(select(ImportAudit).order_by(desc(ImportAudit.uploaded_at)).limit(8)).all()
    return [
        {
            "import_id": audit.import_id,
            "domain": audit.domain,
            "filename": audit.filename,
            "sheet_name": audit.sheet_name,
            "uploaded_at": audit.uploaded_at,
            "total_rows": audit.total_rows,
            "valid_rows": audit.valid_rows,
            "warning_rows": audit.warning_rows,
            "rejected_rows": audit.rejected_rows,
            "status": audit.status,
            "mapping_version": audit.mapping_version,
        }
        for audit in audits
    ]


@app.get("/api/v1/imports/{import_id}")
def get_import(import_id: str, db: Session = Depends(get_db)) -> dict:
    audit = db.get(ImportAudit, import_id)
    if not audit:
        raise HTTPException(status_code=404, detail="Import not found")
    staging_model = {
        "MOBIL_TANGKI": StgMT,
        "SPBU": StgSPBU,
        "LOADING_ORDER": StgLoadingOrder,
        "GPS": StgGPSData,
    }.get(audit.domain)
    preview = []
    if staging_model:
        preview = [
            {
                "source_row_number": row.source_row_number,
                "validation_status": row.validation_status,
                "validation_messages": row.validation_messages,
                "normalized_payload": row.normalized_payload,
            }
            for row in db.scalars(select(staging_model).where(staging_model.import_id == import_id).limit(50)).all()
        ]
    return {"import": public(audit), "preview": preview}


@app.get("/api/v1/foundation/overview")
def foundation_overview(depot_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    depot_id = normalize_depot_filter(db, depot_id)
    count = lambda model: db.scalar(select(func.count()).select_from(model)) or 0
    mt_where = [MasterMT.depot_id == depot_id] if depot_id else []
    spbu_where = [MasterSPBU.primary_depot_id == depot_id] if depot_id else []
    shipment_where = [FactShipment.depot_id == depot_id] if depot_id else []
    lo_stmt = select(func.count()).select_from(FactLoadingOrderLine)
    lo_unique_spbu_stmt = select(func.count(func.distinct(FactLoadingOrderLine.source_spbu_code))).select_from(FactLoadingOrderLine)
    unmatched_spbu_stmt = select(func.count()).select_from(FactLoadingOrderLine).where(FactLoadingOrderLine.spbu_mapping_status != "MATCHED")
    if depot_id:
        lo_stmt = lo_stmt.join(FactShipment, FactShipment.shipment_id == FactLoadingOrderLine.shipment_id).where(FactShipment.depot_id == depot_id)
        lo_unique_spbu_stmt = lo_unique_spbu_stmt.join(FactShipment, FactShipment.shipment_id == FactLoadingOrderLine.shipment_id).where(FactShipment.depot_id == depot_id)
        unmatched_spbu_stmt = unmatched_spbu_stmt.join(FactShipment, FactShipment.shipment_id == FactLoadingOrderLine.shipment_id).where(FactShipment.depot_id == depot_id)
    unmatched_mt = db.scalar(select(func.count()).select_from(FactShipment).where(FactShipment.vehicle_mapping_status != "MATCHED", *shipment_where)) or 0
    unmatched_spbu = db.scalar(unmatched_spbu_stmt) or 0
    unique_mt_lo = db.scalar(select(func.count(func.distinct(FactShipment.vehicle_registration))).where(*shipment_where)) or 0
    unique_spbu_lo = db.scalar(lo_unique_spbu_stmt) or 0
    product_stmt = select(func.count(func.distinct(FactLoadingOrderLine.product_id))).select_from(FactLoadingOrderLine)
    if depot_id:
        product_stmt = product_stmt.join(FactShipment, FactShipment.shipment_id == FactLoadingOrderLine.shipment_id).where(FactShipment.depot_id == depot_id)
    tag_ids = dashboard_tag_ids(db, depot_id)
    tag_type_count = 0
    if tag_ids:
        tag_type_count = db.scalar(select(func.count(func.distinct(MasterTag.tag_type_id))).where(MasterTag.tag_id.in_(tag_ids))) or 0
    return {
        "total_mt": db.scalar(select(func.count()).select_from(MasterMT).where(*mt_where)) or 0,
        "active_mt": db.scalar(select(func.count()).select_from(MasterMT).where(MasterMT.active_status == "ACTIVE", *mt_where)) or 0,
        "total_spbu": db.scalar(select(func.count()).select_from(MasterSPBU).where(*spbu_where)) or 0,
        "active_spbu": db.scalar(select(func.count()).select_from(MasterSPBU).where(MasterSPBU.active_status == "ACTIVE", *spbu_where)) or 0,
        "total_depot": 1 if depot_id else count(MasterDepot),
        "total_product": db.scalar(product_stmt) or 0,
        "total_canonical_tags": len(tag_ids) if depot_id else count(MasterTag),
        "total_tag_types": tag_type_count if depot_id else count(MasterTagType),
        "total_loading_order_lines": db.scalar(lo_stmt) or 0,
        "total_shipments": db.scalar(select(func.count()).select_from(FactShipment).where(*shipment_where)) or 0,
        "unique_mt_observed_in_lo": unique_mt_lo,
        "unique_spbu_observed_in_lo": unique_spbu_lo,
        "unmatched_mt": unmatched_mt,
        "unmatched_spbu": unmatched_spbu,
        "gps_events": count(StgGPSData),
        "gps_confirmed_spbu_visits": 0,
        "data_quality_issues": len(quality_issue_rows(db, depot_id)),
    }


@app.get("/api/v1/foundation/charts")
def foundation_charts(depot_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    depot_id = normalize_depot_filter(db, depot_id)
    def series(stmt):
        return [{"name": str(name or "UNKNOWN"), "value": value} for name, value in db.execute(stmt).all()]

    mt_chart_filters = [MasterMT.active_status != "DELETED"]
    spbu_chart_filters = [MasterSPBU.active_status != "DELETED"]
    if depot_id:
        mt_chart_filters.append(MasterMT.depot_id == depot_id)
        spbu_chart_filters.append(MasterSPBU.primary_depot_id == depot_id)

    mt_by_vehicle_stmt = select(MasterMT.vehicle_type_tag, func.count()).where(*mt_chart_filters).group_by(MasterMT.vehicle_type_tag).order_by(MasterMT.vehicle_type_tag)
    spbu_by_vehicle_stmt = select(MasterSPBU.vehicle_type_tag, func.count()).where(*spbu_chart_filters).group_by(MasterSPBU.vehicle_type_tag).order_by(MasterSPBU.vehicle_type_tag)
    mt_by_tag_stmt = select(MasterTag.tag_value, func.count(BridgeMTTag.mt_id)).join(BridgeMTTag, BridgeMTTag.tag_id == MasterTag.tag_id).join(MasterMT, MasterMT.mt_id == BridgeMTTag.mt_id).where(*mt_chart_filters).group_by(MasterTag.tag_value).order_by(desc(func.count(BridgeMTTag.mt_id))).limit(20)
    spbu_by_tag_stmt = select(MasterTag.tag_value, func.count(BridgeSPBUTag.spbu_id)).join(BridgeSPBUTag, BridgeSPBUTag.tag_id == MasterTag.tag_id).join(MasterSPBU, MasterSPBU.spbu_id == BridgeSPBUTag.spbu_id).where(*spbu_chart_filters).group_by(MasterTag.tag_value).order_by(desc(func.count(BridgeSPBUTag.spbu_id))).limit(20)
    product_distribution_stmt = select(MasterProduct.product_name, func.count(FactLoadingOrderLine.loading_order_number)).join(FactLoadingOrderLine, FactLoadingOrderLine.product_id == MasterProduct.product_id).join(FactShipment, FactShipment.shipment_id == FactLoadingOrderLine.shipment_id).group_by(MasterProduct.product_name).order_by(desc(func.count(FactLoadingOrderLine.loading_order_number)))
    if depot_id:
        product_distribution_stmt = product_distribution_stmt.where(FactShipment.depot_id == depot_id)
    mt_by_vehicle = series(mt_by_vehicle_stmt)
    spbu_by_vehicle = series(spbu_by_vehicle_stmt)
    mt_by_tag = series(mt_by_tag_stmt)
    spbu_by_tag = series(spbu_by_tag_stmt)
    product_distribution = series(product_distribution_stmt)
    stops_base = select(FactShipmentSPBU.shipment_id, func.count(FactShipmentSPBU.spbu_id).label("stops")).join(FactShipment, FactShipment.shipment_id == FactShipmentSPBU.shipment_id)
    if depot_id:
        stops_base = stops_base.where(FactShipment.depot_id == depot_id)
    stops_subquery = (
        stops_base
        .group_by(FactShipmentSPBU.shipment_id)
        .subquery()
    )
    spbu_per_shipment = series(select(stops_subquery.c.stops, func.count()).group_by(stops_subquery.c.stops).order_by(stops_subquery.c.stops))
    shipment_where = [FactShipment.depot_id == depot_id] if depot_id else []
    matched_spbu_stmt = select(func.count()).select_from(FactLoadingOrderLine).join(FactShipment, FactShipment.shipment_id == FactLoadingOrderLine.shipment_id).where(FactLoadingOrderLine.spbu_mapping_status == "MATCHED", *shipment_where)
    unmatched_spbu_stmt = select(func.count()).select_from(FactLoadingOrderLine).join(FactShipment, FactShipment.shipment_id == FactLoadingOrderLine.shipment_id).where(FactLoadingOrderLine.spbu_mapping_status != "MATCHED", *shipment_where)
    issues_by_severity = Counter(issue.severity for issue in quality_issue_rows(db, depot_id))
    mapping_coverage = [
        {"name": "MT matched", "value": db.scalar(select(func.count()).select_from(FactShipment).where(FactShipment.vehicle_mapping_status == "MATCHED", *shipment_where)) or 0},
        {"name": "MT unmatched", "value": db.scalar(select(func.count()).select_from(FactShipment).where(FactShipment.vehicle_mapping_status != "MATCHED", *shipment_where)) or 0},
        {"name": "SPBU matched", "value": db.scalar(matched_spbu_stmt) or 0},
        {"name": "SPBU unmatched", "value": db.scalar(unmatched_spbu_stmt) or 0},
    ]
    quality_by_severity = [{"name": severity, "value": value} for severity, value in sorted(issues_by_severity.items())]
    shipment_count = db.scalar(select(func.count()).select_from(FactShipment).where(*shipment_where)) or 0
    gps_reconstruction = [
        {"name": "full GPS sequence", "value": 0},
        {"name": "partial sequence", "value": 0},
        {"name": "no sequence", "value": shipment_count},
    ]
    coverage_tags = [
        {"name": "MT tag links", "value": db.scalar(select(func.count()).select_from(BridgeMTTag).join(MasterMT, MasterMT.mt_id == BridgeMTTag.mt_id).where(*mt_chart_filters)) or 0},
        {"name": "SPBU tag links", "value": db.scalar(select(func.count()).select_from(BridgeSPBUTag).join(MasterSPBU, MasterSPBU.spbu_id == BridgeSPBUTag.spbu_id).where(*spbu_chart_filters)) or 0},
    ]
    return {
        "mt_by_vehicle_type_tag": mt_by_vehicle,
        "spbu_by_vehicle_type_tag": spbu_by_vehicle,
        "mt_by_project_tag": mt_by_tag,
        "spbu_by_project_tag": spbu_by_tag,
        "mt_vs_spbu_tag_coverage": coverage_tags,
        "spbu_per_shipment_distribution": spbu_per_shipment,
        "product_distribution": product_distribution,
        "reference_mapping_coverage": mapping_coverage,
        "data_quality_issues_by_severity": quality_by_severity,
        "gps_reconstruction_coverage": gps_reconstruction,
    }


@app.get("/api/v1/master/mt")
def list_mt(limit: int = 100, offset: int = 0, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(MasterMT).order_by(MasterMT.vehicle_registration).offset(offset).limit(limit)).all()
    return [public(row) for row in rows]


@app.get("/api/v1/master/mt/{mt_id}")
def get_mt(mt_id: str, db: Session = Depends(get_db)) -> dict:
    mt = db.get(MasterMT, mt_id)
    if not mt:
        raise HTTPException(status_code=404, detail="MT not found")
    issues = db.scalars(select(DataQualityIssue).where(DataQualityIssue.entity_type == "MT", DataQualityIssue.entity_id == mt.vehicle_registration)).all()
    return {"mt": public(mt), "data_quality_issues": [public(issue) for issue in issues]}


@app.get("/api/v1/master/spbu")
def list_spbu(limit: int = 100, offset: int = 0, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(MasterSPBU).order_by(MasterSPBU.spbu_code).offset(offset).limit(limit)).all()
    sync_spbu_coordinates_from_source(db, rows)
    return [public(row) for row in rows]


@app.get("/api/v1/master/spbu/{spbu_id}")
def get_spbu(spbu_id: str, db: Session = Depends(get_db)) -> dict:
    spbu = db.get(MasterSPBU, spbu_id)
    if not spbu:
        raise HTTPException(status_code=404, detail="SPBU not found")
    sync_spbu_coordinates_from_source(db, [spbu])
    issue_count = db.scalar(select(func.count()).select_from(DataQualityIssue).where(DataQualityIssue.entity_type == "SPBU", DataQualityIssue.entity_id == spbu.spbu_code)) or 0
    compatible_count = 0
    for mt in db.scalars(select(MasterMT)).all():
        if evaluate_mt_spbu_compatibility(db, mt.mt_id, spbu_id, vehicle_mode=settings.vehicle_compatibility_mode)["compatible"]:
            compatible_count += 1
    return {"spbu": public(spbu), "compatible_mt_count": compatible_count, "data_quality_issue_count": issue_count}


@app.get("/api/v1/master/depots")
def list_depots(db: Session = Depends(get_db)) -> list[dict]:
    return [public(row) for row in db.scalars(select(MasterDepot).where(MasterDepot.active_status != "DELETED").order_by(MasterDepot.depot_name)).all()]


@app.get("/api/v1/exports/template")
def export_template(domain: str, file_format: str = "xlsx") -> StreamingResponse:
    normalized_domain = normalize_export_domain(domain)
    if normalized_domain not in IMPORT_TEMPLATE_COLUMNS:
        raise HTTPException(status_code=400, detail="Template is available for MOBIL_TANGKI, SPBU, LOADING_ORDER, or GPS.")
    normalized_format = normalize_file_format(file_format)
    rows = []
    filename_base = f"template_{normalized_domain.lower()}"
    if normalized_format == "csv":
        return csv_response(IMPORT_TEMPLATE_COLUMNS[normalized_domain], rows, f"{filename_base}.csv")
    return workbook_response([(EXPORT_DOMAIN_LABELS.get(normalized_domain, normalized_domain), IMPORT_TEMPLATE_COLUMNS[normalized_domain], rows)], f"{filename_base}.xlsx")


@app.get("/api/v1/exports/data")
def export_data(domain: str, depot_id: str, file_format: str = "xlsx", db: Session = Depends(get_db)) -> StreamingResponse:
    normalized_domain = normalize_export_domain(domain)
    normalized_format = normalize_file_format(file_format)
    depot = db.get(MasterDepot, depot_id)
    if not depot or depot.active_status == "DELETED":
        raise HTTPException(status_code=404, detail="Depot not found.")
    if normalized_domain not in EXPORT_DOMAIN_LABELS:
        raise HTTPException(status_code=400, detail="Export domain is not supported.")
    sheets = build_export_sheets(db, normalized_domain, depot)
    safe_depot = safe_filename(depot.depot_name or depot.depot_code or depot.depot_id)
    filename_base = f"export_{normalized_domain.lower()}_{safe_depot}"
    if normalized_format == "csv":
        if normalized_domain == "ALL":
            raise HTTPException(status_code=400, detail="CSV export supports one domain only. Use XLSX for ALL.")
        sheet_name, headers, rows = sheets[0]
        return csv_response(headers, rows, f"{filename_base}.csv")
    return workbook_response(sheets, f"{filename_base}.xlsx")


@app.get("/api/v1/master/products")
def list_products(db: Session = Depends(get_db)) -> list[dict]:
    return [public(row) for row in db.scalars(select(MasterProduct).where(MasterProduct.active_status != "DELETED").order_by(MasterProduct.product_name)).all()]


@app.get("/api/v1/master/tags")
def list_tags(db: Session = Depends(get_db)) -> list[dict]:
    return [public(row) for row in db.scalars(select(MasterTag).where(MasterTag.active_status != "DELETED").order_by(MasterTag.tag_value)).all()]


@app.get("/api/v1/master-crud/{domain}")
def crud_list_master(
    domain: str,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    search_column: str | None = None,
    sort_column: str | None = None,
    sort_direction: str = "asc",
    depot_id: str | None = None,
    active_status: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    normalized_domain = normalize_crud_domain(domain)
    limit = max(1, min(limit, 10000))
    offset = max(0, offset)
    stmt = crud_select_statement(normalized_domain)
    count_stmt = crud_count_statement(normalized_domain)
    stmt, count_stmt = apply_crud_filters(normalized_domain, stmt, count_stmt, search, search_column, depot_id, active_status)
    stmt = apply_crud_sort(normalized_domain, stmt, sort_column, sort_direction)
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.offset(offset).limit(limit)).all()
    if normalized_domain == "SPBU":
        sync_spbu_coordinates_from_source(db, rows)
    tag_types = tag_type_lookup(db) if normalized_domain in {"MOBIL_TANGKI", "SPBU", "TAG"} else None
    mt_tag_values = mt_tag_value_lookup(db, [row.mt_id for row in rows], tag_types or {}) if normalized_domain == "MOBIL_TANGKI" else None
    spbu_tag_values = spbu_tag_value_lookup(db, [row.spbu_id for row in rows], tag_types or {}) if normalized_domain == "SPBU" else None
    lo_shipment_values = loading_order_shipment_lookup(db, [row.shipment_id for row in rows]) if normalized_domain == "LOADING_ORDER" else None
    return {"domain": normalized_domain, "total": total, "limit": limit, "offset": offset, "rows": [serialize_crud_record(normalized_domain, row, tag_types, mt_tag_values, spbu_tag_values, lo_shipment_values) for row in rows]}


@app.post("/api/v1/master-crud/{domain}")
def crud_create_master(domain: str, payload: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    normalized_domain = normalize_crud_domain(domain)
    record = build_crud_record(normalized_domain, payload)
    reactivated = reactivate_deleted_crud_record(db, normalized_domain, record)
    if reactivated:
        apply_crud_tag_links(db, normalized_domain, reactivated, payload)
        return commit_crud(db, normalized_domain, reactivated)
    db.add(record)
    db.flush()
    apply_crud_tag_links(db, normalized_domain, record, payload)
    return commit_crud(db, normalized_domain, record)


@app.post("/api/v1/master-crud/{domain}/sync")
def crud_sync_master(domain: str, db: Session = Depends(get_db)) -> dict:
    normalized_domain = normalize_crud_domain(domain)
    if normalized_domain == "DEPOT":
        result = sync_depots_from_sources(db)
    elif normalized_domain == "PRODUCT":
        result = sync_products_from_sources(db)
    elif normalized_domain == "TAG":
        result = sync_tags_from_sources(db)
    else:
        raise HTTPException(status_code=400, detail="Sync is available only for DEPOT, PRODUCT, and TAG.")
    db.commit()
    return {"domain": normalized_domain, **result}


@app.put("/api/v1/master-crud/{domain}/{record_id}")
def crud_update_master(domain: str, record_id: str, payload: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    normalized_domain = normalize_crud_domain(domain)
    record = get_crud_record(db, normalized_domain, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Master data record not found.")
    apply_crud_update(normalized_domain, record, payload)
    apply_crud_tag_links(db, normalized_domain, record, payload)
    return commit_crud(db, normalized_domain, record)


@app.delete("/api/v1/master-crud/{domain}/{record_id}")
def crud_delete_master(domain: str, record_id: str, db: Session = Depends(get_db)) -> dict:
    normalized_domain = normalize_crud_domain(domain)
    record = get_crud_record(db, normalized_domain, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Master data record not found.")
    if hasattr(record, "active_status"):
        record.active_status = "DELETED"
        db.commit()
        tag_types = tag_type_lookup(db) if normalized_domain == "TAG" else None
        return {"status": "DELETED", "delete_mode": "SOFT", "record": serialize_crud_record(normalized_domain, record, tag_types)}
    if normalized_domain == "TAG_TYPE":
        used = db.scalar(select(func.count()).select_from(MasterTag).where(MasterTag.tag_type_id == record_id)) or 0
        if used:
            raise HTTPException(status_code=409, detail="Tag type is still referenced by canonical tags.")
    db.delete(record)
    db.commit()
    return {"status": "DELETED", "delete_mode": "HARD", "record_id": record_id}


@app.get("/api/v1/master/loading-orders")
def list_loading_orders(limit: int = 100, offset: int = 0, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(FactLoadingOrderLine).offset(offset).limit(limit)).all()
    return [public(row) for row in rows]


@app.get("/api/v1/shipments")
def list_shipments(limit: int = 100, offset: int = 0, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(FactShipment).order_by(FactShipment.source_shipment_id).offset(offset).limit(limit)).all()
    return [public(row) for row in rows]


@app.get("/api/v1/shipments/{shipment_id}")
def get_shipment(shipment_id: str, db: Session = Depends(get_db)) -> dict:
    shipment = db.get(FactShipment, shipment_id)
    if not shipment:
        shipment = db.scalar(select(FactShipment).where(FactShipment.source_shipment_id == shipment_id))
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    lines = db.scalars(select(FactLoadingOrderLine).where(FactLoadingOrderLine.shipment_id == shipment.shipment_id)).all()
    return {"shipment": public(shipment), "loading_orders": [public(line) for line in lines]}


@app.get("/api/v1/shipments/{shipment_id}/reconstruction")
def get_shipment_reconstruction(shipment_id: str, db: Session = Depends(get_db)) -> dict:
    payload = get_shipment(shipment_id, db)
    assigned = []
    for line in payload["loading_orders"]:
        if line["spbu_id"] and line["spbu_id"] not in [item["spbu_id"] for item in assigned]:
            spbu = db.get(MasterSPBU, line["spbu_id"])
            assigned.append({"spbu_id": line["spbu_id"], "spbu_code": spbu.spbu_code if spbu else line["source_spbu_code"], "source": "LO"})
    return {
        "shipment": payload["shipment"],
        "assigned_spbu": assigned,
        "gps_observed_spbu": [],
        "actual_stop_sequence": [],
        "reconciliation": "NO_GPS_SEQUENCE",
        "sequence_source": "UNKNOWN",
        "sequence_confidence": 0,
        "note": "Phase 0 GPS architecture is present; actual reconstruction starts when GPS_data mapping is supplied or seeded GPS scenarios are loaded.",
    }


@app.post("/api/v1/master/compatibility/check")
def compatibility_check(request: CompatibilityRequest, db: Session = Depends(get_db)) -> dict:
    return evaluate_mt_spbu_compatibility(db, request.mt_id, request.spbu_id, request.product_id, settings.vehicle_compatibility_mode)


@app.get("/api/v1/tag-consistency/analysis")
def tag_consistency_analysis(
    start_date: str | None = None,
    end_date: str | None = None,
    depot_id: str | None = None,
    spbu: str | None = None,
    vehicle: str | None = None,
    tag_type: str | None = None,
    overall_status: str | None = None,
    product_id: str | None = None,
    vehicle_class: int | None = None,
    search: str | None = None,
    sort_column: str = "loading_order_date",
    sort_direction: str = "desc",
    limit: int = 25,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> dict:
    return build_tag_consistency_payload(
        db,
        start_date=parse_iso_date_filter(start_date, "start_date"),
        end_date=parse_iso_date_filter(end_date, "end_date"),
        depot_id=normalize_depot_filter(db, depot_id),
        spbu=spbu,
        vehicle=vehicle,
        tag_type=tag_type,
        overall_status=overall_status,
        product_id=product_id,
        vehicle_class=vehicle_class,
        search=search,
        sort_column=sort_column,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
    )


@app.get("/api/v1/tag-consistency/analysis/{analysis_id}")
def tag_consistency_detail(analysis_id: str, db: Session = Depends(get_db)) -> dict:
    detail = get_tag_consistency_detail(db, analysis_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Tag consistency analysis not found.")
    return detail


@app.get("/api/v1/departure-intelligence/analysis")
def depot_departure_intelligence(
    depot_id: str,
    start_date: str,
    end_date: str,
    bucket_minutes: int = 30,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    sort_column: str = "observation_count",
    sort_direction: str = "desc",
    confidence_level: str | None = None,
    spbu_ids: str | None = None,
    profile_search: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    parsed_spbu_ids = [item.strip() for item in spbu_ids.split(",") if item.strip()] if spbu_ids is not None else None
    return build_departure_intelligence_payload(
        db,
        depot_id=depot_id,
        start_date=parse_iso_date_filter(start_date, "start_date"),
        end_date=parse_iso_date_filter(end_date, "end_date"),
        bucket_minutes=bucket_minutes,
        limit=max(1, min(limit, 100)),
        offset=max(0, offset),
        search=search,
        sort_column=sort_column,
        sort_direction=sort_direction,
        confidence_level=confidence_level,
        spbu_ids=parsed_spbu_ids,
        profile_search=profile_search,
    )


@app.get("/api/v1/departure-intelligence/available-dates")
def depot_departure_available_dates(depot_id: str, db: Session = Depends(get_db)) -> dict:
    return build_departure_date_availability(db, depot_id)


@app.post("/api/v1/departure-intelligence/shift-analysis")
def depot_departure_shift_intelligence(request: ShiftAnalysisRequest, db: Session = Depends(get_db)) -> dict:
    return build_shift_intelligence_payload(
        db,
        depot_id=request.depot_id,
        start_date=parse_iso_date_filter(request.start_date, "start_date"),
        end_date=parse_iso_date_filter(request.end_date, "end_date"),
        bucket_minutes=request.bucket_minutes,
        shifts=[shift.model_dump() for shift in request.shifts],
        assignment_method=request.assignment_method,
        search=request.search,
        sort_column=request.sort_column,
        sort_direction=request.sort_direction,
    )


@app.get("/api/v1/pairing-intelligence/analysis")
def spbu_pairing_intelligence(
    depot_id: str,
    start_date: str,
    end_date: str,
    product_id: str | None = None,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    sort_column: str = "evidence_strength",
    sort_direction: str = "desc",
    selected_spbu_id: str | None = None,
    evidence_spbu_a_id: str | None = None,
    evidence_spbu_b_id: str | None = None,
    matrix_limit: int = 30,
    network_limit: int = 40,
    db: Session = Depends(get_db),
) -> dict:
    return build_pairing_intelligence_payload(
        db,
        depot_id=depot_id,
        start_date=parse_iso_date_filter(start_date, "start_date"),
        end_date=parse_iso_date_filter(end_date, "end_date"),
        product_id=product_id or None,
        limit=max(1, min(limit, 100)),
        offset=max(0, offset),
        search=search,
        sort_column=sort_column,
        sort_direction=sort_direction,
        selected_spbu_id=selected_spbu_id,
        evidence_spbu_a_id=evidence_spbu_a_id,
        evidence_spbu_b_id=evidence_spbu_b_id,
        matrix_limit=max(2, min(matrix_limit, 60)),
        network_limit=max(5, min(network_limit, 100)),
    )


@app.get("/api/v1/pairing-intelligence/available-dates")
def spbu_pairing_available_dates(depot_id: str, db: Session = Depends(get_db)) -> dict:
    return build_pairing_date_availability(db, depot_id)


@app.get("/api/v1/affinity-intelligence/analysis")
def spbu_mt_affinity_intelligence(
    depot_id: str,
    start_date: str,
    end_date: str,
    product_id: str | None = None,
    minimum_observations: int = 1,
    confidence: str = "ALL",
    temporal_bucket: str = "AUTO",
    recent_days: int = 7,
    top_n: int = 5,
    selected_spbu_id: str | None = None,
    selected_mt_id: str | None = None,
    edge_metric: str = "SHIPMENT_COUNT",
    network_limit: int = 100,
    db: Session = Depends(get_db),
) -> dict:
    return build_affinity_intelligence_payload(
        db,
        depot_id=depot_id,
        start_date=parse_iso_date_filter(start_date, "start_date"),
        end_date=parse_iso_date_filter(end_date, "end_date"),
        product_id=product_id or None,
        minimum_observations=max(1, minimum_observations),
        confidence_filter=confidence,
        temporal_bucket=temporal_bucket,
        recent_days=max(1, min(recent_days, 365)),
        top_n=max(0, min(top_n, 100)),
        selected_spbu_id=selected_spbu_id,
        selected_mt_id=selected_mt_id,
        edge_metric=edge_metric,
        network_limit=max(5, min(network_limit, 250)),
    )


@app.get("/api/v1/affinity-intelligence/available-dates")
def spbu_mt_affinity_available_dates(depot_id: str, db: Session = Depends(get_db)) -> dict:
    return build_affinity_date_availability(db, depot_id)


@app.get("/api/v1/master/compatibility/summary")
def compatibility_summary(depot_id: str | None = None, limit: int = 100, db: Session = Depends(get_db)) -> dict:
    mt_stmt = select(MasterMT)
    spbu_stmt = select(MasterSPBU)
    if depot_id:
        mt_stmt = mt_stmt.where(MasterMT.depot_id == depot_id)
        spbu_stmt = spbu_stmt.where(MasterSPBU.primary_depot_id == depot_id)
    mts = db.scalars(mt_stmt.limit(500)).all()
    spbus = db.scalars(spbu_stmt.limit(1000)).all()
    mt_tags: dict[str, set[str]] = {mt.mt_id: set() for mt in mts}
    spbu_tags: dict[str, set[str]] = {spbu.spbu_id: set() for spbu in spbus}
    mt_ids = list(mt_tags)
    spbu_ids = list(spbu_tags)
    if mt_ids:
        for mt_id, tag_id in db.execute(select(BridgeMTTag.mt_id, BridgeMTTag.tag_id).where(BridgeMTTag.mt_id.in_(mt_ids))).all():
            mt_tags.setdefault(mt_id, set()).add(tag_id)
    if spbu_ids:
        for spbu_id, tag_id in db.execute(select(BridgeSPBUTag.spbu_id, BridgeSPBUTag.tag_id).where(BridgeSPBUTag.spbu_id.in_(spbu_ids))).all():
            spbu_tags.setdefault(spbu_id, set()).add(tag_id)
    tag_values = {tag.tag_id: tag.tag_value for tag in db.scalars(select(MasterTag)).all()}
    compatible = incompatible = insufficient = 0
    examples = []
    for mt in mts:
        for spbu in spbus:
            failed_rules: list[str] = []
            warnings: list[str] = []
            if settings.vehicle_compatibility_mode == "MT_CAPACITY_LE_SPBU_LIMIT":
                try:
                    vehicle_ok = float(mt.vehicle_type_tag or 0) <= float(spbu.vehicle_type_tag or 0)
                except ValueError:
                    vehicle_ok = False
            else:
                vehicle_ok = bool(mt.vehicle_type_tag and spbu.vehicle_type_tag and str(mt.vehicle_type_tag) == str(spbu.vehicle_type_tag))
            if not vehicle_ok:
                failed_rules.append("VEHICLE_TYPE")
            missing_tags = spbu_tags.get(spbu.spbu_id, set()) - mt_tags.get(mt.mt_id, set())
            if missing_tags:
                failed_rules.append("PROJECT_TAGS")
            if mt.depot_id and spbu.primary_depot_id:
                depot_check = "PASS" if mt.depot_id == spbu.primary_depot_id else "FAIL"
                if depot_check == "FAIL":
                    failed_rules.append("DEPOT")
            else:
                depot_check = "INSUFFICIENT_DATA"
                warnings.append("Depot is missing on MT or SPBU.")
            is_compatible = not failed_rules or failed_rules == []
            is_compatible = vehicle_ok and not missing_tags and depot_check in {"PASS", "INSUFFICIENT_DATA"}
            if is_compatible:
                compatible += 1
            elif depot_check == "INSUFFICIENT_DATA":
                insufficient += 1
            else:
                incompatible += 1
            if len(examples) < limit:
                matched_tags = [tag_values[tag_id] for tag_id in sorted(mt_tags.get(mt.mt_id, set()) & spbu_tags.get(spbu.spbu_id, set())) if tag_id in tag_values]
                explanation = "Compatible by active Phase 0 master rules." if is_compatible else f"Incompatible by: {', '.join(failed_rules)}."
                examples.append({
                    "mt_id": mt.mt_id,
                    "vehicle_registration": mt.vehicle_registration,
                    "spbu_id": spbu.spbu_id,
                    "spbu_code": spbu.spbu_code,
                    "compatible": is_compatible,
                    "vehicle_type_check": "PASS" if vehicle_ok else "FAIL",
                    "project_tag_check": "PASS" if not missing_tags else "FAIL",
                    "product_check": "NOT_AVAILABLE",
                    "depot_check": depot_check,
                    "matched_tags": matched_tags,
                    "failed_rules": failed_rules,
                    "warnings": warnings,
                    "explanation": explanation,
                })
    return {"compatible": compatible, "incompatible": incompatible, "insufficient_data": insufficient, "examples": examples}


@app.get("/api/v1/data-quality/issues")
def list_quality_issues(severity: str | None = None, depot_id: str | None = None, limit: int = 200, db: Session = Depends(get_db)) -> list[dict]:
    depot_id = normalize_depot_filter(db, depot_id)
    rows = quality_issue_rows(db, depot_id, severity=severity)
    rows = sorted(rows, key=lambda issue: issue.detected_at, reverse=True)
    return [public(row) for row in rows[:limit]]


@app.get("/api/v1/tag-intelligence/anomalies")
def phase_placeholder_anomalies() -> dict:
    return {"phase": 1, "status": "NOT_STARTED", "reason": "Phase 0 completion gate must pass before Phase 1 analytics are implemented."}


@app.get("/api/v1/tag-intelligence/recommendations")
def phase_placeholder_recommendations() -> dict:
    return {"phase": 1, "status": "NOT_STARTED"}


@app.get("/api/v1/network/nodes")
def network_nodes() -> dict:
    return {"phase": 6, "status": "NOT_STARTED", "reason": "Network explorer depends on Phase 1-5 derived facts."}


@app.get("/api/v1/network/edges")
def network_edges() -> dict:
    return {"phase": 6, "status": "NOT_STARTED"}


def normalize_depot_filter(db: Session, depot_id: str | None) -> str | None:
    if not depot_id or depot_id == "ALL":
        return None
    if not db.get(MasterDepot, depot_id):
        raise HTTPException(status_code=404, detail="Depot filter not found.")
    return depot_id


def parse_iso_date_filter(value: str | None, field_name: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must use YYYY-MM-DD format.") from exc


def dashboard_tag_ids(db: Session, depot_id: str | None) -> set[str]:
    if not depot_id:
        return {tag_id for (tag_id,) in db.execute(select(MasterTag.tag_id)).all()}
    mt_tag_ids = {
        tag_id
        for (tag_id,) in db.execute(
            select(BridgeMTTag.tag_id)
            .join(MasterMT, MasterMT.mt_id == BridgeMTTag.mt_id)
            .where(MasterMT.depot_id == depot_id)
        ).all()
    }
    spbu_tag_ids = {
        tag_id
        for (tag_id,) in db.execute(
            select(BridgeSPBUTag.tag_id)
            .join(MasterSPBU, MasterSPBU.spbu_id == BridgeSPBUTag.spbu_id)
            .where(MasterSPBU.primary_depot_id == depot_id)
        ).all()
    }
    return mt_tag_ids | spbu_tag_ids


def quality_issue_rows(db: Session, depot_id: str | None, severity: str | None = None) -> list[DataQualityIssue]:
    stmt = select(DataQualityIssue)
    if severity:
        stmt = stmt.where(DataQualityIssue.severity == severity)
    issues = db.scalars(stmt).all()
    if not depot_id:
        return list(issues)
    mt_registrations = {
        value
        for (value,) in db.execute(select(MasterMT.vehicle_registration).where(MasterMT.depot_id == depot_id, MasterMT.vehicle_registration.is_not(None))).all()
    }
    spbu_codes = {
        value
        for (value,) in db.execute(select(MasterSPBU.spbu_code).where(MasterSPBU.primary_depot_id == depot_id)).all()
    }
    shipment_ids = {
        value
        for (value,) in db.execute(select(FactShipment.source_shipment_id).where(FactShipment.depot_id == depot_id)).all()
    }
    lo_numbers = {
        value
        for (value,) in db.execute(
            select(FactLoadingOrderLine.loading_order_number)
            .join(FactShipment, FactShipment.shipment_id == FactLoadingOrderLine.shipment_id)
            .where(FactShipment.depot_id == depot_id, FactLoadingOrderLine.loading_order_number.is_not(None))
        ).all()
    }
    allowed = {
        "MT": mt_registrations,
        "SPBU": spbu_codes,
        "SHIPMENT": shipment_ids,
        "LO_LINE": lo_numbers,
    }
    return [issue for issue in issues if issue.entity_id in allowed.get(issue.entity_type, set())]


def normalize_crud_domain(domain: str) -> str:
    aliases = {
        "MT": "MOBIL_TANGKI",
        "MOBIL_TANGKI": "MOBIL_TANGKI",
        "SPBU": "SPBU",
        "LO": "LOADING_ORDER",
        "LOADING_ORDER": "LOADING_ORDER",
        "LOADING_ORDERS": "LOADING_ORDER",
        "DEPOT": "DEPOT",
        "PRODUCT": "PRODUCT",
        "TAG": "TAG",
        "TAG_TYPE": "TAG_TYPE",
    }
    normalized = aliases.get(domain.upper())
    if not normalized:
        raise HTTPException(status_code=400, detail="Supported CRUD domains: MOBIL_TANGKI, SPBU, LOADING_ORDER, DEPOT, PRODUCT, TAG, TAG_TYPE.")
    return normalized


def crud_model_and_key(domain: str):
    mapping = {
        "MOBIL_TANGKI": (MasterMT, MasterMT.mt_id),
        "SPBU": (MasterSPBU, MasterSPBU.spbu_id),
        "LOADING_ORDER": (FactLoadingOrderLine, FactLoadingOrderLine.loading_order_number),
        "DEPOT": (MasterDepot, MasterDepot.depot_id),
        "PRODUCT": (MasterProduct, MasterProduct.product_id),
        "TAG": (MasterTag, MasterTag.tag_id),
        "TAG_TYPE": (MasterTagType, MasterTagType.tag_type_id),
    }
    return mapping[domain]


def encode_loading_order_record_id(loading_order_number: str, source_depot_name: str) -> str:
    payload = json.dumps([loading_order_number, source_depot_name], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_loading_order_record_id(record_id: str) -> tuple[str, str]:
    try:
        padded = record_id + "=" * (-len(record_id) % 4)
        values = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Loading Order record id.") from exc
    if not isinstance(values, list) or len(values) != 2 or not all(isinstance(value, str) and value for value in values):
        raise HTTPException(status_code=400, detail="Invalid Loading Order record id.")
    return values[0], values[1]


def get_crud_record(db: Session, domain: str, record_id: str):
    model, _ = crud_model_and_key(domain)
    if domain == "LOADING_ORDER":
        return db.get(model, decode_loading_order_record_id(record_id))
    return db.get(model, record_id)


def tag_type_lookup(db: Session) -> dict[str, MasterTagType]:
    return {tag_type.tag_type_id: tag_type for tag_type in db.scalars(select(MasterTagType)).all()}


def tag_type_column_key(code: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", code.lower()).strip("_")
    return f"tag_{normalized}"


def mt_tag_value_lookup(db: Session, mt_ids: list[str], tag_types: dict[str, MasterTagType]) -> dict[str, dict[str, str]]:
    if not mt_ids:
        return {}
    grouped: dict[str, dict[str, list[str]]] = {mt_id: {} for mt_id in mt_ids}
    rows = db.execute(
        select(BridgeMTTag.mt_id, MasterTag.tag_type_id, MasterTag.tag_value)
        .join(MasterTag, MasterTag.tag_id == BridgeMTTag.tag_id)
        .where(BridgeMTTag.mt_id.in_(mt_ids))
        .order_by(BridgeMTTag.mt_id, MasterTag.tag_value)
    ).all()
    for mt_id, tag_type_id, tag_value in rows:
        tag_type = tag_types.get(tag_type_id)
        if not tag_type:
            continue
        key = tag_type_column_key(tag_type.code)
        grouped.setdefault(mt_id, {}).setdefault(key, []).append(tag_value)
    return {
        mt_id: {key: ", ".join(values) for key, values in type_values.items()}
        for mt_id, type_values in grouped.items()
    }


def spbu_tag_value_lookup(db: Session, spbu_ids: list[str], tag_types: dict[str, MasterTagType]) -> dict[str, dict[str, str]]:
    if not spbu_ids:
        return {}
    grouped: dict[str, dict[str, list[str]]] = {spbu_id: {} for spbu_id in spbu_ids}
    rows = db.execute(
        select(BridgeSPBUTag.spbu_id, MasterTag.tag_type_id, MasterTag.tag_value)
        .join(MasterTag, MasterTag.tag_id == BridgeSPBUTag.tag_id)
        .where(BridgeSPBUTag.spbu_id.in_(spbu_ids))
        .order_by(BridgeSPBUTag.spbu_id, MasterTag.tag_value)
    ).all()
    for spbu_id, tag_type_id, tag_value in rows:
        tag_type = tag_types.get(tag_type_id)
        if not tag_type:
            continue
        key = tag_type_column_key(tag_type.code)
        grouped.setdefault(spbu_id, {}).setdefault(key, []).append(tag_value)
    return {
        spbu_id: {key: ", ".join(values) for key, values in type_values.items()}
        for spbu_id, type_values in grouped.items()
    }


def loading_order_shipment_lookup(db: Session, shipment_ids: list[str]) -> dict[str, FactShipment]:
    unique_ids = sorted({shipment_id for shipment_id in shipment_ids if shipment_id})
    if not unique_ids:
        return {}
    return {
        shipment.shipment_id: shipment
        for shipment in db.scalars(select(FactShipment).where(FactShipment.shipment_id.in_(unique_ids))).all()
    }


def serialize_crud_record(
    domain: str,
    record,
    tag_types: dict[str, MasterTagType] | None = None,
    mt_tag_values: dict[str, dict[str, str]] | None = None,
    spbu_tag_values: dict[str, dict[str, str]] | None = None,
    lo_shipment_values: dict[str, FactShipment] | None = None,
) -> dict:
    data = public(record)
    if domain == "LOADING_ORDER":
        data["crud_record_id"] = encode_loading_order_record_id(record.loading_order_number, record.source_depot_name)
        shipment = (lo_shipment_values or {}).get(record.shipment_id)
        data["vehicle_registration"] = shipment.vehicle_registration if shipment else None
        data["validation_datetime"] = shipment.validation_datetime if shipment else None
        data["validation_date"] = shipment.validation_datetime.date() if shipment and shipment.validation_datetime else None
        data["validation_time"] = shipment.validation_datetime.time().replace(microsecond=0) if shipment and shipment.validation_datetime else None
    if domain == "MOBIL_TANGKI":
        for tag_type in (tag_types or {}).values():
            data[tag_type_column_key(tag_type.code)] = None
        data.update((mt_tag_values or {}).get(record.mt_id, {}))
        data["tag_vehicle_class"] = record.vehicle_type_tag
    if domain == "SPBU":
        for tag_type in (tag_types or {}).values():
            data[tag_type_column_key(tag_type.code)] = None
        data.update((spbu_tag_values or {}).get(record.spbu_id, {}))
        data["tag_vehicle_class"] = record.vehicle_type_tag
    if domain == "TAG":
        tag_type = (tag_types or {}).get(record.tag_type_id)
        data["tag_type_code"] = tag_type.code if tag_type else ""
        data["tag_type_name"] = tag_type.name if tag_type else ""
    return data


def crud_select_statement(domain: str):
    model, _ = crud_model_and_key(domain)
    return select(model)


def crud_count_statement(domain: str):
    model, _ = crud_model_and_key(domain)
    return select(func.count()).select_from(model)


def crud_default_sort_columns(domain: str) -> tuple:
    default_columns = {
        "MOBIL_TANGKI": MasterMT.vehicle_registration,
        "SPBU": MasterSPBU.spbu_code,
        "LOADING_ORDER": (FactLoadingOrderLine.source_depot_name, FactLoadingOrderLine.loading_order_number),
        "DEPOT": MasterDepot.depot_name,
        "PRODUCT": MasterProduct.product_name,
        "TAG": MasterTag.tag_value,
        "TAG_TYPE": MasterTagType.code,
    }[domain]
    return default_columns if isinstance(default_columns, tuple) else (default_columns,)


def crud_search_columns(domain: str) -> dict[str, object]:
    return {
        "MOBIL_TANGKI": {
            "vehicle_registration": MasterMT.vehicle_registration,
            "vehicle_name_raw": MasterMT.vehicle_name_raw,
            "tag_vehicle_class": MasterMT.vehicle_type_tag,
            "vehicle_type_tag": MasterMT.vehicle_type_tag,
            "capacity_label": MasterMT.capacity_label,
            "number_of_compartments": MasterMT.number_of_compartments,
            "active_status": MasterMT.active_status,
        },
        "SPBU": {
            "spbu_code": MasterSPBU.spbu_code,
            "city": MasterSPBU.city,
            "tag_vehicle_class": MasterSPBU.vehicle_type_tag,
            "vehicle_type_tag": MasterSPBU.vehicle_type_tag,
            "latitude": MasterSPBU.latitude,
            "longitude": MasterSPBU.longitude,
            "active_status": MasterSPBU.active_status,
        },
        "LOADING_ORDER": {
            "loading_order_number": FactLoadingOrderLine.loading_order_number,
            "source_depot_name": FactLoadingOrderLine.source_depot_name,
            "shipment_id": FactLoadingOrderLine.shipment_id,
            "vehicle_registration": FactShipment.vehicle_registration,
            "validation_datetime": FactShipment.validation_datetime,
            "validation_date": func.date(FactShipment.validation_datetime),
            "validation_time": cast(FactShipment.validation_datetime, Time),
            "source_spbu_code": FactLoadingOrderLine.source_spbu_code,
            "shipto": FactLoadingOrderLine.shipto,
            "source_product_name": FactLoadingOrderLine.source_product_name,
            "quantity": FactLoadingOrderLine.quantity,
            "status": FactLoadingOrderLine.status,
        },
        "DEPOT": {
            "depot_code": MasterDepot.depot_code,
            "depot_name": MasterDepot.depot_name,
            "latitude": MasterDepot.latitude,
            "longitude": MasterDepot.longitude,
            "region": MasterDepot.region,
            "timezone": MasterDepot.timezone,
            "active_status": MasterDepot.active_status,
        },
        "PRODUCT": {
            "product_name": MasterProduct.product_name,
            "normalized_product": MasterProduct.normalized_product,
            "active_status": MasterProduct.active_status,
        },
        "TAG": {
            "tag_value": MasterTag.tag_value,
            "normalized_tag": MasterTag.normalized_tag,
            "tag_type_code": MasterTagType.code,
            "active_status": MasterTag.active_status,
        },
        "TAG_TYPE": {
            "code": MasterTagType.code,
            "name": MasterTagType.name,
            "description": MasterTagType.description,
            "admin_editable": MasterTagType.admin_editable,
        },
    }[domain]


def crud_tag_sort_expression(domain: str, sort_column: str):
    tag_type_code = sort_column.removeprefix("tag_").upper()
    if domain == "MOBIL_TANGKI":
        if tag_type_code == "VEHICLE_CLASS":
            return MasterMT.vehicle_type_tag
        return (
            select(func.aggregate_strings(MasterTag.tag_value, ", "))
            .select_from(BridgeMTTag)
            .join(MasterTag, MasterTag.tag_id == BridgeMTTag.tag_id)
            .join(MasterTagType, MasterTagType.tag_type_id == MasterTag.tag_type_id)
            .where(BridgeMTTag.mt_id == MasterMT.mt_id, MasterTagType.code == tag_type_code)
            .correlate(MasterMT)
            .scalar_subquery()
        )
    if domain == "SPBU":
        if tag_type_code == "VEHICLE_CLASS":
            return MasterSPBU.vehicle_type_tag
        return (
            select(func.aggregate_strings(MasterTag.tag_value, ", "))
            .select_from(BridgeSPBUTag)
            .join(MasterTag, MasterTag.tag_id == BridgeSPBUTag.tag_id)
            .join(MasterTagType, MasterTagType.tag_type_id == MasterTag.tag_type_id)
            .where(BridgeSPBUTag.spbu_id == MasterSPBU.spbu_id, MasterTagType.code == tag_type_code)
            .correlate(MasterSPBU)
            .scalar_subquery()
        )
    return None


def crud_sort_expression(domain: str, sort_column: str | None):
    if not sort_column:
        return None
    columns = crud_search_columns(domain)
    if sort_column in columns:
        if domain == "TAG" and sort_column == "tag_type_code":
            return select(MasterTagType.code).where(MasterTagType.tag_type_id == MasterTag.tag_type_id).scalar_subquery()
        return columns[sort_column]
    if domain in {"MOBIL_TANGKI", "SPBU"} and sort_column.startswith("tag_"):
        return crud_tag_sort_expression(domain, sort_column)
    raise HTTPException(status_code=400, detail=f"sort_column must be one of: {', '.join(columns)}.")


def apply_crud_sort(domain: str, stmt, sort_column: str | None, sort_direction: str):
    direction = (sort_direction or "asc").lower()
    if direction not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="sort_direction must be asc or desc.")
    sort_expr = crud_sort_expression(domain, sort_column)
    if sort_expr is None:
        return stmt.order_by(*crud_default_sort_columns(domain))
    primary_order = sort_expr.desc() if direction == "desc" else sort_expr.asc()
    return stmt.order_by(primary_order, *crud_default_sort_columns(domain))


def apply_column_search_filter(domain: str, stmt, count_stmt, filters: list, search: str | None, search_column: str | None):
    if not search or not search.strip():
        return stmt, count_stmt
    columns = crud_search_columns(domain)
    column_key = search_column if search_column in columns else None
    if domain == "MOBIL_TANGKI" and not column_key and search_column and search_column.startswith("tag_"):
        tag_type_code = search_column.removeprefix("tag_").upper()
        filters.append(
            MasterMT.mt_id.in_(
                select(BridgeMTTag.mt_id)
                .join(MasterTag, MasterTag.tag_id == BridgeMTTag.tag_id)
                .join(MasterTagType, MasterTagType.tag_type_id == MasterTag.tag_type_id)
                .where(MasterTagType.code == tag_type_code, MasterTag.tag_value.ilike(f"%{search.strip()}%"))
            )
        )
        return stmt, count_stmt
    if domain == "SPBU" and not column_key and search_column and search_column.startswith("tag_"):
        tag_type_code = search_column.removeprefix("tag_").upper()
        filters.append(
            MasterSPBU.spbu_id.in_(
                select(BridgeSPBUTag.spbu_id)
                .join(MasterTag, MasterTag.tag_id == BridgeSPBUTag.tag_id)
                .join(MasterTagType, MasterTagType.tag_type_id == MasterTag.tag_type_id)
                .where(MasterTagType.code == tag_type_code, MasterTag.tag_value.ilike(f"%{search.strip()}%"))
            )
        )
        return stmt, count_stmt
    if not column_key:
        raise HTTPException(status_code=400, detail=f"search_column must be one of: {', '.join(columns)}.")
    if domain == "TAG" and column_key == "tag_type_code":
        stmt = stmt.outerjoin(MasterTagType, MasterTagType.tag_type_id == MasterTag.tag_type_id)
        count_stmt = count_stmt.outerjoin(MasterTagType, MasterTagType.tag_type_id == MasterTag.tag_type_id)
    filters.append(cast(columns[column_key], String).ilike(f"%{search.strip()}%"))
    return stmt, count_stmt


def apply_crud_filters(domain: str, stmt, count_stmt, search: str | None, search_column: str | None, depot_id: str | None, active_status: str | None):
    filters = []
    if domain == "MOBIL_TANGKI":
        if depot_id and depot_id != "ALL":
            filters.append(MasterMT.depot_id == depot_id)
        if active_status:
            filters.append(MasterMT.active_status == active_status)
        else:
            filters.append(MasterMT.active_status != "DELETED")
    elif domain == "SPBU":
        if depot_id and depot_id != "ALL":
            filters.append(MasterSPBU.primary_depot_id == depot_id)
        if active_status:
            filters.append(MasterSPBU.active_status == active_status)
        else:
            filters.append(MasterSPBU.active_status != "DELETED")
    elif domain == "LOADING_ORDER":
        stmt = stmt.join(FactShipment, FactShipment.shipment_id == FactLoadingOrderLine.shipment_id)
        count_stmt = count_stmt.join(FactShipment, FactShipment.shipment_id == FactLoadingOrderLine.shipment_id)
        if depot_id and depot_id != "ALL":
            filters.append(FactShipment.depot_id == depot_id)
        if active_status:
            filters.append(FactLoadingOrderLine.status == active_status)
    elif domain == "DEPOT":
        if active_status:
            filters.append(MasterDepot.active_status == active_status)
        else:
            filters.append(MasterDepot.active_status != "DELETED")
    elif domain == "PRODUCT":
        if active_status:
            filters.append(MasterProduct.active_status == active_status)
        else:
            filters.append(MasterProduct.active_status != "DELETED")
    elif domain == "TAG":
        if active_status:
            filters.append(MasterTag.active_status == active_status)
        else:
            filters.append(MasterTag.active_status != "DELETED")
    stmt, count_stmt = apply_column_search_filter(domain, stmt, count_stmt, filters, search, search_column)
    return stmt.where(*filters), count_stmt.where(*filters)


def build_crud_record(domain: str, payload: dict):
    if domain == "DEPOT":
        depot_name = required_text(payload, "depot_name")
        depot_code = clean_str(payload.get("depot_code")) or normalize_key(depot_name)
        return MasterDepot(
            depot_id=make_id("depot", depot_code or depot_name),
            depot_code=depot_code,
            depot_name=depot_name,
            latitude=source_number(payload.get("latitude")),
            longitude=source_number(payload.get("longitude")),
            region=clean_str(payload.get("region")),
            timezone=clean_str(payload.get("timezone")) or "Asia/Jakarta",
            active_status=clean_str(payload.get("active_status")) or "ACTIVE",
        )
    if domain == "PRODUCT":
        product_name = required_text(payload, "product_name")
        normalized = normalize_product(product_name) or normalize_key(product_name) or product_name.upper()
        return MasterProduct(product_id=make_id("product", normalized), product_name=product_name, normalized_product=normalized, active_status=clean_str(payload.get("active_status")) or "ACTIVE")
    if domain == "TAG_TYPE":
        code = required_text(payload, "code").upper()
        return MasterTagType(tag_type_id=make_id("tagtype", code), code=code, name=required_text(payload, "name"), description=clean_str(payload.get("description")), admin_editable=bool(payload.get("admin_editable", True)))
    if domain == "TAG":
        tag_value = required_text(payload, "tag_value")
        normalized = normalize_key(tag_value) or tag_value.upper()
        tag_type_id = clean_str(payload.get("tag_type_id")) or make_id("tagtype", infer_tag_type(tag_value))
        return MasterTag(tag_id=make_id("tag", normalized), tag_type_id=tag_type_id, tag_value=tag_value, normalized_tag=normalized, active_status=clean_str(payload.get("active_status")) or "ACTIVE")
    if domain == "MOBIL_TANGKI":
        raw_name = required_text(payload, "vehicle_name_raw")
        registration = clean_str(payload.get("vehicle_registration"))
        capacity = clean_str(payload.get("capacity_label"))
        if not registration:
            registration, capacity, _ = parse_mt_name(raw_name)
        return MasterMT(
            mt_id=make_id("mt", registration or raw_name),
            source_mt_id=clean_str(payload.get("source_mt_id")),
            vehicle_name_raw=raw_name,
            vehicle_registration=registration,
            capacity_label=capacity,
            vehicle_type_tag=source_int(payload.get("vehicle_type_tag")),
            project_tag_raw=clean_str(payload.get("project_tag_raw")),
            number_of_compartments=source_int(payload.get("number_of_compartments")),
            large_vehicle_profile_status="NOT_REQUIRED",
            depot_id=clean_str(payload.get("depot_id")),
            source_hub_id=clean_str(payload.get("source_hub_id")),
            assignee=clean_str(payload.get("assignee")),
            active_status=clean_str(payload.get("active_status")) or "ACTIVE",
        )
    if domain == "SPBU":
        code = required_text(payload, "spbu_code")
        source_coordinate = clean_str(payload.get("source_coordinate"))
        latitude = source_number(payload.get("latitude"))
        longitude = source_number(payload.get("longitude"))
        if source_coordinate and (latitude is None or longitude is None):
            parsed_latitude, parsed_longitude, coordinate_messages = parse_coordinate(source_coordinate)
            if not coordinate_messages:
                latitude = parsed_latitude
                longitude = parsed_longitude
        return MasterSPBU(
            spbu_id=make_id("spbu", code),
            spbu_code=code,
            spbu_name=clean_str(payload.get("spbu_name")) or code,
            address=clean_str(payload.get("address")),
            city=clean_str(payload.get("city")),
            latitude=latitude,
            longitude=longitude,
            source_coordinate=source_coordinate,
            master_distance_km=source_number(payload.get("master_distance_km")),
            master_travel_time_min=source_number(payload.get("master_travel_time_min")),
            vehicle_type_tag=source_int(payload.get("vehicle_type_tag")),
            project_tag_raw=clean_str(payload.get("project_tag_raw")),
            primary_depot_id=clean_str(payload.get("primary_depot_id")),
            active_status=clean_str(payload.get("active_status")) or "ACTIVE",
        )
    if domain == "LOADING_ORDER":
        source_depot_name = required_text(payload, "source_depot_name")
        return FactLoadingOrderLine(
            loading_order_number=required_text(payload, "loading_order_number"),
            source_depot_name=source_depot_name,
            shipment_id=required_text(payload, "shipment_id"),
            spbu_id=clean_str(payload.get("spbu_id")),
            spbu_mapping_status=clean_str(payload.get("spbu_mapping_status")) or "UNMATCHED",
            source_spbu_code=clean_str(payload.get("source_spbu_code")),
            shipto=clean_str(payload.get("shipto")),
            product_id=clean_str(payload.get("product_id")),
            source_product_name=clean_str(payload.get("source_product_name")),
            quantity=source_number(payload.get("quantity")),
            status=clean_str(payload.get("status")),
            source_distance_km=source_number(payload.get("source_distance_km")),
            actual_km=source_number(payload.get("actual_km")),
            source_import_id=clean_str(payload.get("source_import_id")),
        )
    raise HTTPException(status_code=400, detail="Unsupported CRUD domain.")


def apply_crud_update(domain: str, record, payload: dict) -> None:
    allowed_fields = {
        "DEPOT": ["depot_code", "depot_name", "latitude", "longitude", "region", "timezone", "active_status"],
        "PRODUCT": ["product_name", "active_status"],
        "TAG_TYPE": ["code", "name", "description", "admin_editable"],
        "TAG": ["tag_type_id", "tag_value", "active_status"],
        "MOBIL_TANGKI": ["source_mt_id", "vehicle_name_raw", "vehicle_registration", "capacity_label", "vehicle_type_tag", "project_tag_raw", "number_of_compartments", "depot_id", "source_hub_id", "assignee", "active_status"],
        "SPBU": ["spbu_code", "spbu_name", "address", "city", "latitude", "longitude", "source_coordinate", "master_distance_km", "master_travel_time_min", "vehicle_type_tag", "project_tag_raw", "primary_depot_id", "active_status"],
        "LOADING_ORDER": ["shipment_id", "spbu_id", "spbu_mapping_status", "source_spbu_code", "shipto", "product_id", "source_product_name", "quantity", "status", "source_distance_km", "actual_km", "source_import_id"],
    }[domain]
    for field in allowed_fields:
        if field not in payload:
            continue
        value = payload[field]
        if field in {"latitude", "longitude", "master_distance_km", "master_travel_time_min", "quantity", "source_distance_km", "actual_km"}:
            value = source_number(value)
        elif field in {"number_of_compartments", "vehicle_type_tag"}:
            value = source_int(value)
        elif field == "admin_editable":
            value = bool(value)
        else:
            value = clean_str(value)
        setattr(record, field, value)
    if domain == "PRODUCT" and "product_name" in payload:
        record.normalized_product = normalize_product(record.product_name) or normalize_key(record.product_name) or record.product_name.upper()
    if domain == "TAG" and "tag_value" in payload:
        record.normalized_tag = normalize_key(record.tag_value) or record.tag_value.upper()
    if domain == "TAG_TYPE" and "code" in payload and record.code:
        record.code = record.code.upper()
    if domain == "SPBU" and "source_coordinate" in payload and ("latitude" not in payload or "longitude" not in payload):
        parsed_latitude, parsed_longitude, coordinate_messages = parse_coordinate(payload.get("source_coordinate"))
        if not coordinate_messages:
            if "latitude" not in payload:
                record.latitude = parsed_latitude
            if "longitude" not in payload:
                record.longitude = parsed_longitude


def crud_resolve_tag_for_type(db: Session, tag_value: str, tag_type: MasterTagType, source_domain: str) -> MasterTag:
    value = required_text({"tag_value": tag_value}, "tag_value")
    normalized = normalize_key(value)
    if not normalized:
        raise HTTPException(status_code=400, detail="Tag value is invalid.")
    tag = db.scalar(select(MasterTag).where(MasterTag.normalized_tag == normalized))
    if not tag:
        tag = MasterTag(
            tag_id=make_id("tag", normalized),
            tag_type_id=tag_type.tag_type_id,
            tag_value=value,
            normalized_tag=normalized,
            active_status="ACTIVE",
        )
        db.add(tag)
    else:
        tag.tag_type_id = tag_type.tag_type_id
        tag.tag_value = value
        tag.normalized_tag = normalized
        tag.active_status = "ACTIVE"
    db.flush()
    db.merge(
        TagAlias(
            tag_alias_id=make_id("tagalias", normalized, tag.tag_id, source_domain),
            alias_value=value,
            normalized_alias=normalized,
            canonical_tag_id=tag.tag_id,
            source_domain=source_domain,
            active_status="ACTIVE",
        )
    )
    return tag


def apply_crud_tag_links(db: Session, domain: str, record, payload: dict) -> None:
    if domain not in {"MOBIL_TANGKI", "SPBU"}:
        return
    tag_types_by_key = {
        tag_type_column_key(tag_type.code): tag_type
        for tag_type in db.scalars(select(MasterTagType)).all()
        if tag_type.code != "VEHICLE_CLASS"
    }
    edited_keys = [key for key in payload if key in tag_types_by_key]
    if not edited_keys:
        return
    source_domain = "CRUD_MT" if domain == "MOBIL_TANGKI" else "CRUD_SPBU"
    for key in edited_keys:
        tag_type = tag_types_by_key[key]
        tag_values = split_project_tags(payload.get(key))
        if domain == "MOBIL_TANGKI":
            db.execute(
                delete(BridgeMTTag).where(
                    BridgeMTTag.mt_id == record.mt_id,
                    BridgeMTTag.tag_id.in_(select(MasterTag.tag_id).where(MasterTag.tag_type_id == tag_type.tag_type_id)),
                )
            )
            for tag_value in tag_values:
                tag = crud_resolve_tag_for_type(db, tag_value, tag_type, source_domain)
                db.merge(BridgeMTTag(mt_id=record.mt_id, tag_id=tag.tag_id, source_import_id="crud:master-data"))
            if tag_type.code == "PROJECT":
                record.project_tag_raw = clean_str(payload.get(key))
        else:
            db.execute(
                delete(BridgeSPBUTag).where(
                    BridgeSPBUTag.spbu_id == record.spbu_id,
                    BridgeSPBUTag.tag_id.in_(select(MasterTag.tag_id).where(MasterTag.tag_type_id == tag_type.tag_type_id)),
                )
            )
            for tag_value in tag_values:
                tag = crud_resolve_tag_for_type(db, tag_value, tag_type, source_domain)
                db.merge(BridgeSPBUTag(spbu_id=record.spbu_id, tag_id=tag.tag_id, source_import_id="crud:master-data"))
            if tag_type.code == "PROJECT":
                record.project_tag_raw = clean_str(payload.get(key))
    db.flush()


def reactivate_deleted_crud_record(db: Session, domain: str, record):
    model, key_column = crud_model_and_key(domain)
    if domain == "LOADING_ORDER":
        existing = db.get(model, (record.loading_order_number, record.source_depot_name))
    else:
        record_id = getattr(record, key_column.key)
        existing = db.get(model, record_id)
    if not existing:
        existing = find_deleted_crud_record_by_business_key(db, domain, record)
    if not existing:
        return None
    if not hasattr(existing, "active_status") or existing.active_status != "DELETED":
        raise HTTPException(status_code=409, detail="Master data record conflicts with an active existing primary or unique key.")
    for column in model.__table__.columns:
        if column.primary_key or column.name == "created_at":
            continue
        setattr(existing, column.name, getattr(record, column.name))
    existing.active_status = clean_str(getattr(record, "active_status", None)) or "ACTIVE"
    return existing


def find_deleted_crud_record_by_business_key(db: Session, domain: str, record):
    if domain == "MOBIL_TANGKI" and record.vehicle_registration:
        return db.scalar(select(MasterMT).where(MasterMT.vehicle_registration == record.vehicle_registration, MasterMT.active_status == "DELETED"))
    if domain == "SPBU" and record.spbu_code:
        return db.scalar(select(MasterSPBU).where(MasterSPBU.spbu_code == record.spbu_code, MasterSPBU.active_status == "DELETED"))
    if domain == "DEPOT" and record.depot_code:
        return db.scalar(select(MasterDepot).where(MasterDepot.depot_code == record.depot_code, MasterDepot.active_status == "DELETED"))
    if domain == "PRODUCT" and record.normalized_product:
        return db.scalar(select(MasterProduct).where(MasterProduct.normalized_product == record.normalized_product, MasterProduct.active_status == "DELETED"))
    if domain == "TAG" and record.normalized_tag:
        return db.scalar(select(MasterTag).where(MasterTag.normalized_tag == record.normalized_tag, MasterTag.active_status == "DELETED"))
    return None


def sync_result() -> dict:
    return {"discovered": 0, "created": 0, "reactivated": 0, "updated": 0, "skipped": 0}


def record_sync(result: dict, outcome: str) -> None:
    result["discovered"] += 1
    result[outcome] += 1


def active_loading_order_filter():
    return (FactLoadingOrderLine.status.is_(None)) | (FactLoadingOrderLine.status != "DELETED")


def sync_depot_candidate(db: Session, name, code, result: dict) -> None:
    depot_name = clean_str(name) or clean_str(code)
    if not depot_name:
        record_sync(result, "skipped")
        return
    normalized_name = normalize_key(depot_name)
    if not normalized_name:
        record_sync(result, "skipped")
        return
    depot_code = clean_str(code) or normalized_name
    depot_id = make_id("depot", normalized_name)
    depot = db.get(MasterDepot, depot_id)
    if not depot and depot_code:
        depot = db.scalar(select(MasterDepot).where(MasterDepot.depot_code == depot_code))
    if not depot:
        db.add(MasterDepot(depot_id=depot_id, depot_code=depot_code, depot_name=depot_name, active_status="ACTIVE", source_import_id="sync:depot"))
        actual_depot_id = depot_id
        outcome = "created"
    else:
        actual_depot_id = depot.depot_id
        outcome = "reactivated" if depot.active_status == "DELETED" else "updated"
        depot.depot_code = depot.depot_code or depot_code
        depot.depot_name = depot_name
        depot.active_status = "ACTIVE"
        depot.source_import_id = depot.source_import_id or "sync:depot"
    db.merge(
        DepotIdentifierAlias(
            depot_identifier_alias_id=make_id("depotalias", actual_depot_id, "DEPOT_NAME", depot_name),
            depot_id=actual_depot_id,
            identifier_type="DEPOT_NAME",
            identifier_value=depot_name,
            normalized_identifier=normalized_name,
            source_system="SYNC",
            active_status="ACTIVE",
        )
    )
    if depot_code:
        db.merge(
            DepotIdentifierAlias(
                depot_identifier_alias_id=make_id("depotalias", actual_depot_id, "DEPOT_CODE", depot_code),
                depot_id=actual_depot_id,
                identifier_type="DEPOT_CODE",
                identifier_value=depot_code,
                normalized_identifier=normalize_key(depot_code) or depot_code,
                source_system="SYNC",
                active_status="ACTIVE",
            )
        )
    db.flush()
    record_sync(result, outcome)


def sync_depots_from_sources(db: Session) -> dict:
    result = sync_result()
    seen: set[tuple[str | None, str | None]] = set()

    def add_candidate(name, code=None):
        key = (normalize_key(name), normalize_key(code))
        if key in seen:
            return
        seen.add(key)
        sync_depot_candidate(db, name, code, result)

    for (source_depot_name,) in db.execute(
        select(FactLoadingOrderLine.source_depot_name)
        .where(FactLoadingOrderLine.source_depot_name.is_not(None), active_loading_order_filter())
        .distinct()
    ).all():
        add_candidate(source_depot_name)
    for depot_id in {
        value
        for (value,) in db.execute(select(MasterMT.depot_id).where(MasterMT.depot_id.is_not(None), MasterMT.active_status != "DELETED")).all()
        + db.execute(select(MasterSPBU.primary_depot_id).where(MasterSPBU.primary_depot_id.is_not(None), MasterSPBU.active_status != "DELETED")).all()
        + db.execute(
            select(FactShipment.depot_id)
            .join(FactLoadingOrderLine, FactLoadingOrderLine.shipment_id == FactShipment.shipment_id)
            .where(FactShipment.depot_id.is_not(None), active_loading_order_filter())
            .distinct()
        ).all()
    }:
        depot = db.get(MasterDepot, depot_id)
        if depot:
            add_candidate(depot.depot_name, depot.depot_code)
    return result


def sync_product_candidate(db: Session, product_name, result: dict) -> None:
    name = clean_str(product_name)
    normalized = normalize_product(name)
    if not name or not normalized:
        record_sync(result, "skipped")
        return
    product_id = make_id("product", normalized)
    product = db.get(MasterProduct, product_id) or db.scalar(select(MasterProduct).where(MasterProduct.normalized_product == normalized))
    if not product:
        db.add(MasterProduct(product_id=product_id, product_name=name, normalized_product=normalized, active_status="ACTIVE", source_import_id="sync:product"))
        outcome = "created"
    else:
        outcome = "reactivated" if product.active_status == "DELETED" else "updated"
        product.product_name = name
        product.normalized_product = normalized
        product.active_status = "ACTIVE"
        product.source_import_id = product.source_import_id or "sync:product"
    db.merge(ProductAlias(product_alias_id=make_id("productalias", normalized, "SYNC"), product_id=product_id, alias_value=name, normalized_alias=normalized, source_system="SYNC", active_status="ACTIVE"))
    db.flush()
    record_sync(result, outcome)


def sync_products_from_sources(db: Session) -> dict:
    result = sync_result()
    seen: set[str] = set()

    def add_candidate(value):
        normalized = normalize_product(value)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        sync_product_candidate(db, value, result)

    for (value,) in db.execute(
        select(FactLoadingOrderLine.source_product_name)
        .where(FactLoadingOrderLine.source_product_name.is_not(None), active_loading_order_filter())
        .distinct()
    ).all():
        add_candidate(value)
    return result


def ensure_sync_tag_type(db: Session, tag_type_code: str) -> str:
    tag_type_id = make_id("tagtype", tag_type_code)
    tag_type = db.get(MasterTagType, tag_type_id)
    if not tag_type:
        db.add(MasterTagType(tag_type_id=tag_type_id, code=tag_type_code, name=tag_type_code.replace("_", " ").title(), admin_editable=True))
    return tag_type_id


def sync_tag_candidate(db: Session, tag_value, result: dict) -> None:
    value = clean_str(tag_value)
    normalized = normalize_key(value)
    if not value or not normalized:
        record_sync(result, "skipped")
        return
    tag_type_id = ensure_sync_tag_type(db, infer_tag_type(value))
    tag_id = make_id("tag", normalized)
    tag = db.get(MasterTag, tag_id) or db.scalar(select(MasterTag).where(MasterTag.normalized_tag == normalized))
    if not tag:
        db.add(MasterTag(tag_id=tag_id, tag_type_id=tag_type_id, tag_value=value, normalized_tag=normalized, active_status="ACTIVE"))
        outcome = "created"
    else:
        outcome = "reactivated" if tag.active_status == "DELETED" else "updated"
        tag.tag_type_id = tag.tag_type_id or tag_type_id
        tag.tag_value = value
        tag.normalized_tag = normalized
        tag.active_status = "ACTIVE"
    db.merge(TagAlias(tag_alias_id=make_id("tagalias", normalized, tag_id, "SYNC"), alias_value=value, normalized_alias=normalized, canonical_tag_id=tag_id, source_domain="SYNC", active_status="ACTIVE"))
    db.flush()
    record_sync(result, outcome)


def sync_tags_from_sources(db: Session) -> dict:
    result = sync_result()
    seen: set[str] = set()

    def add_tags(raw):
        for tag_value in split_project_tags(raw):
            normalized = normalize_key(tag_value)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            sync_tag_candidate(db, tag_value, result)

    for (raw,) in db.execute(select(MasterMT.project_tag_raw).where(MasterMT.project_tag_raw.is_not(None), MasterMT.active_status != "DELETED")).all():
        add_tags(raw)
    for (raw,) in db.execute(select(MasterSPBU.project_tag_raw).where(MasterSPBU.project_tag_raw.is_not(None), MasterSPBU.active_status != "DELETED")).all():
        add_tags(raw)
    return result


def required_text(payload: dict, field: str) -> str:
    value = clean_str(payload.get(field))
    if not value:
        raise HTTPException(status_code=400, detail=f"{field} is required.")
    return value


def commit_crud(db: Session, domain: str, record) -> dict:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Master data record conflicts with existing unique or reference constraints.") from exc
    db.refresh(record)
    tag_types = tag_type_lookup(db) if domain in {"MOBIL_TANGKI", "SPBU", "TAG"} else None
    mt_tag_values = mt_tag_value_lookup(db, [record.mt_id], tag_types or {}) if domain == "MOBIL_TANGKI" else None
    spbu_tag_values = spbu_tag_value_lookup(db, [record.spbu_id], tag_types or {}) if domain == "SPBU" else None
    return {"status": "OK", "record": serialize_crud_record(domain, record, tag_types, mt_tag_values, spbu_tag_values)}


def normalize_export_domain(domain: str) -> str:
    normalized = domain.upper()
    aliases = {
        "MT": "MOBIL_TANGKI",
        "MOBIL_TANGKI": "MOBIL_TANGKI",
        "SPBU": "SPBU",
        "LO": "LOADING_ORDER",
        "LOADING_ORDER": "LOADING_ORDER",
        "SHIPMENT": "SHIPMENT",
        "SHIPMENTS": "SHIPMENT",
        "GPS": "GPS",
        "ALL": "ALL",
    }
    return aliases.get(normalized, normalized)


def normalize_file_format(file_format: str) -> str:
    normalized = file_format.lower()
    if normalized not in {"xlsx", "csv"}:
        raise HTTPException(status_code=400, detail="file_format must be xlsx or csv.")
    return normalized


def safe_filename(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_") or "depot"


def format_cell(value):
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def workbook_response(sheets: list[tuple[str, list[str], list[list]]], filename: str) -> StreamingResponse:
    workbook = Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)
    for sheet_name, headers, rows in sheets:
        worksheet = workbook.create_sheet(title=sheet_name[:31])
        worksheet.append(headers)
        for row in rows:
            worksheet.append([format_cell(value) for value in row])
        for cell in worksheet[1]:
            cell.style = "Headline 4"
        worksheet.freeze_panes = "A2"
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def csv_response(headers: list[str], rows: list[list], filename: str) -> StreamingResponse:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([format_cell(value) for value in row])
    data = BytesIO(output.getvalue().encode("utf-8-sig"))
    return StreamingResponse(
        data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def build_export_sheets(db: Session, domain: str, depot: MasterDepot) -> list[tuple[str, list[str], list[list]]]:
    builders = {
        "MOBIL_TANGKI": build_mt_export,
        "SPBU": build_spbu_export,
        "SHIPMENT": build_shipment_export,
        "LOADING_ORDER": build_loading_order_export,
    }
    if domain == "ALL":
        return [
            build_mt_export(db, depot),
            build_spbu_export(db, depot),
            build_shipment_export(db, depot),
            build_loading_order_export(db, depot),
        ]
    if domain not in builders:
        raise HTTPException(status_code=400, detail="Data export is available for MOBIL_TANGKI, SPBU, SHIPMENT, LOADING_ORDER, or ALL.")
    return [builders[domain](db, depot)]


def build_tag_lookup(db: Session, owner: str, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    if owner == "MT":
        rows = db.execute(
            select(BridgeMTTag.mt_id, MasterTag.tag_value)
            .join(MasterTag, MasterTag.tag_id == BridgeMTTag.tag_id)
            .where(BridgeMTTag.mt_id.in_(ids))
            .order_by(MasterTag.tag_value)
        ).all()
    else:
        rows = db.execute(
            select(BridgeSPBUTag.spbu_id, MasterTag.tag_value)
            .join(MasterTag, MasterTag.tag_id == BridgeSPBUTag.tag_id)
            .where(BridgeSPBUTag.spbu_id.in_(ids))
            .order_by(MasterTag.tag_value)
        ).all()
    grouped: dict[str, list[str]] = {}
    for entity_id, tag_value in rows:
        grouped.setdefault(entity_id, []).append(tag_value)
    return {entity_id: ",".join(values) for entity_id, values in grouped.items()}


def build_mt_export(db: Session, depot: MasterDepot) -> tuple[str, list[str], list[list]]:
    headers = [
        "depot_code",
        "depot_name",
        "source_mt_id",
        "vehicle_name_raw",
        "vehicle_registration",
        "capacity_label",
        "vehicle_type_tag",
        "number_of_compartments",
        "source_hub_id",
        "assignee",
        "active_status",
        "project_tags",
        "source_import_id",
    ]
    mts = db.scalars(select(MasterMT).where(MasterMT.depot_id == depot.depot_id).order_by(MasterMT.vehicle_registration)).all()
    tag_lookup = build_tag_lookup(db, "MT", [mt.mt_id for mt in mts])
    rows = [
        [
            depot.depot_code,
            depot.depot_name,
            mt.source_mt_id,
            mt.vehicle_name_raw,
            mt.vehicle_registration,
            mt.capacity_label,
            mt.vehicle_type_tag,
            mt.number_of_compartments,
            mt.source_hub_id,
            mt.assignee,
            mt.active_status,
            tag_lookup.get(mt.mt_id, ""),
            mt.source_import_id,
        ]
        for mt in mts
    ]
    return "Mobil Tangki", headers, rows


def build_spbu_export(db: Session, depot: MasterDepot) -> tuple[str, list[str], list[list]]:
    headers = [
        "depot_code",
        "depot_name",
        "spbu_code",
        "spbu_name",
        "address",
        "city",
        "latitude",
        "longitude",
        "source_coordinate",
        "master_distance_km",
        "master_travel_time_min",
        "vehicle_type_tag",
        "active_status",
        "project_tags",
        "source_import_id",
    ]
    spbus = db.scalars(select(MasterSPBU).where(MasterSPBU.primary_depot_id == depot.depot_id).order_by(MasterSPBU.spbu_code)).all()
    tag_lookup = build_tag_lookup(db, "SPBU", [spbu.spbu_id for spbu in spbus])
    rows = [
        [
            depot.depot_code,
            depot.depot_name,
            spbu.spbu_code,
            spbu.spbu_name,
            spbu.address,
            spbu.city,
            spbu.latitude,
            spbu.longitude,
            spbu.source_coordinate,
            spbu.master_distance_km,
            spbu.master_travel_time_min,
            spbu.vehicle_type_tag,
            spbu.active_status,
            tag_lookup.get(spbu.spbu_id, ""),
            spbu.source_import_id,
        ]
        for spbu in spbus
    ]
    return "SPBU", headers, rows


def build_shipment_export(db: Session, depot: MasterDepot) -> tuple[str, list[str], list[list]]:
    headers = [
        "depot_code",
        "depot_name",
        "source_shipment_id",
        "operating_date",
        "area_id",
        "area",
        "vehicle_registration",
        "vehicle_mapping_status",
        "vehicle_type_tag_observed",
        "validation_datetime",
        "gate_out_datetime",
        "shipment_end_datetime",
        "status",
        "source_import_id",
    ]
    shipments = db.scalars(select(FactShipment).where(FactShipment.depot_id == depot.depot_id).order_by(FactShipment.source_shipment_id)).all()
    rows = [
        [
            depot.depot_code,
            depot.depot_name,
            shipment.source_shipment_id,
            shipment.operating_date,
            shipment.area_id,
            shipment.area,
            shipment.vehicle_registration,
            shipment.vehicle_mapping_status,
            shipment.vehicle_type_tag_observed,
            shipment.validation_datetime,
            shipment.gate_out_datetime,
            shipment.shipment_end_datetime,
            shipment.status,
            shipment.source_import_id,
        ]
        for shipment in shipments
    ]
    return "Shipments", headers, rows


def build_loading_order_export(db: Session, depot: MasterDepot) -> tuple[str, list[str], list[list]]:
    headers = [
        "depot_code",
        "depot_name",
        "source_depot_name",
        "source_shipment_id",
        "loading_order_number",
        "source_spbu_code",
        "spbu_code",
        "spbu_mapping_status",
        "shipto",
        "product_name",
        "source_product_name",
        "quantity",
        "status",
        "source_distance_km",
        "actual_km",
        "source_import_id",
    ]
    rows_query = (
        select(FactLoadingOrderLine, FactShipment)
        .join(FactShipment, FactShipment.shipment_id == FactLoadingOrderLine.shipment_id)
        .where(FactShipment.depot_id == depot.depot_id)
        .order_by(FactShipment.source_shipment_id, FactLoadingOrderLine.loading_order_number)
    )
    line_pairs = db.execute(rows_query).all()
    spbu_ids = {line.spbu_id for line, _ in line_pairs if line.spbu_id}
    product_ids = {line.product_id for line, _ in line_pairs if line.product_id}
    spbus = {spbu.spbu_id: spbu for spbu in db.scalars(select(MasterSPBU).where(MasterSPBU.spbu_id.in_(spbu_ids))).all()} if spbu_ids else {}
    products = {product.product_id: product for product in db.scalars(select(MasterProduct).where(MasterProduct.product_id.in_(product_ids))).all()} if product_ids else {}
    rows = []
    for line, shipment in line_pairs:
        spbu = spbus.get(line.spbu_id)
        product = products.get(line.product_id)
        rows.append(
            [
                depot.depot_code,
                depot.depot_name,
                line.source_depot_name,
                shipment.source_shipment_id,
                line.loading_order_number,
                line.source_spbu_code,
                spbu.spbu_code if spbu else "",
                line.spbu_mapping_status,
                line.shipto,
                product.product_name if product else "",
                line.source_product_name,
                line.quantity,
                line.status,
                line.source_distance_km,
                line.actual_km,
                line.source_import_id,
            ]
        )
    return "Loading Orders", headers, rows


def public(model) -> dict:
    data = {key: value for key, value in model.__dict__.items() if not key.startswith("_")}
    return data
