"""Audit trail on the AUDIT_LOGS table.

AUDIT_LOGS only allows ACTION_TYPE in (INSERT, UPDATE, DELETE, SECURITY) and
needs a RECORD_ID, so every event is mapped onto those columns; the event name
and its context go into DETAILS as compact JSON. PERFORMED_BY is left to its
column default (the database user), as Review 1 business rule BR-10 describes.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import oracledb

from database.connection import connection, fetch_all
from services.serialization import fit_bytes, serialize_rows

ACTION_TYPES = ("INSERT", "UPDATE", "DELETE", "SECURITY")
DETAILS_MAX_BYTES = 4000
_SENSITIVE_KEYS = ("password", "secret", "token", "dsn", "credential", "webhook")

SQL_INSERT_AUDIT = """
INSERT INTO AUDIT_LOGS (table_name, action_type, record_id, account_id, transaction_id, details)
VALUES (:table_name, :action_type, :record_id, :account_id, :transaction_id, :details)
"""

SQL_LIST_AUDIT = """
SELECT l.log_id, l.table_name, l.action_type, l.record_id, l.account_id, l.transaction_id,
       l.performed_by, l.action_timestamp, l.details,
       COUNT(*) OVER () AS total_count
  FROM AUDIT_LOGS l
 WHERE (:table_name IS NULL OR l.table_name = :table_name)
   AND (:action_type IS NULL OR l.action_type = :action_type)
   AND (:account_id IS NULL OR l.account_id = :account_id)
   AND (:transaction_id IS NULL OR l.transaction_id = :transaction_id OR
        (l.table_name = 'PAYMENT_TRANSACTIONS' AND l.record_id = :transaction_id))
   AND (:search IS NULL OR UPPER(l.details) LIKE '%' || UPPER(:search) || '%')
 ORDER BY l.log_id DESC
 OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
"""


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _clean(v)
            for k, v in value.items()
            if not any(word in str(k).lower() for word in _SENSITIVE_KEYS)
        }
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def details_json(event: str, **context: Any) -> str:
    """Compact JSON for the DETAILS column; secret-looking keys are dropped."""
    payload = {"event": event, **_clean(context)}
    text = json.dumps(payload, separators=(",", ":"), default=str)
    if len(text.encode("utf-8")) <= DETAILS_MAX_BYTES:
        return text
    short = {"event": event, "truncated": True, "summary": fit_bytes(text, DETAILS_MAX_BYTES - 120)}
    return json.dumps(short, separators=(",", ":"))


def audit_row(
    table_name: str,
    action_type: str,
    record_id: int,
    details: str,
    *,
    account_id: int | None = None,
    transaction_id: int | None = None,
) -> dict[str, Any]:
    if action_type not in ACTION_TYPES:
        raise ValueError(f"Unsupported audit action type {action_type!r}")
    return {
        "table_name": table_name,
        "action_type": action_type,
        "record_id": record_id,
        "account_id": account_id,
        "transaction_id": transaction_id,
        "details": details,
    }


def write(cursor, rows: list[dict[str, Any]]) -> None:
    """Insert audit rows inside the caller's transaction (same connection as `cursor`)."""
    if not rows:
        return
    batch = cursor.connection.cursor()
    # Declare the types: a NULL in the first row must not decide a column's bind type.
    batch.setinputsizes(
        record_id=oracledb.DB_TYPE_NUMBER,
        account_id=oracledb.DB_TYPE_NUMBER,
        transaction_id=oracledb.DB_TYPE_NUMBER,
        details=DETAILS_MAX_BYTES,
    )
    batch.executemany(SQL_INSERT_AUDIT, rows)


def write_now(rows: list[dict[str, Any]]) -> None:
    """Insert audit rows in their own short transaction (used after a rollback)."""
    with connection() as conn:
        write(conn.cursor(), rows)
        conn.commit()


def list_logs(
    *,
    table_name: str | None = None,
    action_type: str | None = None,
    account_id: int | None = None,
    transaction_id: int | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    params = {
        "table_name": table_name.upper() if table_name else None,
        "action_type": action_type.upper() if action_type else None,
        "account_id": account_id,
        "transaction_id": transaction_id,
        "search": search or None,
        "limit": limit,
        "offset": offset,
    }
    with connection() as conn:
        rows = fetch_all(conn.cursor(), SQL_LIST_AUDIT, params)
    total = int(rows[0]["total_count"]) if rows else 0
    items = serialize_rows(rows)
    for item in items:
        item.pop("total_count", None)
        item["event"] = event_name(item.get("details"))
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def event_name(details: str | None) -> str | None:
    if not details or not details.startswith("{"):
        return None
    try:
        return json.loads(details).get("event")
    except (ValueError, AttributeError):
        return None
