from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .database import get_db
from .google_routes import (
    delete_google_routes_api_key,
    public_google_routes_configuration,
    save_google_routes_configuration,
    test_google_routes_connection,
)
from .phase6_auth import Phase6Actor, require_phase6_permission


router = APIRouter(prefix="/api/v1/settings/google-routes", tags=["Settings - Google Routes"])


class GoogleRoutesSettingsRequest(BaseModel):
    api_key: str | None = Field(default=None, max_length=512)
    routing_preference: str = "TRAFFIC_AWARE"
    cache_ttl_minutes: int = 60
    departure_time_bucket_minutes: int = 15
    default_depot_processing_minutes: int = 30
    default_spbu_service_minutes: int = 45
    default_return_processing_minutes: int = 15
    default_turnaround_buffer_minutes: int = 30
    default_route_duration_minutes: int = 120


@router.get("")
def google_routes_settings(
    depot_id: str | None = None,
    db: Session = Depends(get_db),
    _actor: Phase6Actor = Depends(require_phase6_permission("settings_view")),
) -> dict:
    return public_google_routes_configuration(db, depot_id=depot_id)


@router.put("")
def update_google_routes_settings(
    request: GoogleRoutesSettingsRequest,
    db: Session = Depends(get_db),
    actor: Phase6Actor = Depends(require_phase6_permission("settings_manage")),
) -> dict:
    return save_google_routes_configuration(db, request.model_dump(exclude_none=True), updated_by=actor.user_id)


@router.delete("/api-key")
def delete_google_routes_key(
    db: Session = Depends(get_db),
    actor: Phase6Actor = Depends(require_phase6_permission("settings_manage")),
) -> dict:
    return delete_google_routes_api_key(db, updated_by=actor.user_id)


@router.post("/test")
def test_google_routes(
    db: Session = Depends(get_db),
    actor: Phase6Actor = Depends(require_phase6_permission("settings_manage")),
) -> dict:
    return test_google_routes_connection(db, tested_by=actor.user_id)
