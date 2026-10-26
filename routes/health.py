"""Liveness and database health (connection test + schema verification)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config import get_settings
from database.connection import ping
from database.schema import verify_schema
from services.errors import AppError

router = APIRouter(tags=["health"])


@router.get("/health", summary="Is the web application running?")
def health() -> dict:
    return {"status": "ok", "service": "payment-reconciliation-engine"}


@router.get("/api/health/database", summary="Test the Oracle connection and verify the schema (read-only)")
def database_health():
    settings = get_settings()
    flags = {
        "failure_simulation_enabled": settings.enable_failure_simulation,
        "db_pool_max": settings.db_pool_max,
        "connection_method": "ORACLE_DSN" if settings.oracle_dsn else "ORACLE_HOST/PORT/SERVICE_NAME",
    }
    if not settings.is_db_configured:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_configured",
                "configured": False,
                "connected": False,
                "missing": settings.missing_db_settings,
                "message": "Copy .env.example to .env, fill in the FreeSQL connection details and restart.",
                **flags,
            },
        )
    try:
        info = ping()
        schema = verify_schema()
    except AppError as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "configured": True, "connected": False, "error": exc.to_dict(), **flags},
        )
    return {
        "status": "ok" if schema["ok"] else "schema_mismatch",
        "configured": True,
        "connected": True,
        **info,
        "schema": schema,
        **flags,
    }
