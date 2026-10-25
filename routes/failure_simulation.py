"""Failure simulation (refused unless ENABLE_FAILURE_SIMULATION=true)."""

from fastapi import APIRouter

from models.schemas import FailureSimulationRequest
from services import failure_service

router = APIRouter(prefix="/api/failure-simulation", tags=["failure simulation"])


@router.get("", summary="Is failure simulation enabled, and which failure points exist?")
def status() -> dict:
    return failure_service.status()


@router.post("/run", summary="Run a payment that fails on purpose at the chosen point, then verify the rollback")
def run(request: FailureSimulationRequest) -> dict:
    return failure_service.run_failure_simulation(
        sender_account_id=request.sender_account_id,
        receiver_account_id=request.receiver_account_id,
        amount=request.amount,
        failure_point=request.failure_point,
    )
