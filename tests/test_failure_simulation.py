"""Failure simulation: hooks are off by default and can only be reached deliberately."""

import typing

import pytest

from models.schemas import FailurePoint
from services import payment_service
from services.errors import ForbiddenError, ValidationFailedError
from services.payment_service import FAILURE_POINTS, PLSQL_APPLY_TRANSFER, SimulatedFailure

BODY = {"sender_account_id": 1, "receiver_account_id": 2, "amount": "10.00", "failure_point": "AFTER_DEBIT"}


def test_status_reports_disabled_by_default(client):
    body = client.get("/api/failure-simulation").json()
    assert body["enabled"] is False
    assert [p["name"] for p in body["failure_points"]] == list(FAILURE_POINTS)


def test_run_is_forbidden_when_disabled(client):
    response = client.post("/api/failure-simulation/run", json=BODY)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FAILURE_SIMULATION_DISABLED"


def test_unknown_failure_point_rejected_by_api(client, enable_failure_simulation):
    response = client.post("/api/failure-simulation/run", json={**BODY, "failure_point": "DROP_TABLES"})
    assert response.status_code == 422


def test_guard_function():
    payment_service.check_failure_point(None)  # ordinary payments are never blocked
    with pytest.raises(ForbiddenError):
        payment_service.check_failure_point("AFTER_DEBIT")


def test_guard_when_enabled(enable_failure_simulation):
    payment_service.check_failure_point("BEFORE_COMMIT")
    with pytest.raises(ValidationFailedError):
        payment_service.check_failure_point("NOT_A_POINT")


def test_api_and_service_agree_on_failure_points():
    assert set(typing.get_args(FailurePoint)) == set(FAILURE_POINTS)
    # The two in-block points are implemented inside the PL/SQL transfer block.
    assert "'AFTER_DEBIT'" in PLSQL_APPLY_TRANSFER and "'AFTER_BALANCE_UPDATES'" in PLSQL_APPLY_TRANSFER


def test_python_checkpoint_only_fires_at_its_own_point(monkeypatch):
    monkeypatch.setattr(
        payment_service, "fetch_one",
        lambda cur, sql, params: {"sender_balance": 900, "receiver_balance": 100, "ledger_entries": 0, "status": "INITIATED"},
    )
    ids = {"sender_id": 1, "receiver_id": 2, "transaction_id": 55}
    payment_service._python_checkpoint("BEFORE_COMMIT", None, None, ids)
    payment_service._python_checkpoint("BEFORE_COMMIT", "AFTER_DEBIT", None, ids)
    with pytest.raises(SimulatedFailure) as info:
        payment_service._python_checkpoint("BEFORE_COMMIT", "BEFORE_COMMIT", None, ids)
    assert info.value.transaction_id == 55
    assert info.value.snapshot["sender_balance"] == "900.00"
    assert info.value.snapshot["transaction_status"] == "INITIATED"


def test_public_payment_route_has_no_failure_hook():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("routes", "payments.py").read_text(encoding="utf-8")
    assert "failure_point" not in source
