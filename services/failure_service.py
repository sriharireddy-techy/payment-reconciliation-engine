"""Failure simulation: break a real payment on purpose and prove the rollback.

The hooks live inside payment_service and are refused unless
ENABLE_FAILURE_SIMULATION=true. Nothing intermediate is committed to make the
failure visible: the "inside the transaction" numbers are read by the failing
transaction itself just before it raises, and only an audit record is written
afterwards, in its own transaction.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from config import get_settings
from database.connection import connection, fetch_one
from services import payment_service
from services.payment_service import FAILURE_POINTS, PaymentCommand, SimulatedFailure
from services.serialization import money, money_str, serialize_row

SQL_STATE = """
SELECT (SELECT current_balance FROM BANK_ACCOUNTS WHERE account_id = :sender_id) AS sender_balance,
       (SELECT current_balance FROM BANK_ACCOUNTS WHERE account_id = :receiver_id) AS receiver_balance,
       (SELECT COUNT(*) FROM LEDGER_ENTRIES WHERE account_id IN (:sender_id, :receiver_id)) AS ledger_entries,
       (SELECT COUNT(*) FROM PAYMENT_TRANSACTIONS WHERE idempotency_key = :idempotency_key) AS payments_with_key
  FROM DUAL
"""

SQL_ROLLBACK_AUDIT = """
SELECT log_id, table_name, action_type, record_id, account_id, transaction_id,
       performed_by, action_timestamp, details
  FROM AUDIT_LOGS
 WHERE table_name = 'PAYMENT_TRANSACTIONS'
   AND action_type = 'SECURITY'
   AND record_id = :record_id
 ORDER BY log_id DESC
 FETCH FIRST 1 ROWS ONLY
"""


def status() -> dict[str, Any]:
    return {
        "enabled": get_settings().enable_failure_simulation,
        "failure_points": [{"name": name, "description": text} for name, text in FAILURE_POINTS.items()],
    }


def _state(sender_id: int, receiver_id: int, key: str) -> dict[str, Any]:
    with connection() as conn:
        row = fetch_one(
            conn.cursor(), SQL_STATE, {"sender_id": sender_id, "receiver_id": receiver_id, "idempotency_key": key}
        ) or {}
    return {
        "sender_balance": money_str(row.get("sender_balance")),
        "receiver_balance": money_str(row.get("receiver_balance")),
        "ledger_entries": int(row.get("ledger_entries") or 0),
        "payments_with_key": int(row.get("payments_with_key") or 0),
    }


def run_failure_simulation(
    *, sender_account_id: int, receiver_account_id: int, amount: Decimal, failure_point: str
) -> dict[str, Any]:
    payment_service.check_failure_point(failure_point)  # refuses when disabled
    key = f"FAILSIM-{uuid.uuid4().hex[:16].upper()}"
    command = PaymentCommand(
        sender_account_id=sender_account_id,
        receiver_account_id=receiver_account_id,
        amount=money(amount),
        idempotency_key=key,
        description=f"Failure simulation at {failure_point}",
    )
    before = _state(sender_account_id, receiver_account_id, key)
    try:
        outcome = payment_service.create_payment(command, failure_point=failure_point)
    except SimulatedFailure as failure:
        after = _state(sender_account_id, receiver_account_id, key)
        with connection() as conn:
            audit = fetch_one(conn.cursor(), SQL_ROLLBACK_AUDIT, {"record_id": failure.transaction_id})
        checks = [
            {
                "name": "Balances restored",
                "passed": before["sender_balance"] == after["sender_balance"]
                and before["receiver_balance"] == after["receiver_balance"],
                "detail": f"Sender {before['sender_balance']} -> {after['sender_balance']}, "
                f"receiver {before['receiver_balance']} -> {after['receiver_balance']}.",
            },
            {
                "name": "No ledger entries survived",
                "passed": before["ledger_entries"] == after["ledger_entries"],
                "detail": f"Ledger rows on both accounts: {before['ledger_entries']} before, {after['ledger_entries']} after.",
            },
            {
                "name": "Payment row rolled back",
                "passed": after["payments_with_key"] == 0,
                "detail": f"Rows with idempotency key {key}: {after['payments_with_key']}.",
            },
            {
                "name": "Failure recorded in the audit log",
                "passed": audit is not None,
                "detail": "A SECURITY audit row was written after the rollback, in its own transaction.",
            },
        ]
        return {
            "result": "ROLLED_BACK",
            "failure_point": failure.point,
            "failure_point_description": FAILURE_POINTS[failure.point],
            "idempotency_key": key,
            "rolled_back_transaction_id": failure.transaction_id,
            "before": before,
            "inside_transaction": failure.snapshot,
            "after": after,
            "audit": serialize_row(audit),
            "checks": checks,
            "all_checks_passed": all(c["passed"] for c in checks),
        }
    # The business rules rejected the payment before the failure point was reached.
    return {
        "result": "REJECTED_BEFORE_FAILURE_POINT" if outcome.rejection else "COMPLETED",
        "failure_point": failure_point,
        "failure_point_description": FAILURE_POINTS[failure_point],
        "idempotency_key": key,
        "transaction": outcome.transaction,
        "rejection": {"code": outcome.rejection.code, "message": outcome.rejection.message} if outcome.rejection else None,
        "before": before,
        "after": _state(sender_account_id, receiver_account_id, key),
        "checks": [],
        "all_checks_passed": None,
    }
