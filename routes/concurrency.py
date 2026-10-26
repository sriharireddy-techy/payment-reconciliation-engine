"""Concurrency lab."""

from fastapi import APIRouter

from models.schemas import ConcurrencyRunRequest
from services import concurrency_service

router = APIRouter(prefix="/api/concurrency", tags=["concurrency"])


@router.post("/run", summary="Send N simultaneous real payments from one account and verify consistency")
def run(request: ConcurrencyRunRequest) -> dict:
    return concurrency_service.run_concurrency_test(
        sender_account_id=request.sender_account_id,
        receiver_account_id=request.receiver_account_id,
        amount=request.amount,
        num_requests=request.num_requests,
        mode=request.mode,
        gateway_id=request.gateway_id,
    )
