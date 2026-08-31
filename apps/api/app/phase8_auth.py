from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import Header, HTTPException


PHASE8_PERMISSIONS = {
    "view": "phase8:view",
    "edit": "phase8:edit",
    "finalize": "phase8:finalize",
}


@dataclass(frozen=True)
class Phase8Actor:
    user_id: str
    permissions: frozenset[str]


def phase8_actor(x_user: str | None = Header(default=None), x_permissions: str | None = Header(default=None)) -> Phase8Actor:
    if x_permissions is None:
        permissions = frozenset(PHASE8_PERMISSIONS.values())
    else:
        permissions = frozenset(item.strip() for item in x_permissions.split(",") if item.strip())
    return Phase8Actor(user_id=(x_user or "local-user").strip() or "local-user", permissions=permissions)


def require_phase8_permission(permission_key: str) -> Callable:
    required = PHASE8_PERMISSIONS[permission_key]

    def dependency(x_user: str | None = Header(default=None), x_permissions: str | None = Header(default=None)) -> Phase8Actor:
        actor = phase8_actor(x_user=x_user, x_permissions=x_permissions)
        if required not in actor.permissions:
            raise HTTPException(status_code=403, detail=f"Missing permission: {required}")
        return actor

    return dependency
