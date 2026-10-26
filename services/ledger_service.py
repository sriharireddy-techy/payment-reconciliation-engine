"""Double-entry ledger: reading LEDGER_ENTRIES and checking that it balances.

Every successful payment owns exactly two entries of the same amount: a DR on
the sender's account and a CR on the receiver's (Review 1 rule BR-01). The
integrity report applies these expectations to every transaction in the table,
including rows that were inserted by hand before this application existed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from database.connection import connection, fetch_all, fetch_one
from services.serialization import money, serialize_rows

# How many ledger rows each transaction status should own in this design.
EXPECTED_ENTRY_COUNT = {"SUCCESS": 2, "FAILED": 0, "INITIATED": 0, "PROCESSING": 0}

SQL_ENTRIES_FOR_TRANSACTION = """
SELECT l.entry_id, l.transaction_id, l.account_id, l.entry_type, l.amount, l.entry_timestamp,
       l.description, u.full_name AS account_holder,
       'XXXXXX' || SUBSTR(a.account_number, -4) AS masked_account_number
  FROM LEDGER_ENTRIES l
  JOIN BANK_ACCOUNTS a ON a.account_id = l.account_id
  JOIN USERS u ON u.user_id = a.user_id
 WHERE l.transaction_id = :transaction_id
 ORDER BY l.entry_id
"""

SQL_LIST_ENTRIES = """
SELECT l.entry_id, l.transaction_id, l.account_id, l.entry_type, l.amount, l.entry_timestamp,
       l.description, t.transaction_ref, t.status AS transaction_status, t.currency,
       u.full_name AS account_holder,
       'XXXXXX' || SUBSTR(a.account_number, -4) AS masked_account_number,
       COUNT(*) OVER () AS total_count
  FROM LEDGER_ENTRIES l
  JOIN PAYMENT_TRANSACTIONS t ON t.transaction_id = l.transaction_id
  JOIN BANK_ACCOUNTS a ON a.account_id = l.account_id
  JOIN USERS u ON u.user_id = a.user_id
 WHERE (:account_id IS NULL OR l.account_id = :account_id)
   AND (:transaction_id IS NULL OR l.transaction_id = :transaction_id)
   AND (:entry_type IS NULL OR l.entry_type = :entry_type)
 ORDER BY l.entry_id DESC
 OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
"""

SQL_LEDGER_TOTALS = """
SELECT NVL(SUM(CASE WHEN entry_type = 'DR' THEN amount END), 0) AS dr_total,
       NVL(SUM(CASE WHEN entry_type = 'CR' THEN amount END), 0) AS cr_total,
       COUNT(*) AS entry_count
  FROM LEDGER_ENTRIES
"""

SQL_PER_TRANSACTION_TOTALS = """
SELECT t.transaction_id, t.transaction_ref, t.status, t.amount, t.sender_account_id,
       t.receiver_account_id,
       NVL(SUM(CASE WHEN l.entry_type = 'DR' THEN l.amount END), 0) AS dr_total,
       NVL(SUM(CASE WHEN l.entry_type = 'CR' THEN l.amount END), 0) AS cr_total,
       COUNT(l.entry_id) AS entry_count,
       NVL(SUM(CASE WHEN l.entry_type = 'DR' AND l.account_id = t.sender_account_id THEN 1 ELSE 0 END), 0) AS sender_debits,
       NVL(SUM(CASE WHEN l.entry_type = 'CR' AND l.account_id = t.receiver_account_id THEN 1 ELSE 0 END), 0) AS receiver_credits
  FROM PAYMENT_TRANSACTIONS t
  LEFT JOIN LEDGER_ENTRIES l ON l.transaction_id = t.transaction_id
 GROUP BY t.transaction_id, t.transaction_ref, t.status, t.amount, t.sender_account_id, t.receiver_account_id
"""

SQL_ACCOUNT_LEDGER_SUMMARY = """
SELECT NVL(SUM(CASE WHEN entry_type = 'DR' THEN amount END), 0) AS dr_total,
       NVL(SUM(CASE WHEN entry_type = 'CR' THEN amount END), 0) AS cr_total,
       COUNT(*) AS entry_count
  FROM LEDGER_ENTRIES
 WHERE account_id = :account_id
