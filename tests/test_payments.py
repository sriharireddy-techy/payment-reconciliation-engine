"""Payments: input validation, business rules, fees, state transitions, idempotency and the API contract.

Business rules are pure functions and are tested directly. The route tests
replace the service with a stub; real Oracle behaviour is in tests/integration.
"""

from decimal import Decimal

import pytest

from services import payment_service
from services.errors import ForbiddenError, ValidationFailedError
from services.payment_rules import (
    AccountState,
    GatewayState,
    ProfileState,
    Rejection,
    can_transition,
    compute_fee,
    evaluate_payment,
    rejected_description,
    rejection_code,
    same_request,
)

VALID = {"sender_account_id": 1, "receiver_account_id": 2, "amount": "250.00", "idempotency_key": "PAY-test-0001"}


def acct(account_id=1, balance="1000.00", currency="INR", status="ACTIVE"):
    return AccountState(account_id, Decimal(balance), currency, status)


PROFILE = ProfileState(Decimal("50000"), Decimal("10000"), "ACTIVE")
GATEWAY = GatewayState(1, "ACTIVE", Decimal("1.50"))


# ------------------------------------------------------------------ validation (HTTP 422)

@pytest.mark.parametrize(
    "change",
    [
        {"amount": "0"},
        {"amount": "-5.00"},
        {"amount": "10.555"},
        {"amount": "12345678901234.00"},
        {"amount": "abc"},
        {"receiver_account_id": 1},
        {"sender_account_id": 0},
        {"idempotency_key": "short"},
        {"idempotency_key": "has spaces in it"},
        {"description": "x" * 151},
        {"failure_point": "BEFORE_COMMIT"},
    ],
)
def test_invalid_payment_requests_are_rejected(client, change):
    response = client.post("/api/payments", json={**VALID, **change})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_missing_fields_are_rejected(client):
    response = client.post("/api/payments", json={"amount": "10.00"})
    assert response.status_code == 422
    fields = {d["field"] for d in response.json()["error"]["details"]}
    assert {"sender_account_id", "receiver_account_id", "idempotency_key"} <= fields


def test_valid_request_without_database_returns_503(client):
    response = client.post("/api/payments", json=VALID)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DB_NOT_CONFIGURED"


# ------------------------------------------------------------------ API contract (service stubbed)

def _outcome(status="SUCCESS", replayed=False, rejection=None):
    txn = {"transaction_id": 7, "status": status, "amount": "250.00", "currency": "INR"}
    return payment_service.PaymentOutcome(transaction=txn, replayed=replayed, rejection=rejection)


def test_successful_payment_returns_201(client, monkeypatch):
    seen = {}

    def fake(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return _outcome()

    monkeypatch.setattr(payment_service, "create_payment", fake)
    response = client.post("/api/payments", json=VALID)
    assert response.status_code == 201
    assert response.json()["transaction"]["transaction_id"] == 7
    assert seen["command"].amount == Decimal("250.00")
    assert seen["kwargs"] == {}  # the public route can never pass a failure point


def test_replayed_payment_returns_200(client, monkeypatch):
    monkeypatch.setattr(payment_service, "create_payment", lambda c, **k: _outcome(replayed=True))
    response = client.post("/api/payments", json=VALID)
    assert response.status_code == 200
    assert response.json()["replayed"] is True


def test_rejected_payment_returns_422_with_the_failed_record(client, monkeypatch):
    rejection = Rejection("INSUFFICIENT_FUNDS", "Not enough money.")
    monkeypatch.setattr(payment_service, "create_payment", lambda c, **k: _outcome("FAILED", rejection=rejection))
    response = client.post("/api/payments", json=VALID)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INSUFFICIENT_FUNDS"
    assert error["details"]["transaction"]["status"] == "FAILED"


# ------------------------------------------------------------------ service guards (before any DB access)

def test_failure_point_refused_when_simulation_disabled():
    command = payment_service.PaymentCommand(1, 2, Decimal("10"), "PAY-guard-0001")
    with pytest.raises(ForbiddenError):
        payment_service.create_payment(command, failure_point="AFTER_DEBIT")


def test_service_rejects_same_account_and_bad_amount_before_database():
    with pytest.raises(ValidationFailedError):
        payment_service.create_payment(payment_service.PaymentCommand(3, 3, Decimal("10"), "PAY-guard-0002"))
    with pytest.raises(ValidationFailedError):
        payment_service.create_payment(payment_service.PaymentCommand(3, 4, Decimal("0"), "PAY-guard-0003"))


# ------------------------------------------------------------------ business rules

def test_valid_payment_passes():
    assert evaluate_payment(Decimal("100"), acct(1), acct(2), PROFILE, Decimal("0"), GATEWAY) is None


def test_exact_balance_is_allowed():
    assert evaluate_payment(Decimal("1000.00"), acct(1), acct(2), None, Decimal("0"), None) is None


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"amount": Decimal("1000.01")}, "INSUFFICIENT_FUNDS"),
        ({"sender": acct(1, status="FROZEN")}, "SENDER_ACCOUNT_NOT_ACTIVE"),
        ({"receiver": acct(2, status="CLOSED")}, "RECEIVER_ACCOUNT_NOT_ACTIVE"),
        ({"receiver": acct(2, currency="USD")}, "CURRENCY_MISMATCH"),
        ({"gateway": GatewayState(1, "MAINTENANCE", Decimal("1"))}, "GATEWAY_NOT_ACTIVE"),
        ({"profile": ProfileState(Decimal("50000"), Decimal("10000"), "SUSPENDED")}, "PROFILE_SUSPENDED"),
        ({"profile": ProfileState(Decimal("50000"), Decimal("50"), "ACTIVE")}, "SINGLE_TX_LIMIT_EXCEEDED"),
        ({"profile": ProfileState(Decimal("1000"), Decimal("1000"), "ACTIVE"), "spent": Decimal("950")}, "DAILY_LIMIT_EXCEEDED"),
        ({"receiver": acct(1)}, "SAME_ACCOUNT"),
        ({"amount": Decimal("0")}, "INVALID_AMOUNT"),
    ],
)
def test_rejections(kwargs, code):
    args = {"amount": Decimal("100"), "sender": acct(1), "receiver": acct(2), "profile": PROFILE,
            "spent": Decimal("0"), "gateway": GATEWAY, **kwargs}
    rejection = evaluate_payment(args["amount"], args["sender"], args["receiver"], args["profile"], args["spent"], args["gateway"])
    assert rejection is not None and rejection.code == code


