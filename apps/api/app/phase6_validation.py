from __future__ import annotations

from collections import Counter
from io import BytesIO
from time import perf_counter
from typing import Any

from fastapi import HTTPException
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MLBehavioralModel, MasterMT, MasterSPBU, SpbuIdentifierAlias
from .normalization import clean_str, normalize_key


MAX_WORKBOOK_BYTES = 10 * 1024 * 1024
LOADING_ORDER_COLUMNS = ("loading_order_no", "shift_gate_out", "spbu_no")
MT_AVAILABILITY_COLUMNS = ("shift", "vehicle_registration_no")


def require_prediction_model(db: Session, depot_id: str, model_id: str) -> MLBehavioralModel:
    model = db.get(MLBehavioralModel, model_id)
    if not model:
        raise HTTPException(status_code=404, detail={"code": "MODEL_NOT_FOUND", "message": "Prediction model was not found."})
    if model.depot_id != depot_id:
        raise HTTPException(status_code=409, detail={"code": "MODEL_DEPOT_MISMATCH", "message": "Prediction model belongs to another depot."})
    if model.model_status not in {"SAVED", "ACTIVE", "READY"}:
        raise HTTPException(status_code=409, detail={"code": "MODEL_NOT_READY", "message": "Prediction model is not ready for inference."})
    return model


def _issue(file_name: str, row: int, field: str, status: str, code: str, description: str) -> dict:
    return {"file": file_name, "row": row, "field": field, "status": status, "error_code": code, "description": description}


def read_excel_rows(content: bytes, *, file_name: str, required_columns: tuple[str, ...], aliases: dict[str, str] | None = None) -> tuple[list[dict], list[dict]]:
    if not content or len(content) > MAX_WORKBOOK_BYTES:
        return [], [_issue(file_name, 1, "file", "ERROR", "INVALID_FILE_SIZE", "Workbook must be non-empty and no larger than 10 MB.")]
    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
        sheet = workbook.active
        values = sheet.iter_rows(values_only=True)
        raw_headers = next(values, None)
        if not raw_headers:
            return [], [_issue(file_name, 1, "header", "ERROR", "REQUIRED_COLUMNS_MISSING", "Workbook has no header row.")]
        alias_map = {normalize_key(key): value for key, value in (aliases or {}).items()}
        headers: list[str] = []
        for value in raw_headers:
            raw = clean_str(value) or ""
            normalized = normalize_key(raw) or ""
            headers.append(alias_map.get(normalized, raw.strip().lower()))
        missing = [column for column in required_columns if column not in headers]
        if missing:
            return [], [
                _issue(file_name, 1, column, "ERROR", "REQUIRED_COLUMN_MISSING", f"Required column '{column}' is missing.")
                for column in missing
            ]
        rows = []
        for row_number, values_row in enumerate(values, start=2):
            row = {headers[index]: clean_str(value) for index, value in enumerate(values_row) if index < len(headers) and headers[index]}
            if any(value is not None for value in row.values()):
                row["_row_number"] = row_number
                rows.append(row)
        return rows, []
    except Exception:
        return [], [_issue(file_name, 1, "file", "ERROR", "INVALID_EXCEL_FILE", "File could not be read as an Excel workbook.")]


def shift_lookup(model: MLBehavioralModel) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for shift in model.shift_definition_snapshot or []:
        cleaned = {
            "shift_id": clean_str(shift.get("shift_id")) or "",
            "name": clean_str(shift.get("name")) or clean_str(shift.get("shift_id")) or "",
            "start_time": clean_str(shift.get("start_time")),
            "end_time": clean_str(shift.get("end_time")),
        }
        for value in (cleaned["shift_id"], cleaned["name"]):
            if normalize_key(value):
                lookup[normalize_key(value)] = cleaned
    return lookup


def validate_loading_orders(db: Session, *, depot_id: str, model: MLBehavioralModel, content: bytes, file_name: str) -> dict:
    started = perf_counter()
    rows, issues = read_excel_rows(
        content,
        file_name=file_name,
        required_columns=LOADING_ORDER_COLUMNS,
        aliases={
            "loading_order_no": "loading_order_no",
            "loading_order_number": "loading_order_no",
            "loading_order_id": "loading_order_no",
            "shift_gate_out": "shift_gate_out",
            "shift": "shift_gate_out",
            "spbu_no": "spbu_no",
            "spbu_code": "spbu_no",
            "spbu_id": "spbu_no",
        },
    )
    if issues:
        return _validation_payload("LOADING_ORDER", [], issues, started)

    spbus = db.scalars(select(MasterSPBU)).all()
    spbu_by_key = {key: spbu for spbu in spbus for key in {normalize_key(spbu.spbu_id), normalize_key(spbu.spbu_code)} if key}
    for alias in db.scalars(select(SpbuIdentifierAlias).where(SpbuIdentifierAlias.active_status == "ACTIVE")).all():
        if normalize_key(alias.identifier_value) and alias.spbu_id in {spbu.spbu_id for spbu in spbus}:
            spbu_by_key[normalize_key(alias.identifier_value)] = next(spbu for spbu in spbus if spbu.spbu_id == alias.spbu_id)
    shifts = shift_lookup(model)
    duplicates = Counter(normalize_key(row.get("loading_order_no")) for row in rows if normalize_key(row.get("loading_order_no")))
    normalized_rows = []
    for row in rows:
        row_number = int(row["_row_number"])
        for field in LOADING_ORDER_COLUMNS:
            if not clean_str(row.get(field)):
                issues.append(_issue(file_name, row_number, field, "ERROR", "REQUIRED_VALUE_EMPTY", f"{field} must not be empty."))
        lo_key = normalize_key(row.get("loading_order_no"))
        if lo_key and duplicates[lo_key] > 1:
            issues.append(_issue(file_name, row_number, "loading_order_no", "ERROR", "DUPLICATE_LOADING_ORDER", "Loading Order appears more than once and cannot be persisted unambiguously."))
        spbu = spbu_by_key.get(normalize_key(row.get("spbu_no")) or "")
        if row.get("spbu_no") and not spbu:
            issues.append(_issue(file_name, row_number, "spbu_no", "ERROR", "SPBU_NOT_FOUND", "SPBU identifier is not present in canonical master data."))
        elif spbu and spbu.primary_depot_id != depot_id:
            issues.append(_issue(file_name, row_number, "spbu_no", "ERROR", "SPBU_DEPOT_MISMATCH", "SPBU belongs to another depot."))
        shift = shifts.get(normalize_key(row.get("shift_gate_out")) or "")
        if row.get("shift_gate_out") and not shift:
            issues.append(_issue(file_name, row_number, "shift_gate_out", "ERROR", "SHIFT_NOT_FOUND", "Shift is not defined by the selected model's shift configuration."))
        if all(clean_str(row.get(field)) for field in LOADING_ORDER_COLUMNS) and spbu and spbu.primary_depot_id == depot_id and shift:
            normalized_rows.append(
                {
                    "source_row_number": row_number,
                    "loading_order_no": clean_str(row["loading_order_no"]),
                    "shift_id": shift["shift_id"],
                    "shift": shift["name"],
                    "spbu_id": spbu.spbu_id,
                    "spbu_no": spbu.spbu_code,
                }
            )
    return _validation_payload("LOADING_ORDER", normalized_rows, issues, started)


