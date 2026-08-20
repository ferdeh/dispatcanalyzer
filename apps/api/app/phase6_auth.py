from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import Header, HTTPException

from .phase6_constants import PHASE6_PERMISSIONS


@dataclass(frozen=True)
class Phase6Actor:
    user_id: str
    permissions: frozenset[str]


def phase6_actor(x_user: str | None = Header(default=None), x_permissions: str | None = Header(default=None)) -> Phase6Actor:
    if x_permissions is None:
        permissions = frozenset(PHASE6_PERMISSIONS.values())
    else:
        permissions = frozenset(item.strip() for item in x_permissions.split(",") if item.strip())
    return Phase6Actor(user_id=(x_user or "local-user").strip() or "local-user", permissions=permissions)


def require_phase6_permission(permission_key: str) -> Callable:
    required = PHASE6_PERMISSIONS[permission_key]

    def dependency(x_user: str | None = Header(default=None), x_permissions: str | None = Header(default=None)) -> Phase6Actor:
        actor = phase6_actor(x_user=x_user, x_permissions=x_permissions)
        if required not in actor.permissions:
            raise HTTPException(status_code=403, detail=f"Missing permission: {required}")
        return actor

    return dependency
