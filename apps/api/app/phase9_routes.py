from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .database import get_db
from .phase9_alignment import (
    create_evaluation,
    get_evaluation,
    get_evaluation_row,
    get_latest_evaluation_for_route,
    list_evaluation_rows,
    list_evaluation_trips,
    list_route_options,
)


router = APIRouter(
    prefix="/api/v1/phase9/route-model-alignment",
    tags=["Phase 9 - Route-Model Alignment Evaluation"],
)


class EvaluationCreateRequest(BaseModel):
    depot_id: str
    route_version_id: str


@router.get("/routes")
def phase9_route_options(
    depot_id: str = Query(...),
    db: Session = Depends(get_db),
) -> dict:
    return list_route_options(db, depot_id)


@router.post("/evaluations", status_code=status.HTTP_201_CREATED)
def create_phase9_evaluation(
    request: EvaluationCreateRequest,
    db: Session = Depends(get_db),
) -> dict:
    return create_evaluation(db, request.depot_id, request.route_version_id)


@router.get("/evaluations/by-route/{route_version_id}")
def latest_phase9_evaluation_for_route(
    route_version_id: str,
    db: Session = Depends(get_db),
) -> dict:
    return get_latest_evaluation_for_route(db, route_version_id)


@router.get("/evaluations/{evaluation_run_id}")
def phase9_evaluation(
    evaluation_run_id: str,
    db: Session = Depends(get_db),
) -> dict:
    return get_evaluation(db, evaluation_run_id)


@router.get("/evaluations/{evaluation_run_id}/rows")
def phase9_evaluation_rows(
    evaluation_run_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25),
    search: str | None = Query(default=None),
    sort_by: str = Query(default="planned_gate_out"),
    sort_direction: str = Query(default="asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
) -> dict:
    return list_evaluation_rows(
        db,
        evaluation_run_id,
        page=page,
        page_size=page_size,
        search=search,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )


@router.get("/evaluations/{evaluation_run_id}/rows/{evaluation_row_id}")
def phase9_evaluation_row(
    evaluation_run_id: str,
    evaluation_row_id: str,
    db: Session = Depends(get_db),
) -> dict:
    return get_evaluation_row(db, evaluation_run_id, evaluation_row_id)


@router.get("/evaluations/{evaluation_run_id}/trips")
def phase9_evaluation_trips(
    evaluation_run_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10),
    search: str | None = Query(default=None),
    sort_by: str = Query(default="gate_out"),
    sort_direction: str = Query(default="asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
) -> dict:
    return list_evaluation_trips(
        db,
        evaluation_run_id,
        page=page,
        page_size=page_size,
        search=search,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )
