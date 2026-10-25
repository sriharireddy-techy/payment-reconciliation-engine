"""Read-only views of PAYMENT_TRANSACTIONS: searchable history and full details."""

from __future__ import annotations

from datetime import date
from typing import Any

from database.connection import connection, fetch_all
from services import ledger_service
from services.errors import NotFoundError
from services.payment_rules import rejection_code
from services.serialization import serialize_row, serialize_rows

SQL_LIST_TRANSACTIONS = """
SELECT t.transaction_id, t.transaction_ref, t.sender_account_id, t.receiver_account_id,
       su.full_name AS sender_name, ru.full_name AS receiver_name,
       t.gateway_id, g.gateway_name, t.amount, t.fee_amount, t.currency, t.status,
       t.initiated_at, t.completed_at, t.description,
       COUNT(*) OVER () AS total_count
  FROM PAYMENT_TRANSACTIONS t
  JOIN BANK_ACCOUNTS sa ON sa.account_id = t.sender_account_id
  JOIN USERS su ON su.user_id = sa.user_id
  JOIN BANK_ACCOUNTS ra ON ra.account_id = t.receiver_account_id
  JOIN USERS ru ON ru.user_id = ra.user_id
  LEFT JOIN PAYMENT_GATEWAYS g ON g.gateway_id = t.gateway_id
 WHERE (:status IS NULL OR t.status = :status)
   AND (:gateway_id IS NULL OR t.gateway_id = :gateway_id)
   AND (:account_id IS NULL OR t.sender_account_id = :account_id OR t.receiver_account_id = :account_id)
   AND (:search IS NULL
        OR UPPER(t.transaction_ref) LIKE UPPER(:search) || '%'
        OR UPPER(t.idempotency_key) LIKE UPPER(:search) || '%'
        OR TO_CHAR(t.transaction_id) = :search
        OR UPPER(su.full_name) LIKE '%' || UPPER(:search) || '%'
        OR UPPER(ru.full_name) LIKE '%' || UPPER(:search) || '%')
   AND (:date_from IS NULL OR t.initiated_at >= CAST(:date_from AS DATE))
   AND (:date_to IS NULL OR t.initiated_at < CAST(:date_to AS DATE) + 1)
 ORDER BY t.transaction_id DESC
 OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
"""

SQL_TRANSACTION_DETAIL = """
SELECT t.transaction_id, t.transaction_ref, t.idempotency_key, t.sender_account_id,
       t.receiver_account_id, t.gateway_id, g.gateway_name, g.fee_percentage,
       t.amount, t.fee_amount, t.currency, t.status, t.initiated_at, t.completed_at, t.description,
       su.user_id AS sender_user_id, su.full_name AS sender_name, sa.bank_name AS sender_bank,
       'XXXXXX' || SUBSTR(sa.account_number, -4) AS sender_masked_account,
       ru.user_id AS receiver_user_id, ru.full_name AS receiver_name, ra.bank_name AS receiver_bank,
       'XXXXXX' || SUBSTR(ra.account_number, -4) AS receiver_masked_account
  FROM PAYMENT_TRANSACTIONS t
  JOIN BANK_ACCOUNTS sa ON sa.account_id = t.sender_account_id
  JOIN USERS su ON su.user_id = sa.user_id
  JOIN BANK_ACCOUNTS ra ON ra.account_id = t.receiver_account_id
  JOIN USERS ru ON ru.user_id = ra.user_id
  LEFT JOIN PAYMENT_GATEWAYS g ON g.gateway_id = t.gateway_id
 WHERE t.transaction_id = :transaction_id
"""

SQL_TRANSACTION_AUDIT = """
SELECT log_id, table_name, action_type, record_id, account_id, performed_by, action_timestamp, details
  FROM AUDIT_LOGS
 WHERE transaction_id = :transaction_id
    OR (table_name = 'PAYMENT_TRANSACTIONS' AND record_id = :transaction_id)
 ORDER BY log_id
"""

SQL_TRANSACTION_EXTERNAL = """
SELECT e.ext_transaction_id, e.gateway_id, e.external_reference_id, e.reported_amount,
       e.reported_fee, e.reported_status, e.settlement_date, e.created_at
  FROM EXTERNAL_TRANSACTIONS e
 WHERE e.internal_transaction_ref = :transaction_ref
 ORDER BY e.ext_transaction_id
"""

SQL_TRANSACTION_EXCEPTIONS = """
SELECT x.exception_id, x.batch_id, x.ext_transaction_id, x.exception_type, x.discrepancy_amount,
       x.resolution_status, x.resolution_notes, x.created_at
  FROM RECONCILIATION_EXCEPTIONS x
 WHERE x.internal_transaction_id = :transaction_id
 ORDER BY x.exception_id
"""


def list_transactions(
    *,
    status: str | None = None,
    gateway_id: int | None = None,
    account_id: int | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    params = {
        "status": status,
        "gateway_id": gateway_id,
        "account_id": account_id,
        "search": search.strip() if search and search.strip() else None,
        "date_from": date_from,
        "date_to": date_to,
        "limit": limit,
        "offset": offset,
    }
    with connection() as conn:
        rows = fetch_all(conn.cursor(), SQL_LIST_TRANSACTIONS, params)
    total = int(rows[0]["total_count"]) if rows else 0
    items = serialize_rows(rows)
    for item, row in zip(items, rows):
        item.pop("total_count", None)
        item["rejection_code"] = rejection_code(row.get("description"))
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def get_transaction(transaction_id: int) -> dict[str, Any]:
    """Everything known about one payment: parties, ledger, audit trail, reconciliation."""
    with connection() as conn:
        cur = conn.cursor()
        rows = fetch_all(cur, SQL_TRANSACTION_DETAIL, {"transaction_id": transaction_id})
        if not rows:
            raise NotFoundError(f"Transaction {transaction_id} does not exist.", code="TRANSACTION_NOT_FOUND")
        row = rows[0]
        ledger_rows = fetch_all(cur, ledger_service.SQL_ENTRIES_FOR_TRANSACTION, {"transaction_id": transaction_id})
        audit_rows = fetch_all(cur, SQL_TRANSACTION_AUDIT, {"transaction_id": transaction_id})
        external_rows = fetch_all(cur, SQL_TRANSACTION_EXTERNAL, {"transaction_ref": row["transaction_ref"]})
        exception_rows = fetch_all(cur, SQL_TRANSACTION_EXCEPTIONS, {"transaction_id": transaction_id})
    transaction = serialize_row(row)
    transaction["rejection_code"] = rejection_code(row.get("description"))
    external = serialize_rows(external_rows)
    for item in external:
        item["is_simulated"] = str(item["external_reference_id"]).startswith("SIM-")
    return {
        "transaction": transaction,
        "ledger": ledger_service.summarize_entries(ledger_rows),
        "audit": serialize_rows(audit_rows),
        "external_records": external,
        "reconciliation_exceptions": serialize_rows(exception_rows),
    }
