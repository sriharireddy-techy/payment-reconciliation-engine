"""Pydantic request models. FastAPI rejects invalid input with HTTP 422 before
any service or database code runs. Front-end checks are only a convenience;
these models (and the database constraints) are what actually protect the data.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

# NUMBER(15,2): up to 13 digits before the point, exactly 2 after.
Money = Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=2)]
NonNegativeMoney = Annotated[Decimal, Field(ge=0, max_digits=15, decimal_places=2)]
IdempotencyKey = Annotated[str, Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9._:\-]+$")]

FailurePoint = Literal["AFTER_TRANSACTION_INSERT", "AFTER_DEBIT", "AFTER_BALANCE_UPDATES", "BEFORE_COMMIT"]
ExternalStatus = Literal["SUCCESS", "FAILURE", "PENDING", "REFUNDED"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _TwoAccounts(StrictModel):
    sender_account_id: PositiveInt
    receiver_account_id: PositiveInt

    @model_validator(mode="after")
    def _different_accounts(self):
        if self.sender_account_id == self.receiver_account_id:
            raise ValueError("sender_account_id and receiver_account_id must be different")
        return self


class PaymentRequest(_TwoAccounts):
    """A payment between two existing BANK_ACCOUNTS rows.

    `extra="forbid"` also guarantees that the failure-simulation hook cannot be
    smuggled into an ordinary payment request.
    """

    amount: Money
    idempotency_key: IdempotencyKey
    gateway_id: PositiveInt | None = None
    description: str | None = Field(default=None, max_length=150)


class ConcurrencyRunRequest(_TwoAccounts):
    amount: Money
    num_requests: int = Field(ge=2, le=100)
    mode: Literal["unique_keys", "same_key"] = "unique_keys"
    gateway_id: PositiveInt | None = None


class FailureSimulationRequest(_TwoAccounts):
    amount: Money
    failure_point: FailurePoint


class SimulateExternalRequest(StrictModel):
    gateway_id: PositiveInt
    settlement_date: date
    amount_mismatches: int = Field(default=1, ge=0, le=20)
    status_mismatches: int = Field(default=1, ge=0, le=20)
    missing_in_gateway: int = Field(default=1, ge=0, le=20)
    missing_in_internal: int = Field(default=1, ge=0, le=20)
    duplicates: int = Field(default=1, ge=0, le=20)
    seed: int | None = Field(default=None, ge=0)


class ExternalRecordRequest(StrictModel):
    gateway_id: PositiveInt
    settlement_date: date
    internal_transaction_ref: str | None = Field(default=None, max_length=36)
    reported_amount: Money
    reported_fee: NonNegativeMoney = Decimal("0")
    reported_status: ExternalStatus


class ReconciliationBatchRequest(StrictModel):
    gateway_id: PositiveInt
    batch_date: date


class ExceptionUpdateRequest(StrictModel):
    resolution_status: Literal["UNRESOLVED", "MANUALLY_RESOLVED", "WRITTEN_OFF"]
    resolution_notes: str | None = Field(default=None, max_length=500)
