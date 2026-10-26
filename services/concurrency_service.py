"""Concurrency lab: fire N real payment requests at the same account at once.

Each request runs in its own thread with its own pooled Oracle connection and
goes through exactly the same create_payment() code as a normal payment. A
threading.Barrier releases them together; correctness comes from Oracle row
locks, the guarded UPDATE and the CHECK constraint, not from any Python lock.
Afterwards the database is read back and checked for consistency.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any

from config import get_settings
from database.connection import connection, fetch_all
from services import payment_service
from services.errors import AppError, NotFoundError, ValidationFailedError
from services.serialization import money, money_str

log = logging.getLogger(__name__)

MODES = ("unique_keys", "same_key")

SQL_BALANCES = """
SELECT account_id, current_balance, currency, account_status
  FROM BANK_ACCOUNTS
 WHERE account_id IN (:sender_id, :receiver_id)
"""

# All payments created by one run share the idempotency-key prefix CONC-<run id>-.
SQL_RUN_TRANSACTIONS = """
SELECT t.transaction_id, t.idempotency_key, t.status, t.amount,
       NVL(SUM(CASE WHEN l.entry_type = 'DR' AND l.account_id = t.sender_account_id THEN l.amount END), 0) AS dr_total,
       NVL(SUM(CASE WHEN l.entry_type = 'CR' AND l.account_id = t.receiver_account_id THEN l.amount END), 0) AS cr_total,
       COUNT(l.entry_id) AS entry_count
  FROM PAYMENT_TRANSACTIONS t
  LEFT JOIN LEDGER_ENTRIES l ON l.transaction_id = t.transaction_id
 WHERE t.idempotency_key LIKE :key_prefix
 GROUP BY t.transaction_id, t.idempotency_key, t.status, t.amount
 ORDER BY t.transaction_id
