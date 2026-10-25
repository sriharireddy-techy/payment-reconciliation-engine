"""Simulated gateway records, reconciliation batches and exceptions."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Path, Query

from models.schemas import (
    ExceptionUpdateRequest,
    ExternalRecordRequest,
    ReconciliationBatchRequest,
    SimulateExternalRequest,
)
from services import reconciliation_service

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])

ExceptionType = Literal[
    "AMOUNT_MISMATCH", "STATUS_MISMATCH", "MISSING_IN_GATEWAY", "MISSING_IN_INTERNAL", "DUPLICATE_EXTERNAL"
]
ResolutionStatus = Literal["UNRESOLVED", "MANUALLY_RESOLVED", "AUTO_RESOLVED", "WRITTEN_OFF"]


@router.post("/simulate-external", status_code=201, summary="Generate simulated (SIM-) gateway records")
def simulate_external(request: SimulateExternalRequest) -> dict:
    return reconciliation_service.generate_simulated_external(**request.model_dump())


@router.post("/external-records", status_code=201, summary="Add one simulated gateway record by hand")
def add_external_record(request: ExternalRecordRequest) -> dict:
    return reconciliation_service.add_external_record(**request.model_dump())


@router.get("/external-records", summary="List gateway records")
def list_external_records(
    gateway_id: int | None = Query(default=None, gt=0),
    settlement_date: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return reconciliation_service.list_external(
        gateway_id=gateway_id, settlement_date=settlement_date, limit=limit, offset=offset
    )


@router.post("/batches", status_code=201, summary="Run a reconciliation batch for one gateway and date")
def run_batch(request: ReconciliationBatchRequest) -> dict:
    return reconciliation_service.run_batch(gateway_id=request.gateway_id, batch_date=request.batch_date)


@router.get("/batches", summary="List reconciliation batches")
def list_batches(
    gateway_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return reconciliation_service.list_batches(gateway_id=gateway_id, limit=limit, offset=offset)


@router.get("/batches/{batch_id}", summary="One batch with its exceptions")
def get_batch(batch_id: int = Path(gt=0)) -> dict:
    return reconciliation_service.get_batch(batch_id)


@router.get("/exceptions", summary="List reconciliation exceptions")
def list_exceptions(
    batch_id: int | None = Query(default=None, gt=0),
    gateway_id: int | None = Query(default=None, gt=0),
    exception_type: ExceptionType | None = None,
    resolution_status: ResolutionStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return reconciliation_service.list_exceptions(
        batch_id=batch_id, gateway_id=gateway_id, exception_type=exception_type,
        resolution_status=resolution_status, limit=limit, offset=offset,
    )


@router.patch("/exceptions/{exception_id}", summary="Record a manual review decision (never moves money)")
def update_exception(request: ExceptionUpdateRequest, exception_id: int = Path(gt=0)) -> dict:
    return reconciliation_service.update_exception(
        exception_id, resolution_status=request.resolution_status, resolution_notes=request.resolution_notes
    )
