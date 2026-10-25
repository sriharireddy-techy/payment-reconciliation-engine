"""Audit log viewer."""

from typing import Literal

from fastapi import APIRouter, Query

from services import audit_service

router = APIRouter(prefix="/api", tags=["audit"])


@router.get("/audit-logs", summary="Search the audit trail")
def list_audit_logs(
    table_name: str | None = Query(default=None, max_length=50, pattern=r"^[A-Za-z_]+$"),
    action_type: Literal["INSERT", "UPDATE", "DELETE", "SECURITY"] | None = None,
    account_id: int | None = Query(default=None, gt=0),
    transaction_id: int | None = Query(default=None, gt=0),
    search: str | None = Query(default=None, max_length=100, description="Text inside the details, e.g. an event name"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return audit_service.list_logs(
        table_name=table_name, action_type=action_type, account_id=account_id,
        transaction_id=transaction_id, search=search, limit=limit, offset=offset,
    )
