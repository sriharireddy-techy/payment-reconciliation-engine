"""Create payments (atomic, idempotent)."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from models.schemas import PaymentRequest
from services import payment_service

router = APIRouter(prefix="/api", tags=["payments"])


@router.post(
    "/payments",
    status_code=201,
    summary="Make a payment between two accounts",
    responses={
        200: {"description": "Replay: the idempotency key was already used for this same payment"},
        409: {"description": "Idempotency key reused for different details, or account busy"},
        422: {"description": "Invalid input, or the payment was rejected (recorded as FAILED)"},
    },
)
def create_payment(request: PaymentRequest):
    outcome = payment_service.create_payment(
        payment_service.PaymentCommand(
            sender_account_id=request.sender_account_id,
            receiver_account_id=request.receiver_account_id,
            amount=request.amount,
            idempotency_key=request.idempotency_key,
            gateway_id=request.gateway_id,
            description=request.description,
        )
    )
    if outcome.rejection is not None:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": outcome.rejection.code,
                    "message": outcome.rejection.message,
                    "details": {"transaction": outcome.transaction, "replayed": outcome.replayed},
                }
            },
        )
    body = {
        "outcome": outcome.status,
        "replayed": outcome.replayed,
        "transaction": outcome.transaction,
        "message": (
            "Duplicate request: returning the original payment, nothing was charged again."
            if outcome.replayed
            else "Payment completed."
        ),
    }
    return JSONResponse(status_code=200 if outcome.replayed else 201, content=body)