def test_daily_limit_boundary_is_inclusive():
    profile = ProfileState(Decimal("1000"), Decimal("1000"), "ACTIVE")
    assert evaluate_payment(Decimal("50"), acct(1), acct(2), profile, Decimal("950"), None) is None


def test_fee_uses_decimal_half_up_rounding():
    assert compute_fee(Decimal("1234.57"), GatewayState(1, "ACTIVE", Decimal("1.50"))) == Decimal("18.52")
    assert compute_fee(Decimal("0.10"), GatewayState(1, "ACTIVE", Decimal("5.00"))) == Decimal("0.01")
    assert compute_fee(Decimal("100.00"), None) == Decimal("0.00")
    assert compute_fee(Decimal("0.1"), GatewayState(1, "ACTIVE", Decimal("0.00"))) == Decimal("0.00")


def test_money_arithmetic_avoids_float_errors():
    # 0.1 + 0.2 is 0.30000000000000004 in floating point; balances must stay exact.
    sender = acct(1, balance="0.30")
    assert evaluate_payment(Decimal("0.1") + Decimal("0.2"), sender, acct(2), None, Decimal("0"), None) is None


def test_status_transitions():
    assert can_transition("INITIATED", "PROCESSING")
    assert can_transition("INITIATED", "FAILED")
    assert can_transition("PROCESSING", "SUCCESS")
    assert can_transition("SUCCESS", "REVERSED")
    assert not can_transition("SUCCESS", "FAILED")
    assert not can_transition("FAILED", "SUCCESS")
    assert not can_transition("INITIATED", "SUCCESS")
    assert not can_transition("REVERSED", "SUCCESS")


def test_same_request_detection_for_idempotent_retries():
    stored = {"sender_account_id": Decimal(1), "receiver_account_id": Decimal(2), "amount": Decimal("250"), "gateway_id": Decimal(3)}
    assert same_request(stored, 1, 2, Decimal("250.00"), 3)
    assert same_request(stored, 1, 2, Decimal("250.00"), None)  # gateway defaulted from the profile
    assert not same_request(stored, 1, 2, Decimal("251.00"), 3)
    assert not same_request(stored, 1, 9, Decimal("250.00"), 3)
    assert not same_request(stored, 1, 2, Decimal("250.00"), 4)


def test_rejection_reason_round_trip():
    text = rejected_description("INSUFFICIENT_FUNDS", "rent")
    assert text == "[REJECTED:INSUFFICIENT_FUNDS] rent"
    assert rejection_code(text) == "INSUFFICIENT_FUNDS"
    assert rejection_code("ordinary memo") is None
    assert rejection_code(None) is None
