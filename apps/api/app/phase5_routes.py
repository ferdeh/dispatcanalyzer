from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import MLBehavioralModel
from .phase5_auth import Phase5Actor, require_phase5_permission
from .phase5_behavioral import get_training_run, prepare_training_dataset, train_behavioral_model
from .phase5_concentration import (
    get_concentration_profile,
    get_concentration_run,
    list_concentration_runs,
    run_concentration_analysis,
)
from .phase5_readiness import build_phase5_readiness
from .phase5_registry import (
    activate_behavioral_model,
    compare_behavioral_models,
    delete_behavioral_model,
    duplicate_behavioral_configuration,
    get_behavioral_model,
    list_behavioral_models,
    save_behavioral_model,
)


router = APIRouter(prefix="/api/v1/phase5", tags=["Phase 5 - Machine Learning Intelligence"])


class ConcentrationAnalysisRequest(BaseModel):
    depot_id: str
    baseline_start_date: date
    baseline_end_date: date
    minimum_shipment_observation: int = Field(default=10, ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class PrepareDatasetRequest(BaseModel):
    depot_id: str
    training_start_date: date
    training_end_date: date
    minimum_shipment_observation: int = Field(default=10, ge=1)
    shift_definitions: list[dict[str, Any]] = Field(default_factory=list)


class TrainModelRequest(BaseModel):
    configuration: dict[str, Any] = Field(default_factory=dict)


class SaveModelRequest(BaseModel):
    model_name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class CompareModelsRequest(BaseModel):
    model_a_id: str
    model_b_id: str


class ModelStatusRequest(BaseModel):
    status: str


@router.get("/readiness")
def phase5_readiness(
    depot_id: str,
    db: Session = Depends(get_db),
    _actor: Phase5Actor = Depends(require_phase5_permission("view")),
) -> dict:
    return build_phase5_readiness(db, depot_id)


@router.post("/engine-a/analyze")
def analyze_concentration(
    request: ConcentrationAnalysisRequest,
    db: Session = Depends(get_db),
    actor: Phase5Actor = Depends(require_phase5_permission("run")),
) -> dict:
    return run_concentration_analysis(
        db,
        depot_id=request.depot_id,
        baseline_start_date=request.baseline_start_date,
        baseline_end_date=request.baseline_end_date,
        minimum_shipment_observation=request.minimum_shipment_observation,
        parameters=request.parameters,
        created_by=actor.user_id,
    )


@router.get("/engine-a/runs")
def concentration_runs(
    depot_id: str | None = None,
    db: Session = Depends(get_db),
    _actor: Phase5Actor = Depends(require_phase5_permission("view")),
) -> list[dict]:
    return list_concentration_runs(db, depot_id)


@router.get("/engine-a/runs/{analysis_run_id}")
def concentration_run(
    analysis_run_id: str,
    db: Session = Depends(get_db),
    _actor: Phase5Actor = Depends(require_phase5_permission("view")),
) -> dict:
    return get_concentration_run(db, analysis_run_id)


@router.get("/engine-a/runs/{analysis_run_id}/spbu/{spbu_id}")
def concentration_spbu_detail(
    analysis_run_id: str,
    spbu_id: str,
    db: Session = Depends(get_db),
    _actor: Phase5Actor = Depends(require_phase5_permission("view")),
) -> dict:
    return get_concentration_profile(db, analysis_run_id, spbu_id)


@router.post("/engine-b/prepare-dataset")
def prepare_dataset(
    request: PrepareDatasetRequest,
    db: Session = Depends(get_db),
    actor: Phase5Actor = Depends(require_phase5_permission("train")),
) -> dict:
    return prepare_training_dataset(
        db,
        depot_id=request.depot_id,
        training_start_date=request.training_start_date,
        training_end_date=request.training_end_date,
        minimum_shipment_observation=request.minimum_shipment_observation,
        shift_definitions=request.shift_definitions or None,
        created_by=actor.user_id,
    )


@router.post("/engine-b/training-runs/{training_run_id}/train")
def train_model(
    training_run_id: str,
    request: TrainModelRequest,
    db: Session = Depends(get_db),
    _actor: Phase5Actor = Depends(require_phase5_permission("train")),
) -> dict:
    return train_behavioral_model(db, training_run_id, request.configuration)


@router.get("/engine-b/training-runs/{training_run_id}")
def training_run(
    training_run_id: str,
    db: Session = Depends(get_db),
    _actor: Phase5Actor = Depends(require_phase5_permission("view")),
) -> dict:
    return get_training_run(db, training_run_id)


@router.post("/engine-b/training-runs/{training_run_id}/save")
def save_model(
    training_run_id: str,
    request: SaveModelRequest,
    db: Session = Depends(get_db),
    actor: Phase5Actor = Depends(require_phase5_permission("save")),
) -> dict:
    return save_behavioral_model(
        db,
        training_run_id,
        model_name=request.model_name,
        description=request.description,
        created_by=actor.user_id,
    )


@router.get("/models")
def model_registry(
    depot_id: str | None = None,
    db: Session = Depends(get_db),
    _actor: Phase5Actor = Depends(require_phase5_permission("view")),
) -> list[dict]:
    return list_behavioral_models(db, depot_id)


@router.get("/models/active")
def active_model(
    depot_id: str,
    db: Session = Depends(get_db),
    _actor: Phase5Actor = Depends(require_phase5_permission("view")),
) -> dict:
    model = db.scalar(
        select(MLBehavioralModel).where(
            MLBehavioralModel.depot_id == depot_id,
            MLBehavioralModel.model_status == "ACTIVE",
        )
    )
    if not model:
        raise HTTPException(status_code=404, detail="No active behavioral model exists for this depot.")
    return get_behavioral_model(db, model.model_id)


@router.post("/models/compare")
def compare_models(
    request: CompareModelsRequest,
    db: Session = Depends(get_db),
    _actor: Phase5Actor = Depends(require_phase5_permission("view")),
) -> dict:
    return compare_behavioral_models(db, request.model_a_id, request.model_b_id)


@router.get("/models/{model_id}")
def open_model(
    model_id: str,
    db: Session = Depends(get_db),
    _actor: Phase5Actor = Depends(require_phase5_permission("view")),
) -> dict:
    return get_behavioral_model(db, model_id)


@router.post("/models/{model_id}/activate")
def activate_model(
    model_id: str,
    db: Session = Depends(get_db),
    _actor: Phase5Actor = Depends(require_phase5_permission("activate")),
) -> dict:
    return activate_behavioral_model(db, model_id)


@router.post("/models/{model_id}/duplicate")
def duplicate_model(
    model_id: str,
    db: Session = Depends(get_db),
    _actor: Phase5Actor = Depends(require_phase5_permission("train")),
) -> dict:
    return duplicate_behavioral_configuration(db, model_id)


@router.post("/models/{model_id}/status")
def update_model_status(
    model_id: str,
    request: ModelStatusRequest,
    db: Session = Depends(get_db),
    _actor: Phase5Actor = Depends(require_phase5_permission("activate")),
) -> dict:
    status = request.status.upper()
    if status == "ACTIVE":
        return activate_behavioral_model(db, model_id)
    if status not in {"SAVED", "ARCHIVED"}:
        raise HTTPException(status_code=400, detail="Model status must be SAVED, ACTIVE, or ARCHIVED.")
    model = db.get(MLBehavioralModel, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Behavioral model not found.")
    model.model_status = status
    db.commit()
    return get_behavioral_model(db, model_id)


@router.delete("/models/{model_id}")
def delete_model(
    model_id: str,
    db: Session = Depends(get_db),
    _actor: Phase5Actor = Depends(require_phase5_permission("delete")),
) -> dict:
    return delete_behavioral_model(db, model_id)
