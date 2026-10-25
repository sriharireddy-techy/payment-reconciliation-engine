"""Accounts and payment gateways (read-only)."""

from fastapi import APIRouter, Path

from services import account_service

router = APIRouter(prefix="/api", tags=["accounts"])


@router.get("/accounts", summary="All bank accounts with masked numbers, owners and limits")
def list_accounts() -> dict:
    return {"items": account_service.list_accounts()}


@router.get("/accounts/{account_id}", summary="One account with today's spending and ledger totals")
def get_account(account_id: int = Path(gt=0)) -> dict:
    return account_service.get_account(account_id)


@router.get("/gateways", summary="Payment gateways (secrets are never returned)")
def list_gateways() -> dict:
    return {"items": account_service.list_gateways()}
