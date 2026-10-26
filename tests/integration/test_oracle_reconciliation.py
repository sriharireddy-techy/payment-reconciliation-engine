"""Real Oracle: simulated gateway file -> reconciliation batch -> repeat run."""

import datetime as dt
from decimal import Decimal

from database.connection import connection, fetch_one
from services import reconciliation_service as recon
from services.payment_service import PaymentCommand, create_payment

SQL_TODAY = "SELECT TRUNC(SYSDATE) AS today FROM DUAL"
SQL_BALANCE = "SELECT current_balance FROM BANK_ACCOUNTS WHERE account_id = :account_id"


def test_reconciliation_finds_injected_discrepancies_and_is_repeatable(make_account, make_gateway, key):
    gateway = make_gateway()
    sender, receiver = make_account("100000.00"), make_account("0.00")
    for amount in ("100.00", "200.00", "300.00", "400.00", "500.00", "600.00"):
        create_payment(PaymentCommand(sender, receiver, Decimal(amount), key(), gateway_id=gateway))
    with connection() as conn:
        today = fetch_one(conn.cursor(), SQL_TODAY)["today"].date()  # database date, not the laptop's
        balance_before = fetch_one(conn.cursor(), SQL_BALANCE, {"account_id": sender})["current_balance"]

    generated = recon.generate_simulated_external(
        gateway_id=gateway, settlement_date=today, amount_mismatches=1, status_mismatches=1,
        missing_in_gateway=1, missing_in_internal=1, duplicates=1, seed=5,
    )
    assert all(r["external_reference_id"].startswith("SIM-") for r in generated["created"])

    first = recon.run_batch(gateway_id=gateway, batch_date=today)
    found = sorted(x["exception_type"] for x in first["new_exceptions"])
    assert found == ["AMOUNT_MISMATCH", "MISSING_IN_GATEWAY", "MISSING_IN_INTERNAL", "STATUS_MISMATCH"]
    assert first["matched_records"] == 3
    assert len(first["duplicates"]) == 1
    assert first["batch"]["batch_status"] == "COMPLETED"

    second = recon.run_batch(gateway_id=gateway, batch_date=today)
    assert second["new_exceptions"] == []
    assert len(second["previously_recorded"]) == 4

    with connection() as conn:  # reconciliation never moves money
        assert fetch_one(conn.cursor(), SQL_BALANCE, {"account_id": sender})["current_balance"] == balance_before

    exception_id = recon.get_batch(first["batch"]["batch_id"])["exceptions"][0]["exception_id"]
    updated = recon.update_exception(exception_id, resolution_status="MANUALLY_RESOLVED", resolution_notes="IT review")
    assert updated["resolution_status"] == "MANUALLY_RESOLVED"


def test_batch_for_unknown_gateway_is_not_found(oracle):
    import pytest

    from services.errors import NotFoundError

    with pytest.raises(NotFoundError):
        recon.run_batch(gateway_id=999_999_999, batch_date=dt.date.today())