"""


def transaction_problems(row: dict[str, Any]) -> list[str]:
    """Pure check of one transaction's ledger totals against the design rules."""
    problems: list[str] = []
    status = row["status"]
    dr, cr = money(row["dr_total"]), money(row["cr_total"])
    count = int(row["entry_count"])
    if dr != cr:
        problems.append(f"Debits {dr} do not equal credits {cr}.")
    expected = EXPECTED_ENTRY_COUNT.get(status)
    if status == "REVERSED":
        if count and count % 2:
            problems.append("A reversed payment should have an even number of entries.")
    elif expected is not None and count != expected:
        problems.append(f"{status} payment has {count} ledger entries; expected {expected}.")
    if status == "SUCCESS" and count == 2:
        if dr != money(row["amount"]):
            problems.append(f"Ledger amount {dr} differs from the payment amount {money(row['amount'])}.")
        if int(row.get("sender_debits", 1)) != 1 or int(row.get("receiver_credits", 1)) != 1:
            problems.append("The DR entry is not on the sender or the CR entry is not on the receiver.")
    return problems


def entries_for_transaction(transaction_id: int) -> dict[str, Any]:
    with connection() as conn:
        rows = fetch_all(conn.cursor(), SQL_ENTRIES_FOR_TRANSACTION, {"transaction_id": transaction_id})
    return summarize_entries(rows)


def summarize_entries(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dr = sum((money(r["amount"]) for r in rows if r["entry_type"] == "DR"), Decimal("0.00"))
    cr = sum((money(r["amount"]) for r in rows if r["entry_type"] == "CR"), Decimal("0.00"))
    return {
        "entries": serialize_rows(rows),
        "dr_total": f"{dr:.2f}",
        "cr_total": f"{cr:.2f}",
        "balanced": dr == cr,
    }


def list_entries(
    *, account_id: int | None = None, transaction_id: int | None = None, entry_type: str | None = None,
    limit: int = 50, offset: int = 0,
) -> dict[str, Any]:
    params = {
        "account_id": account_id,
        "transaction_id": transaction_id,
        "entry_type": entry_type,
        "limit": limit,
        "offset": offset,
    }
    with connection() as conn:
        rows = fetch_all(conn.cursor(), SQL_LIST_ENTRIES, params)
    total = int(rows[0]["total_count"]) if rows else 0
    items = serialize_rows(rows)
    for item in items:
        item.pop("total_count", None)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def integrity_report(max_items: int = 100) -> dict[str, Any]:
    """Check the whole ledger: global DR = CR, and each transaction's entries."""
    with connection() as conn:
        cur = conn.cursor()
        totals = fetch_one(cur, SQL_LEDGER_TOTALS) or {"dr_total": 0, "cr_total": 0, "entry_count": 0}
        per_txn = fetch_all(cur, SQL_PER_TRANSACTION_TOTALS)
    violations = []
    for row in per_txn:
        problems = transaction_problems(row)
        if problems:
            violations.append(
                {
                    "transaction_id": int(row["transaction_id"]),
                    "transaction_ref": row["transaction_ref"],
                    "status": row["status"],
                    "amount": f"{money(row['amount']):.2f}",
                    "dr_total": f"{money(row['dr_total']):.2f}",
                    "cr_total": f"{money(row['cr_total']):.2f}",
                    "entry_count": int(row["entry_count"]),
                    "problems": problems,
                }
            )
    dr, cr = money(totals["dr_total"]), money(totals["cr_total"])
    return {
        "global": {
            "dr_total": f"{dr:.2f}",
            "cr_total": f"{cr:.2f}",
            "entry_count": int(totals["entry_count"]),
            "balanced": dr == cr,
        },
        "transactions_checked": len(per_txn),
        "violation_count": len(violations),
        "violations": violations[:max_items],
        "ok": dr == cr and not violations,
    }


def account_ledger_summary(cur, account_id: int) -> dict[str, Any]:
    row = fetch_one(cur, SQL_ACCOUNT_LEDGER_SUMMARY, {"account_id": account_id}) or {}
    return {
        "dr_total": f"{money(row.get('dr_total') or 0):.2f}",
        "cr_total": f"{money(row.get('cr_total') or 0):.2f}",
        "entry_count": int(row.get("entry_count") or 0),
    }