"""


def _balances(sender_id: int, receiver_id: int) -> dict[int, dict[str, Any]]:
    with connection() as conn:
        rows = fetch_all(conn.cursor(), SQL_BALANCES, {"sender_id": sender_id, "receiver_id": receiver_id})
    return {int(r["account_id"]): r for r in rows}


def run_concurrency_test(
    *,
    sender_account_id: int,
    receiver_account_id: int,
    amount: Decimal,
    num_requests: int,
    mode: str = "unique_keys",
    gateway_id: int | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if mode not in MODES:
        raise ValidationFailedError(f"mode must be one of {', '.join(MODES)}.")
    if not 2 <= num_requests <= settings.concurrency_max_requests:
        raise ValidationFailedError(
            f"num_requests must be between 2 and {settings.concurrency_max_requests}."
        )
    if sender_account_id == receiver_account_id:
        raise ValidationFailedError("Sender and receiver accounts must be different.", code="SAME_ACCOUNT")
    amount = money(amount)

    before = _balances(sender_account_id, receiver_account_id)
    for account_id in (sender_account_id, receiver_account_id):
        if account_id not in before:
            raise NotFoundError(f"Account {account_id} does not exist.", code="ACCOUNT_NOT_FOUND")

    run_id = uuid.uuid4().hex[:10].upper()
    key_prefix = f"CONC-{run_id}-"
    barrier = threading.Barrier(num_requests)
    epoch: list[float] = []

    def worker(index: int) -> dict[str, Any]:
        key = key_prefix + ("SAME" if mode == "same_key" else f"{index + 1:03d}")
        command = payment_service.PaymentCommand(
            sender_account_id=sender_account_id,
            receiver_account_id=receiver_account_id,
            amount=amount,
            idempotency_key=key,
            gateway_id=gateway_id,
            description=f"Concurrency lab run {run_id}",
        )
        try:
            barrier.wait(timeout=30)
        except threading.BrokenBarrierError:
            pass
        start = time.perf_counter()
        result: dict[str, Any] = {"request": index + 1, "idempotency_key": key}
        try:
            outcome = payment_service.create_payment(command)
            result.update(
                outcome="REPLAYED" if outcome.replayed else outcome.status,
                transaction_id=outcome.transaction["transaction_id"],
                reason=outcome.rejection.code if outcome.rejection else None,
            )
        except AppError as exc:
            result.update(outcome="ERROR", transaction_id=None, reason=exc.code, message=exc.message)
        except Exception:  # keep the other threads' results even if one crashes
            log.exception("Concurrency worker %s failed", index + 1)
            result.update(outcome="ERROR", transaction_id=None, reason="UNEXPECTED_ERROR")
        end = time.perf_counter()
        origin = epoch[0] if epoch else start
        result["started_ms"] = round((start - origin) * 1000, 1)
        result["finished_ms"] = round((end - origin) * 1000, 1)
        result["duration_ms"] = round((end - start) * 1000, 1)
        return result

    epoch.append(time.perf_counter())
    with ThreadPoolExecutor(max_workers=num_requests, thread_name_prefix="conc") as pool:
        results = list(pool.map(worker, range(num_requests)))
    elapsed_ms = round((time.perf_counter() - epoch[0]) * 1000, 1)

    after = _balances(sender_account_id, receiver_account_id)
    with connection() as conn:
        transactions = fetch_all(conn.cursor(), SQL_RUN_TRANSACTIONS, {"key_prefix": key_prefix + "%"})

    checks = evaluate_consistency(
        amount=amount,
        mode=mode,
        sender_before=before[sender_account_id]["current_balance"],
        sender_after=after[sender_account_id]["current_balance"],
        receiver_before=before[receiver_account_id]["current_balance"],
        receiver_after=after[receiver_account_id]["current_balance"],
        results=results,
        transactions=transactions,
    )
    counts = {name: sum(1 for r in results if r["outcome"] == name) for name in ("SUCCESS", "FAILED", "REPLAYED", "ERROR")}
    return {
        "run_id": run_id,
        "mode": mode,
        "amount": money_str(amount),
        "currency": before[sender_account_id]["currency"],
        "attempted": num_requests,
        "succeeded": counts["SUCCESS"],
        "rejected": counts["FAILED"],
        "replayed": counts["REPLAYED"],
        "errors": counts["ERROR"],
        "elapsed_ms": elapsed_ms,
        "db_pool_max": settings.db_pool_max,
        "sender": {
            "account_id": sender_account_id,
            "balance_before": money_str(before[sender_account_id]["current_balance"]),
            "balance_after": money_str(after[sender_account_id]["current_balance"]),
        },
        "receiver": {
            "account_id": receiver_account_id,
            "balance_before": money_str(before[receiver_account_id]["current_balance"]),
            "balance_after": money_str(after[receiver_account_id]["current_balance"]),
        },
        "transactions_created": len(transactions),
        "checks": checks,
        "all_checks_passed": all(c["passed"] for c in checks),
        "requests": sorted(results, key=lambda r: r["started_ms"]),
    }


def evaluate_consistency(
    *,
    amount: Decimal,
    mode: str,
    sender_before: Any,
    sender_after: Any,
    receiver_before: Any,
    receiver_after: Any,
    results: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pure consistency checks on a finished run (also used by the unit tests)."""
    amount = money(amount)
    s_before, s_after = money(sender_before), money(sender_after)
    r_before, r_after = money(receiver_before), money(receiver_after)
    db_success = [t for t in transactions if t["status"] == "SUCCESS"]
    db_failed = [t for t in transactions if t["status"] == "FAILED"]
    moved = amount * len(db_success)
    reached_db = sum(1 for r in results if r["outcome"] in ("SUCCESS", "FAILED"))
    api_success = sum(1 for r in results if r["outcome"] == "SUCCESS")
    affordable = int(s_before // amount) if amount > 0 else 0

    def check(name: str, passed: bool, detail: str) -> dict[str, Any]:
        return {"name": name, "passed": bool(passed), "detail": detail}

    checks = [
        check("Sender balance never negative", s_after >= 0, f"Final sender balance is {s_after:.2f}."),
        check(
            "No overspending",
            len(db_success) <= affordable,
            f"{len(db_success)} payment(s) of {amount:.2f} succeeded; the starting balance {s_before:.2f} covers at most {affordable}.",
        ),
        check(
            "Sender debited exactly once per success",
            s_after == s_before - moved,
            f"{s_before:.2f} - {len(db_success)} x {amount:.2f} = {s_before - moved:.2f}; database shows {s_after:.2f}.",
        ),
        check(
            "Receiver credited exactly once per success",
            r_after == r_before + moved,
            f"{r_before:.2f} + {len(db_success)} x {amount:.2f} = {r_before + moved:.2f}; database shows {r_after:.2f}.",
        ),
        check(
            "Ledger balanced for every successful payment",
            all(int(t["entry_count"]) == 2 and money(t["dr_total"]) == amount and money(t["cr_total"]) == amount for t in db_success),
            "Each SUCCESS payment must have one DR on the sender and one CR on the receiver for the full amount.",
        ),
        check(
            "Rejected payments moved no money",
            all(int(t["entry_count"]) == 0 for t in db_failed),
            f"{len(db_failed)} FAILED payment(s) checked for ledger entries.",
        ),
    ]
    if mode == "same_key":
        checks.append(
            check(
                "Same idempotency key created one payment",
                len(transactions) == 1,
                f"{len(transactions)} payment row(s) exist for the shared key; the other requests were replayed.",
            )
        )
    else:
        checks.append(
            check(
                "API responses match the database",
                api_success == len(db_success) and reached_db == len(transactions),
                f"API reported {api_success} success(es) and {reached_db} recorded outcome(s); database holds "
                f"{len(db_success)} SUCCESS of {len(transactions)} row(s).",
            )
        )
    return checks
