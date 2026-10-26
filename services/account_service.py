"""Accounts, users and gateways (read-only).

Account numbers are masked in SQL with SUBSTR, as proposed in Review 1
(NFR-05), so the full number never leaves the database. The gateway query
never selects WEBHOOK_SECRET.
"""

from __future__ import annotations

from typing import Any

from database.connection import connection, fetch_all, fetch_one
from services import ledger_service
from services.errors import NotFoundError
from services.serialization import serialize_row, serialize_rows

SQL_LIST_ACCOUNTS = """
SELECT a.account_id, a.user_id, u.full_name, u.kyc_status,
       'XXXXXX' || SUBSTR(a.account_number, -4) AS masked_account_number,
       a.bank_name, a.ifsc_code, a.account_type, a.current_balance, a.currency,
       a.account_status, a.created_at,
       p.daily_limit, p.single_tx_limit, p.profile_status, p.preferred_gateway_id,
       g.gateway_name AS preferred_gateway_name
  FROM BANK_ACCOUNTS a
  JOIN USERS u ON u.user_id = a.user_id
  LEFT JOIN PAYMENT_PROFILES p ON p.user_id = a.user_id
  LEFT JOIN PAYMENT_GATEWAYS g ON g.gateway_id = p.preferred_gateway_id
 ORDER BY a.account_id
"""

SQL_ACCOUNT = """
SELECT a.account_id, a.user_id, u.full_name, u.email, u.kyc_status,
       'XXXXXX' || SUBSTR(a.account_number, -4) AS masked_account_number,
       a.bank_name, a.ifsc_code, a.account_type, a.current_balance, a.currency,
       a.account_status, a.created_at,
       p.daily_limit, p.single_tx_limit, p.two_factor_auth, p.profile_status, p.preferred_gateway_id,
       g.gateway_name AS preferred_gateway_name
  FROM BANK_ACCOUNTS a
  JOIN USERS u ON u.user_id = a.user_id
  LEFT JOIN PAYMENT_PROFILES p ON p.user_id = a.user_id
  LEFT JOIN PAYMENT_GATEWAYS g ON g.gateway_id = p.preferred_gateway_id
 WHERE a.account_id = :account_id
"""

SQL_SPENT_TODAY = """
SELECT NVL(SUM(t.amount), 0) AS spent_today
  FROM PAYMENT_TRANSACTIONS t
  JOIN BANK_ACCOUNTS a ON a.account_id = t.sender_account_id
 WHERE a.user_id = :user_id
   AND t.status = 'SUCCESS'
   AND t.initiated_at >= TRUNC(SYSDATE)
"""

SQL_LIST_GATEWAYS = """
SELECT gateway_id, gateway_name, settlement_currency, fee_percentage, gateway_status, created_at
  FROM PAYMENT_GATEWAYS
 ORDER BY gateway_id
"""


def list_accounts() -> list[dict[str, Any]]:
    with connection() as conn:
        return serialize_rows(fetch_all(conn.cursor(), SQL_LIST_ACCOUNTS))


def get_account(account_id: int) -> dict[str, Any]:
    with connection() as conn:
        cur = conn.cursor()
        row = fetch_one(cur, SQL_ACCOUNT, {"account_id": account_id})
        if row is None:
            raise NotFoundError(f"Account {account_id} does not exist.", code="ACCOUNT_NOT_FOUND")
        spent = fetch_one(cur, SQL_SPENT_TODAY, {"user_id": int(row["user_id"])}) or {"spent_today": 0}
        summary = ledger_service.account_ledger_summary(cur, account_id)
    account = serialize_row(row)
    account["spent_today"] = serialize_row(spent)["spent_today"]
    account["ledger_summary"] = summary
    return account


def list_gateways() -> list[dict[str, Any]]:
    with connection() as conn:
        return serialize_rows(fetch_all(conn.cursor(), SQL_LIST_GATEWAYS))
