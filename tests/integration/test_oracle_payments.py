"""Real Oracle: connection, schema, payments, ledger, idempotency, rollback."""

from decimal import Decimal

import pytest

import config
from database.connection import connection, fetch_all
from database.schema import verify_schema
from services import failure_service, ledger_service
from services.errors import ConflictError, NotFoundError
from services.payment_service import FAILURE_POINTS, PaymentCommand, create_payment

SQL_LEDGER = "SELECT entry_type, account_id, amount FROM LEDGER_ENTRIES WHERE transaction_id = :t ORDER BY entry_type"
SQL_AUDIT = "SELECT table_name, action_type FROM AUDIT_LOGS WHERE transaction_id = :t"
SQL_BY_KEY = "SELECT COUNT(*) AS n FROM PAYMENT_TRANSACTIONS WHERE idempotency_key = :k"


def rows(sql, params):
    with connection() as conn:
        return fetch_all(conn.cursor(), sql, params)


def test_connection_and_schema(oracle):
    result = verify_schema()
    assert result["ok"], result
    assert all(c["present"] for c in result["constraints"]), result["constraints"]


def test_successful_payment_updates_balances_ledger_and_audit(make_account, balance, key):
    sender, receiver = make_account("1000.00"), make_account("0.00")
    outcome = create_payment(PaymentCommand(sender, receiver, Decimal("250.50"), key()))
    txn_id = outcome.transaction["transaction_id"]
    assert outcome.status == "SUCCESS" and not outcome.replayed
    assert balance(sender) == Decimal("749.50") and balance(receiver) == Decimal("250.50")
    ledger = rows(SQL_LEDGER, {"t": txn_id})
    assert [(r["entry_type"], int(r["account_id"]), r["amount"]) for r in ledger] == [
        ("CR", receiver, Decimal("250.5")), ("DR", sender, Decimal("250.5"))]
    audit = {(r["table_name"], r["action_type"]) for r in rows(SQL_AUDIT, {"t": txn_id})}
    assert {("PAYMENT_TRANSACTIONS", "INSERT"), ("BANK_ACCOUNTS", "UPDATE")} <= audit


def test_insufficient_funds_is_recorded_as_failed_without_moving_money(make_account, balance, key):
    sender, receiver = make_account("100.00"), make_account("0.00")
    outcome = create_payment(PaymentCommand(sender, receiver, Decimal("100.01"), key()))
    assert outcome.status == "FAILED" and outcome.rejection.code == "INSUFFICIENT_FUNDS"
    assert balance(sender) == Decimal("100") and balance(receiver) == Decimal("0")
    assert rows(SQL_LEDGER, {"t": outcome.transaction["transaction_id"]}) == []


def test_unknown_account_creates_nothing(make_account, key):
    sender = make_account("100.00")
    k = key()
    with pytest.raises(NotFoundError):
        create_payment(PaymentCommand(sender, 999_999_999, Decimal("1.00"), k))
    assert rows(SQL_BY_KEY, {"k": k})[0]["n"] == 0


def test_frozen_receiver_is_rejected(make_account, balance, key):
    sender, receiver = make_account("500.00"), make_account("0.00", status="FROZEN")
    outcome = create_payment(PaymentCommand(sender, receiver, Decimal("10.00"), key()))
    assert outcome.rejection.code == "RECEIVER_ACCOUNT_NOT_ACTIVE"
    assert balance(sender) == Decimal("500")


def test_single_transaction_limit(make_account, key):
    sender = make_account("5000.00", single_tx_limit="100.00")
    outcome = create_payment(PaymentCommand(sender, make_account("0"), Decimal("100.01"), key()))
    assert outcome.rejection.code == "SINGLE_TX_LIMIT_EXCEEDED"


def test_same_key_is_processed_once(make_account, balance, key):
    sender, receiver = make_account("1000.00"), make_account("0.00")
    k = key()
    first = create_payment(PaymentCommand(sender, receiver, Decimal("300.00"), k))
    second = create_payment(PaymentCommand(sender, receiver, Decimal("300.00"), k))
    assert second.replayed and second.transaction["transaction_id"] == first.transaction["transaction_id"]
    assert balance(sender) == Decimal("700")
    assert rows(SQL_BY_KEY, {"k": k})[0]["n"] == 1


def test_key_reuse_with_different_details_is_refused(make_account, key):
    sender, receiver = make_account("1000.00"), make_account("0.00")
    k = key()
    create_payment(PaymentCommand(sender, receiver, Decimal("10.00"), k))
    with pytest.raises(ConflictError):
        create_payment(PaymentCommand(sender, receiver, Decimal("11.00"), k))


def test_ledger_rules_hold_for_new_payments(make_account, key):
    sender, receiver = make_account("1000.00"), make_account("0.00")
    txn_id = create_payment(PaymentCommand(sender, receiver, Decimal("5.00"), key())).transaction["transaction_id"]
    report = ledger_service.integrity_report(max_items=10_000)
    assert report["global"]["balanced"]
    assert txn_id not in {v["transaction_id"] for v in report["violations"]}


@pytest.mark.parametrize("point", list(FAILURE_POINTS))
def test_each_failure_point_rolls_back_completely(point, make_account, balance, monkeypatch):
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")
    config.get_settings.cache_clear()
    try:
        sender, receiver = make_account("1000.00"), make_account("0.00")
        result = failure_service.run_failure_simulation(
            sender_account_id=sender, receiver_account_id=receiver, amount=Decimal("100.00"), failure_point=point
        )
    finally:
        monkeypatch.delenv("ENABLE_FAILURE_SIMULATION")
        config.get_settings.cache_clear()
    assert result["result"] == "ROLLED_BACK"
    assert result["all_checks_passed"], result["checks"]
    assert balance(sender) == Decimal("1000") and balance(receiver) == Decimal("0")
    if point != "AFTER_TRANSACTION_INSERT":
        assert result["inside_transaction"]["sender_balance"] == "900.00"  # the debit really happened, uncommitted