def validate_mt_availability(db: Session, *, depot_id: str, model: MLBehavioralModel, content: bytes, file_name: str) -> dict:
    started = perf_counter()
    rows, issues = read_excel_rows(
        content,
        file_name=file_name,
        required_columns=MT_AVAILABILITY_COLUMNS,
        aliases={
            "shift": "shift",
            "shift_gate_out": "shift",
            "vehicle_registration_no": "vehicle_registration_no",
            "vehicle_registration": "vehicle_registration_no",
            "registration_no": "vehicle_registration_no",
            "mt_id": "vehicle_registration_no",
        },
    )
    if issues:
        return _validation_payload("MT_AVAILABILITY", [], issues, started)
    mts = db.scalars(select(MasterMT)).all()
    mt_by_key = {
        key: mt
        for mt in mts
        for key in {normalize_key(mt.mt_id), normalize_key(mt.vehicle_registration), normalize_key(mt.source_mt_id)}
        if key
    }
    shifts = shift_lookup(model)
    duplicate_keys = Counter(
        (normalize_key(row.get("shift")), normalize_key(row.get("vehicle_registration_no")))
        for row in rows
        if normalize_key(row.get("shift")) and normalize_key(row.get("vehicle_registration_no"))
    )
    normalized_rows = []
    for row in rows:
        row_number = int(row["_row_number"])
        for field in MT_AVAILABILITY_COLUMNS:
            if not clean_str(row.get(field)):
                issues.append(_issue(file_name, row_number, field, "ERROR", "REQUIRED_VALUE_EMPTY", f"{field} must not be empty."))
        key = (normalize_key(row.get("shift")), normalize_key(row.get("vehicle_registration_no")))
        if all(key) and duplicate_keys[key] > 1:
            issues.append(_issue(file_name, row_number, "vehicle_registration_no", "WARNING", "DUPLICATE_MT_AVAILABILITY", "Vehicle is listed more than once for this shift; it was not silently removed."))
        mt = mt_by_key.get(key[1] or "")
        if row.get("vehicle_registration_no") and not mt:
            issues.append(_issue(file_name, row_number, "vehicle_registration_no", "ERROR", "VEHICLE_NOT_FOUND", "Vehicle is not present in canonical MT master data."))
        elif mt and mt.depot_id and mt.depot_id != depot_id:
            issues.append(_issue(file_name, row_number, "vehicle_registration_no", "ERROR", "VEHICLE_DEPOT_MISMATCH", "Vehicle belongs to another depot."))
        shift = shifts.get(key[0] or "")
        if row.get("shift") and not shift:
            issues.append(_issue(file_name, row_number, "shift", "ERROR", "SHIFT_NOT_FOUND", "Shift is not defined by the selected model's shift configuration."))
        if all(clean_str(row.get(field)) for field in MT_AVAILABILITY_COLUMNS) and mt and (not mt.depot_id or mt.depot_id == depot_id) and shift:
            normalized_rows.append(
                {
                    "source_row_number": row_number,
                    "shift_id": shift["shift_id"],
                    "shift": shift["name"],
                    "vehicle_id": mt.mt_id,
                    "vehicle_registration_no": mt.vehicle_registration or mt.mt_id,
                }
            )
    return _validation_payload("MT_AVAILABILITY", normalized_rows, issues, started)


def _validation_payload(file_type: str, rows: list[dict], issues: list[dict], started: float) -> dict:
    errors = sum(issue["status"] == "ERROR" for issue in issues)
    warnings = sum(issue["status"] == "WARNING" for issue in issues)
    return {
        "file_type": file_type,
        "status": "ERROR" if errors else "WARNING" if warnings else "PASS",
        "blocking_error_count": errors,
        "warning_count": warnings,
        "issues": issues,
        "normalized_rows": rows,
        "row_count": len(rows),
        "detected_shifts": sorted({row["shift"] for row in rows}),
        "duration_ms": round((perf_counter() - started) * 1000),
    }
