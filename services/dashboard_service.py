"""Dashboard figures, all computed live from the database (nothing is invented).

Amounts are always grouped by currency, because adding INR to USD is meaningless.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from database.connection import connection, fetch_all, fetch_one
from services import audit_service
from services.serialization import money, serialize_row, serialize_rows

SQL_STATUS_SUMMARY = """
SELECT status, currency, COUNT(*) AS txn_count, NVL(SUM(amount), 0) AS total_amount
  FROM PAYMENT_TRANSACTIONS
 GROUP BY status, currency
"""

SQL_DAILY_ACTIVITY = """
SELECT TRUNC(initiated_at) AS day,
       COUNT(*) AS txn_count,
       SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) AS success_count,
       SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed_count
  FROM PAYMENT_TRANSACTIONS
 WHERE initiated_at >= TRUNC(SYSDATE) - 13
 GROUP BY TRUNC(initiated_at)
 ORDER BY day
"""

SQL_BALANCES = """
SELECT currency, COUNT(*) AS account_count, NVL(SUM(current_balance), 0) AS total_balance
  FROM BANK_ACCOUNTS
 WHERE account_status = 'ACTIVE'
 GROUP BY currency
 ORDER BY currency
"""

SQL_COUNTS = """
SELECT (SELECT COUNT(*) FROM USERS) AS user_count,
       (SELECT COUNT(*) FROM BANK_ACCOUNTS) AS account_count,
       (SELECT COUNT(*) FROM PAYMENT_GATEWAYS) AS gateway_count,
       (SELECT COUNT(*) FROM LEDGER_ENTRIES) AS ledger_entry_count,
       (SELECT COUNT(*) FROM AUDIT_LOGS) AS audit_event_count,
       (SELECT COUNT(*) FROM EXTERNAL_TRANSACTIONS) AS external_record_count,
       (SELECT COUNT(*) FROM EXTERNAL_TRANSACTIONS
         WHERE external_reference_id LIKE 'SIM-%') AS simulated_external_count,
       (SELECT COUNT(*) FROM EXTERNAL_TRANSACTIONS e
         WHERE NOT EXISTS (SELECT 1 FROM PAYMENT_TRANSACTIONS t
                            WHERE t.transaction_ref = e.internal_transaction_ref)) AS unmatched_external_count,
       (SELECT COUNT(*) FROM RECONCILIATION_BATCHES) AS batch_count
  FROM DUAL
"""

SQL_EXCEPTION_SUMMARY = """
SELECT exception_type, resolution_status, COUNT(*) AS exception_count
  FROM RECONCILIATION_EXCEPTIONS
 GROUP BY exception_type, resolution_status
"""

SQL_RECENT_TRANSACTIONS = """
SELECT t.transaction_id, t.transaction_ref, t.sender_account_id, t.receiver_account_id,
       su.full_name AS sender_name, ru.full_name AS receiver_name,
       t.amount, t.currency, t.status, t.initiated_at
  FROM PAYMENT_TRANSACTIONS t
  JOIN BANK_ACCOUNTS sa ON sa.account_id = t.sender_account_id
  JOIN USERS su ON su.user_id = sa.user_id
  JOIN BANK_ACCOUNTS ra ON ra.account_id = t.receiver_account_id
  JOIN USERS ru ON ru.user_id = ra.user_id
 ORDER BY t.transaction_id DESC
 FETCH FIRST 8 ROWS ONLY
"""

SQL_RECENT_BATCHES = """
SELECT b.batch_id, g.gateway_name, b.batch_date, b.total_records, b.matched_records,
       b.exception_records, b.batch_status, b.execution_timestamp
  FROM RECONCILIATION_BATCHES b
  JOIN PAYMENT_GATEWAYS g ON g.gateway_id = b.gateway_id
 ORDER BY b.batch_id DESC
 FETCH FIRST 5 ROWS ONLY
"""

SQL_RECENT_AUDIT = """
SELECT log_id, table_name, action_type, record_id, transaction_id, performed_by, action_timestamp, details
  FROM AUDIT_LOGS
 ORDER BY log_id DESC
 FETCH FIRST 8 ROWS ONLY
"""

PENDING_STATUSES = ("INITIATED", "PROCESSING")


def summarize_status_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn (status, currency, count, total) rows into dashboard KPIs (pure)."""
    counts: dict[str, int] = {}
    success_value: dict[str, Decimal] = {}
    for row in rows:
        status = row["status"]
        counts[status] = counts.get(status, 0) + int(row["txn_count"])
        if status == "SUCCESS":
            success_value[row["currency"]] = success_value.get(row["currency"], Decimal("0")) + money(row["total_amount"])
    return {
        "total": sum(counts.values()),
        "success": counts.get("SUCCESS", 0),
        "failed": counts.get("FAILED", 0),
        "pending": sum(counts.get(s, 0) for s in PENDING_STATUSES),
        "reversed": counts.get("REVERSED", 0),
        "by_status": counts,
        "success_value_by_currency": [
            {"currency": cur, "total_value": f"{value:.2f}"} for cur, value in sorted(success_value.items())
        ],
    }


def get_dashboard() -> dict[str, Any]:
    with connection() as conn:
        cur = conn.cursor()
        status_rows = fetch_all(cur, SQL_STATUS_SUMMARY)
        daily = fetch_all(cur, SQL_DAILY_ACTIVITY)
        balances = fetch_all(cur, SQL_BALANCES)
        counts = fetch_one(cur, SQL_COUNTS) or {}
        exceptions = fetch_all(cur, SQL_EXCEPTION_SUMMARY)
        recent = fetch_all(cur, SQL_RECENT_TRANSACTIONS)
        batches = fetch_all(cur, SQL_RECENT_BATCHES)
        audit = fetch_all(cur, SQL_RECENT_AUDIT)

    exception_types: dict[str, int] = {}
    unresolved = 0
    for row in exceptions:
        n = int(row["exception_count"])
        exception_types[row["exception_type"]] = exception_types.get(row["exception_type"], 0) + n
        if row["resolution_status"] == "UNRESOLVED":
            unresolved += n
    recent_audit = serialize_rows(audit)
    for item in recent_audit:
        item["event"] = audit_service.event_name(item.get("details"))
    return {
        "transactions": summarize_status_rows(status_rows),
        "daily_activity": serialize_rows(daily),
        "balances": serialize_rows(balances),
        "counts": serialize_row(counts),
        "reconciliation": {
            "exceptions_by_type": exception_types,
            "unresolved_exceptions": unresolved,
            "recent_batches": serialize_rows(batches),
        },
        "recent_transactions": serialize_rows(recent),
        "recent_audit": recent_audit,
    }
