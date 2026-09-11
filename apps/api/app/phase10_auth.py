from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import Depends, Header, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import IntegrationClient


INTEGRATION_NAME = "amt-scheduler"
DEFAULT_CLIENT_CODE = "AMT-SCHEDULER"
PHASE10_PERMISSIONS = {
    "view": "phase10.view",
    "manage_credentials": "phase10.manage_credentials",
    "view_logs": "phase10.view_logs",
    "try_api": "phase10.try_api",
}
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="AMT Scheduler Bearer Token",
    description="Token generated in Phase 10 API Access. Only its SHA-256 hash is stored.",
)


class IntegrationAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class IntegrationPrincipal:
    client_id: str
    client_code: str
    permissions: frozenset[str]


@dataclass(frozen=True)
class Phase10Actor:
    user_id: str
    permissions: frozenset[str]


def hash_integration_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_integration_token() -> str:
    return f"amt_{secrets.token_urlsafe(36)}"


def token_hint(token: str) -> str:
    return f"{token[:8]}...{token[-4:]}"


def phase10_actor(
    x_user: str | None = Header(default=None),
    x_permissions: str | None = Header(default=None),
) -> Phase10Actor:
    if x_permissions is None:
        permissions = frozenset(PHASE10_PERMISSIONS.values())
    else:
        permissions = frozenset(item.strip() for item in x_permissions.split(",") if item.strip())
    return Phase10Actor(user_id=(x_user or "local-user").strip() or "local-user", permissions=permissions)


def require_phase10_permission(permission_key: str) -> Callable:
    required = PHASE10_PERMISSIONS[permission_key]

    def dependency(
        x_user: str | None = Header(default=None),
        x_permissions: str | None = Header(default=None),
    ) -> Phase10Actor:
        actor = phase10_actor(x_user=x_user, x_permissions=x_permissions)
        if required not in actor.permissions:
            raise IntegrationAPIError(403, "FORBIDDEN", f"Missing permission: {required}")
        return actor

    return dependency


_rate_lock = threading.Lock()
_request_windows: dict[str, deque[datetime]] = defaultdict(deque)


def _enforce_rate_limit(client_id: str) -> None:
    limit = max(1, int(get_settings().phase10_rate_limit_per_minute))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=1)
    with _rate_lock:
        window = _request_windows[client_id]
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= limit:
            raise IntegrationAPIError(429, "RATE_LIMITED", "Request rate limit exceeded. Retry after one minute.")
        window.append(now)


def _authenticate_integration_client(request: Request, authorization: str | None, db: Session) -> IntegrationPrincipal:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise IntegrationAPIError(401, "UNAUTHORIZED", "A valid Bearer integration token is required.")
    digest = hash_integration_token(token.strip())
    # A normal deployment has one logical AMT Scheduler client. Iterate only
    # across this small credential set so hash comparison remains constant-time.
    candidates = db.scalars(
        select(IntegrationClient).where(
            IntegrationClient.integration_name == INTEGRATION_NAME,
            IntegrationClient.active.is_(True),
            IntegrationClient.token_hash.is_not(None),
        )
    ).all()
    client = next((row for row in candidates if hmac.compare_digest(row.token_hash or "", digest)), None)
    if not client:
        raise IntegrationAPIError(401, "UNAUTHORIZED", "A valid Bearer integration token is required.")
    permissions = frozenset(str(item) for item in (client.permissions or []))
    if "read" not in permissions:
        raise IntegrationAPIError(403, "FORBIDDEN", "The integration client does not have read access.")
    _enforce_rate_limit(client.id)
    now = datetime.now(timezone.utc)
    if client.last_used_at is None or (now - (client.last_used_at if client.last_used_at.tzinfo else client.last_used_at.replace(tzinfo=timezone.utc))) >= timedelta(minutes=1):
        client.last_used_at = now
        db.commit()
    request.state.integration_client_id = client.id
    request.state.integration_client_code = client.client_code
    return IntegrationPrincipal(client_id=client.id, client_code=client.client_code, permissions=permissions)


def require_integration_client(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: Session = Depends(get_db),
) -> IntegrationPrincipal:
    authorization = f"{credentials.scheme} {credentials.credentials}" if credentials else None
    return _authenticate_integration_client(request, authorization, db)
