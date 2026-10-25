"""Transaction history, details and the double-entry ledger."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Path, Query

from services import ledger_service, transaction_service

router = APIRouter(prefix="/api", tags=["transactions & ledger"])

Status = Literal["INITIATED", "PROCESSING", "SUCCESS", "FAILED", "REVERSED"]


@router.get("/transactions", summary="Search and filter payment history")
def list_transactions(
    status: Status | None = None,
    gateway_id: int | None = Query(default=None, gt=0),
    account_id: int | None = Query(default=None, gt=0),
    search: str | None = Query(default=None, max_length=100, description="Reference, key, ID or name"),
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return transaction_service.list_transactions(
        status=status, gateway_id=gateway_id, account_id=account_id, search=search,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset,
    )


@router.get("/transactions/{transaction_id}", summary="Full details of one payment")
def get_transaction(transaction_id: int = Path(gt=0)) -> dict:
    return transaction_service.get_transaction(transaction_id)


@router.get("/transactions/{transaction_id}/ledger", summary="Ledger entries of one payment")
def transaction_ledger(transaction_id: int = Path(gt=0)) -> dict:
    return ledger_service.entries_for_transaction(transaction_id)


@router.get("/ledger", summary="Browse ledger entries")
def list_ledger(
    account_id: int | None = Query(default=None, gt=0),
    transaction_id: int | None = Query(default=None, gt=0),
    entry_type: Literal["DR", "CR"] | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return ledger_service.list_entries(
        account_id=account_id, transaction_id=transaction_id, entry_type=entry_type, limit=limit, offset=offset
    )


@router.get("/ledger/integrity", summary="Check that debits equal credits, globally and per payment")
def ledger_integrity() -> dict:
    return ledger_service.integrity_report()
