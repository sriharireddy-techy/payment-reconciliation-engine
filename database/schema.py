"""The project schema as the application expects it, and a live check against Oracle.

EXPECTED_COLUMNS mirrors the team's Review 1 DDL (sql/01_schema_reference.sql),
which was confirmed against USER_TAB_COLUMNS of the FreeSQL schema. The two
columns added later with ALTER TABLE are listed separately: the app never
reads them (WEBHOOK_SECRET is a secret and must not leave the database).
"""

from __future__ import annotations

from typing import Any

from database.connection import connection, fetch_all

EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "USERS": ("USER_ID", "FULL_NAME", "EMAIL", "PHONE_NUMBER", "KYC_STATUS", "CREATED_AT"),
    "PAYMENT_GATEWAYS": (
        "GATEWAY_ID", "GATEWAY_NAME", "API_ENDPOINT", "SETTLEMENT_CURRENCY",
        "FEE_PERCENTAGE", "GATEWAY_STATUS", "CREATED_AT",
    ),
    "BANK_ACCOUNTS": (
        "ACCOUNT_ID", "USER_ID", "ACCOUNT_NUMBER", "BANK_NAME", "IFSC_CODE", "ACCOUNT_TYPE",
        "CURRENT_BALANCE", "CURRENCY", "ACCOUNT_STATUS", "CREATED_AT",
    ),
    "PAYMENT_PROFILES": (
        "PROFILE_ID", "USER_ID", "PREFERRED_GATEWAY_ID", "DAILY_LIMIT", "SINGLE_TX_LIMIT",
        "TWO_FACTOR_AUTH", "PROFILE_STATUS", "UPDATED_AT",
    ),
    "PAYMENT_TRANSACTIONS": (
        "TRANSACTION_ID", "TRANSACTION_REF", "IDEMPOTENCY_KEY", "SENDER_ACCOUNT_ID",
        "RECEIVER_ACCOUNT_ID", "GATEWAY_ID", "AMOUNT", "FEE_AMOUNT", "CURRENCY", "STATUS",
        "INITIATED_AT", "COMPLETED_AT", "DESCRIPTION",
    ),
    "LEDGER_ENTRIES": (
        "ENTRY_ID", "TRANSACTION_ID", "ACCOUNT_ID", "ENTRY_TYPE", "AMOUNT", "ENTRY_TIMESTAMP", "DESCRIPTION",
    ),
    "EXTERNAL_TRANSACTIONS": (
        "EXT_TRANSACTION_ID", "GATEWAY_ID", "EXTERNAL_REFERENCE_ID", "INTERNAL_TRANSACTION_REF",
        "REPORTED_AMOUNT", "REPORTED_FEE", "REPORTED_STATUS", "SETTLEMENT_DATE", "CREATED_AT",
    ),
    "RECONCILIATION_BATCHES": (
        "BATCH_ID", "GATEWAY_ID", "BATCH_DATE", "TOTAL_RECORDS", "MATCHED_RECORDS",
        "EXCEPTION_RECORDS", "BATCH_STATUS", "EXECUTION_TIMESTAMP",
    ),
    "RECONCILIATION_EXCEPTIONS": (
        "EXCEPTION_ID", "BATCH_ID", "INTERNAL_TRANSACTION_ID", "EXT_TRANSACTION_ID", "EXCEPTION_TYPE",
        "DISCREPANCY_AMOUNT", "RESOLUTION_STATUS", "RESOLUTION_NOTES", "CREATED_AT",
    ),
    "AUDIT_LOGS": (
        "LOG_ID", "TABLE_NAME", "ACTION_TYPE", "RECORD_ID", "ACCOUNT_ID", "TRANSACTION_ID",
        "PERFORMED_BY", "ACTION_TIMESTAMP", "DETAILS",
    ),
}

