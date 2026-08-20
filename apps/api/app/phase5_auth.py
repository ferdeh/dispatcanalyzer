from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import Header, HTTPException

from .phase5_constants import PHASE5_PERMISSIONS


@dataclass(frozen=True)
class Phase5Actor:
    user_id: str
    permissions: frozenset[str]


def phase5_actor(
    x_user: str | None = Header(default=None),
    x_permissions: str | None = Header(default=None),
) -> Phase5Actor:
    """Resolve the application's current lightweight identity seam.

    The repository has no authentication provider yet. Local requests therefore
    retain the existing permissive behavior, while deployments that supply
    X-User/X-Permissions receive granular Phase 5 enforcement without exposing a
    second login system. A future auth dependency can replace this function.
    """
    if x_permissions is None:
        permissions = frozenset(PHASE5_PERMISSIONS.values())
    else:
        permissions = frozenset(item.strip() for item in x_permissions.split(",") if item.strip())
    return Phase5Actor(user_id=(x_user or "local-user").strip() or "local-user", permissions=permissions)


def require_phase5_permission(permission_key: str) -> Callable:
    required = PHASE5_PERMISSIONS[permission_key]

    def dependency(
        x_user: str | None = Header(default=None),
        x_permissions: str | None = Header(default=None),
    ) -> Phase5Actor:
        actor = phase5_actor(x_user=x_user, x_permissions=x_permissions)
        if required not in actor.permissions:
            raise HTTPException(status_code=403, detail=f"Missing permission: {required}")
        return actor

    return dependency
