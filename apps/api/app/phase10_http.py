from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from time import perf_counter

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .database import SessionLocal
from .models import IntegrationAPILog
from .phase10_auth import INTEGRATION_NAME, IntegrationAPIError
from .phase10_routes import BASE_PATH


logger = logging.getLogger(__name__)


def request_id() -> str:
    return f"REQ-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8].upper()}"


def _safe_request_id(candidate: str | None) -> str:
    if candidate and re.fullmatch(r"[A-Za-z0-9._:-]{8,80}", candidate):
        return candidate
    return request_id()


def canonical_error(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    request.state.error_code = code
    request.state.error_message = message
    correlation_id = getattr(request.state, "request_id", request_id())
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "request_id": correlation_id}},
        headers={"X-Request-ID": correlation_id},
    )


async def integration_error_handler(request: Request, exc: IntegrationAPIError) -> JSONResponse:
    return canonical_error(request, exc.status_code, exc.code, exc.message)


async def integration_validation_error_handler(request: Request, exc: RequestValidationError):
    if not request.url.path.startswith(BASE_PATH):
        from fastapi.exception_handlers import request_validation_exception_handler

        return await request_validation_exception_handler(request, exc)
    message = "; ".join(
        f"{'.'.join(str(item) for item in error.get('loc', [])[1:])}: {error.get('msg', 'Invalid value')}"
        for error in exc.errors()
    ) or "Request validation failed."
    return canonical_error(request, 422, "VALIDATION_ERROR", message)


def _session_factory(request: Request):
    return getattr(request.app.state, "phase10_session_factory", SessionLocal)


async def phase10_request_middleware(request: Request, call_next):
    if not request.url.path.startswith(BASE_PATH):
        return await call_next(request)
    correlation_id = _safe_request_id(request.headers.get("X-Request-ID"))
    request.state.request_id = correlation_id
    started_at = datetime.now(timezone.utc)
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        response = canonical_error(request, 500, "INTERNAL_ERROR", "The connector could not complete the request.")
    response.headers["X-Request-ID"] = correlation_id

    # Console activity is governed by application RBAC and intentionally kept
    # out of the external AMT Scheduler traffic metrics.
    if "/console" not in request.url.path:
        duration_ms = max(0, round((perf_counter() - started) * 1000))
        query_params = {
            key: values[0] if len(values) == 1 else values
            for key in request.query_params.keys()
            if "token" not in key.lower() and "secret" not in key.lower() and "authorization" not in key.lower()
            for values in [request.query_params.getlist(key)]
        }
        forwarded = request.headers.get("x-forwarded-for")
        ip_address = forwarded.split(",", 1)[0].strip() if forwarded else (request.client.host if request.client else None)
        factory = _session_factory(request)
        try:
            with factory() as db:
                db.add(
                    IntegrationAPILog(
                        id=uuid.uuid4().hex,
                        request_id=correlation_id,
                        integration_name=INTEGRATION_NAME,
                        client_id=getattr(request.state, "integration_client_id", None),
                        http_method=request.method,
                        endpoint=request.url.path,
                        query_params=query_params,
                        requested_at=started_at,
                        response_status=response.status_code,
                        response_time_ms=duration_ms,
                        record_count=getattr(request.state, "record_count", None),
                        ip_address=ip_address,
                        error_code=getattr(request.state, "error_code", None),
                        error_message=getattr(request.state, "error_message", None),
                    )
                )
                db.commit()
        except Exception:
            # Observability must never turn a successful read into a connector
            # failure. Database logging errors remain in the application log.
            logger.exception("Could not persist the sanitized Phase 10 request log.")
    return response
