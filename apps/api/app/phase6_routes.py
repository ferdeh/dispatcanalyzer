from __future__ import annotations

import json
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .database import get_db
from .phase6_auth import Phase6Actor, require_phase6_permission
from .phase6_export import loading_order_template, mt_availability_template, prediction_export, validation_report
from .phase6_service import (
    adjust_shipment,
    create_prediction_run,
    duplicate_prediction_run,
    get_prediction_run,
    list_prediction_models,
    list_prediction_runs,
    override_assignment,
)
from .phase6_validation import require_prediction_model, validate_loading_orders, validate_mt_availability


router = APIRouter(prefix="/api/v1/phase6", tags=["Phase 6 - Prediction and Assignment"])


class AssignmentOverrideRequest(BaseModel):
    vehicle_id: str
    override_reason: str | None = Field(default=None, max_length=2000)


class ShipmentOverrideRequest(BaseModel):
    action: str
    line_ids: list[str] = Field(default_factory=list)
    target_shipment_id: str | None = None


class RerunRequest(BaseModel):
    model_id: str | None = None


def _excel_response(content: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/models")
def prediction_models(
    depot_id: str,
    db: Session = Depends(get_db),
    _actor: Phase6Actor = Depends(require_phase6_permission("view")),
) -> list[dict]:
    return list_prediction_models(db, depot_id)


@router.get("/templates/loading-order")
def download_loading_order_template(
    _actor: Phase6Actor = Depends(require_phase6_permission("view")),
) -> StreamingResponse:
    return _excel_response(loading_order_template(), "phase6-loading-order-template.xlsx")


@router.get("/templates/mt-availability")
def download_mt_availability_template(
    _actor: Phase6Actor = Depends(require_phase6_permission("view")),
) -> StreamingResponse:
    return _excel_response(mt_availability_template(), "phase6-mt-availability-template.xlsx")


@router.post("/validate/loading-order")
async def validate_loading_order_file(
    depot_id: str = Form(...),
    model_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _actor: Phase6Actor = Depends(require_phase6_permission("run")),
) -> dict:
    model = require_prediction_model(db, depot_id, model_id)
    return validate_loading_orders(db, depot_id=depot_id, model=model, content=await file.read(), file_name=file.filename or "loading-order.xlsx")


@router.post("/validate/mt-availability")
async def validate_mt_availability_file(
    depot_id: str = Form(...),
    model_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _actor: Phase6Actor = Depends(require_phase6_permission("run")),
) -> dict:
    model = require_prediction_model(db, depot_id, model_id)
    return validate_mt_availability(db, depot_id=depot_id, model=model, content=await file.read(), file_name=file.filename or "mt-availability.xlsx")


@router.post("/validation-report")
async def download_validation_report(
    depot_id: str = Form(...),
    model_id: str = Form(...),
    loading_order_file: UploadFile = File(...),
    mt_availability_file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _actor: Phase6Actor = Depends(require_phase6_permission("view")),
) -> StreamingResponse:
    model = require_prediction_model(db, depot_id, model_id)
    lo = validate_loading_orders(db, depot_id=depot_id, model=model, content=await loading_order_file.read(), file_name=loading_order_file.filename or "loading-order.xlsx")
    mt = validate_mt_availability(db, depot_id=depot_id, model=model, content=await mt_availability_file.read(), file_name=mt_availability_file.filename or "mt-availability.xlsx")
    return _excel_response(validation_report([*lo["issues"], *mt["issues"]]), "phase6-validation-report.xlsx")


@router.post("/predictions")
async def run_prediction(
    depot_id: str = Form(...),
    model_id: str = Form(...),
    parameters: str = Form(default="{}"),
    loading_order_file: UploadFile = File(...),
    mt_availability_file: UploadFile = File(...),
    db: Session = Depends(get_db),
    actor: Phase6Actor = Depends(require_phase6_permission("run")),
) -> dict:
    try:
        parsed_parameters = json.loads(parameters)
        if not isinstance(parsed_parameters, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"code": "INVALID_PARAMETER", "message": "parameters must be a JSON object."}) from exc
    return create_prediction_run(
        db,
        depot_id=depot_id,
        model_id=model_id,
        loading_order_content=await loading_order_file.read(),
        loading_order_filename=loading_order_file.filename or "loading-order.xlsx",
        availability_content=await mt_availability_file.read(),
        availability_filename=mt_availability_file.filename or "mt-availability.xlsx",
        parameters=parsed_parameters,
        created_by=actor.user_id,
    )


@router.get("/predictions")
def prediction_history(
    depot_id: str | None = None,
    db: Session = Depends(get_db),
    _actor: Phase6Actor = Depends(require_phase6_permission("view")),
) -> list[dict]:
    return list_prediction_runs(db, depot_id)


@router.get("/predictions/{run_id}")
def prediction_detail(
    run_id: str,
    db: Session = Depends(get_db),
    _actor: Phase6Actor = Depends(require_phase6_permission("view")),
) -> dict:
    return get_prediction_run(db, run_id)


@router.post("/predictions/{run_id}/recalculate")
def rerun_prediction(
    run_id: str,
    request: RerunRequest,
    db: Session = Depends(get_db),
    actor: Phase6Actor = Depends(require_phase6_permission("run")),
) -> dict:
    return duplicate_prediction_run(db, run_id, model_id=request.model_id, created_by=actor.user_id)


@router.patch("/predictions/{run_id}/shipments/{shipment_id}")
def patch_shipment(
    run_id: str,
    shipment_id: str,
    request: ShipmentOverrideRequest,
    db: Session = Depends(get_db),
    actor: Phase6Actor = Depends(require_phase6_permission("override")),
) -> dict:
    return adjust_shipment(
        db,
        run_id,
        shipment_id,
        action=request.action,
        line_ids=request.line_ids,
        target_shipment_id=request.target_shipment_id,
        user_id=actor.user_id,
    )


@router.patch("/predictions/{run_id}/assignments/{assignment_id}")
def patch_assignment(
    run_id: str,
    assignment_id: str,
    request: AssignmentOverrideRequest,
    db: Session = Depends(get_db),
    actor: Phase6Actor = Depends(require_phase6_permission("override")),
) -> dict:
    return override_assignment(db, run_id, assignment_id, request.vehicle_id, request.override_reason, actor.user_id)


@router.get("/predictions/{run_id}/export")
def export_prediction(
    run_id: str,
    db: Session = Depends(get_db),
    _actor: Phase6Actor = Depends(require_phase6_permission("export")),
) -> StreamingResponse:
    content, filename = prediction_export(db, run_id)
    return _excel_response(content, filename)