# Added by the ALTER TABLE statements in Review 1; present in FreeSQL, unused by the app.
OPTIONAL_COLUMNS: dict[str, tuple[str, ...]] = {
    "PAYMENT_GATEWAYS": ("WEBHOOK_SECRET",),
    "BANK_ACCOUNTS": ("BRANCH_CODE",),
}

# Constraints the transaction logic relies on (names from the team's DDL).
CRITICAL_CONSTRAINTS: dict[str, str] = {
    "UQ_IDEMPOTENCY_KEY": "Unique idempotency key (duplicate payment protection)",
    "CHK_ACCOUNT_BALANCE": "Balance can never go below zero",
    "CHK_TXN_STATUS": "Allowed transaction statuses",
    "CHK_DIFFERENT_ACCOUNTS": "Sender and receiver must differ",
    "CHK_ENTRY_TYPE": "Ledger entries are DR or CR",
    "UQ_GATEWAY_EXTERNAL_REF": "External reference unique per gateway",
    "CHK_EXCEPTION_TYPE": "Allowed reconciliation exception types",
}

SQL_TABLE_COLUMNS = """
SELECT table_name, column_name
  FROM user_tab_columns
 WHERE table_name IN ('USERS', 'BANK_ACCOUNTS', 'PAYMENT_GATEWAYS', 'PAYMENT_PROFILES',
                      'PAYMENT_TRANSACTIONS', 'LEDGER_ENTRIES', 'EXTERNAL_TRANSACTIONS',
                      'RECONCILIATION_BATCHES', 'RECONCILIATION_EXCEPTIONS', 'AUDIT_LOGS')
"""

SQL_CONSTRAINTS = """
SELECT constraint_name, table_name, constraint_type, search_condition_vc
  FROM user_constraints
 WHERE table_name IN ('PAYMENT_TRANSACTIONS', 'BANK_ACCOUNTS', 'LEDGER_ENTRIES',
                      'EXTERNAL_TRANSACTIONS', 'RECONCILIATION_EXCEPTIONS')
"""

SQL_DUPLICATE_TYPE_ALLOWED = """
SELECT COUNT(*) AS allowed
  FROM user_constraints
 WHERE table_name = 'RECONCILIATION_EXCEPTIONS'
   AND constraint_type = 'C'
   AND search_condition_vc LIKE '%DUPLICATE_EXTERNAL%'
"""


def compare_columns(found: dict[str, set[str]]) -> dict[str, Any]:
    """Pure comparison of live columns with EXPECTED_COLUMNS."""
    tables = []
    for table, expected in EXPECTED_COLUMNS.items():
        live = found.get(table, set())
        missing = [c for c in expected if c not in live]
        extra = sorted(live - set(expected) - set(OPTIONAL_COLUMNS.get(table, ())))
        tables.append(
            {
                "table": table,
                "exists": table in found,
                "missing_columns": missing,
                "unexpected_columns": extra,
                "ok": table in found and not missing,
            }
        )
    return {"ok": all(t["ok"] for t in tables), "tables": tables}


def verify_schema() -> dict[str, Any]:
    """Read the data dictionary (read-only) and compare it with what the app expects."""
    with connection() as conn:
        cur = conn.cursor()
        found: dict[str, set[str]] = {}
        for row in fetch_all(cur, SQL_TABLE_COLUMNS):
            found.setdefault(row["table_name"], set()).add(row["column_name"])
        result = compare_columns(found)
        names = {row["constraint_name"]: row for row in fetch_all(cur, SQL_CONSTRAINTS)}
        result["constraints"] = [
            {"name": name, "purpose": purpose, "present": name in names}
            for name, purpose in CRITICAL_CONSTRAINTS.items()
        ]
        result["duplicate_exception_type_enabled"] = duplicate_type_allowed(cur)
        return result


def duplicate_type_allowed(cur) -> bool:
    """True once the optional migration 001 has extended CHK_EXCEPTION_TYPE."""
    row = fetch_all(cur, SQL_DUPLICATE_TYPE_ALLOWED)
    return bool(row and row[0]["allowed"])
