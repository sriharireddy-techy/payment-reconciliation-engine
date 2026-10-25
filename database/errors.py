"""Translate python-oracledb exceptions into application errors.

Only the Oracle error code and the constraint name are kept. SQL text, schema
details and connection strings never reach the API response.
"""

from __future__ import annotations

import re

import oracledb

from services.errors import (
    AppError,
    ConflictError,
    ConstraintViolationError,
    DatabaseOperationError,
    ResourceBusyError,
    ServiceUnavailableError,
)

_CONSTRAINT_RE = re.compile(r"\((?:[A-Za-z0-9_$#\"]+\.)?\"?([A-Za-z0-9_$#]+)\"?\)")

# Errors that mean "the database cannot be reached or refused the login".
_CONNECTIVITY_CODES = {
    "ORA-01017",  # invalid username/password
    "ORA-28000",  # account locked
    "ORA-12514",  # listener does not know the service
    "ORA-12541",  # no listener
    "ORA-12170",  # connect timeout
    "ORA-03113",  # end-of-file on communication channel
    "ORA-03114",  # not connected
    "ORA-03135",  # connection lost contact
}

_FRIENDLY_CONSTRAINTS = {
    "UQ_IDEMPOTENCY_KEY": "This idempotency key has already been used.",
    "CHK_ACCOUNT_BALANCE": "The change would make an account balance negative.",
    "CHK_DIFFERENT_ACCOUNTS": "Sender and receiver accounts must be different.",
    "CHK_TXN_AMOUNT": "The transaction amount must be greater than zero.",
    "UQ_GATEWAY_EXTERNAL_REF": "This external reference already exists for the gateway.",
    "CHK_EXCEPTION_TYPE": "The exception type is not allowed by the schema.",
}


def error_code(exc: BaseException) -> str:
    """Return the ORA-/DPY- code of an oracledb exception, or '' if unknown."""
    err = exc.args[0] if getattr(exc, "args", None) else None
    full_code = getattr(err, "full_code", None)
    if full_code:
        return str(full_code)
    match = re.search(r"\b((?:ORA|DPY|DPI)-\d{4,5})\b", str(exc))
    return match.group(1) if match else ""


def constraint_name(exc: BaseException) -> str | None:
    """Extract the constraint name from ORA-00001 / ORA-0229x messages."""
    err = exc.args[0] if getattr(exc, "args", None) else None
    message = getattr(err, "message", None) or str(exc)
    match = _CONSTRAINT_RE.search(message)
    return match.group(1).upper() if match else None


def translate_db_error(exc: BaseException) -> AppError:
    """Map an oracledb exception to the AppError the API should return."""
    code = error_code(exc)
    if code == "ORA-00001":
        name = constraint_name(exc)
        return ConstraintViolationError(
            _FRIENDLY_CONSTRAINTS.get(name or "", "A unique constraint rejected a duplicate value."),
            code="DUPLICATE_VALUE",
            status_code=409,
            constraint=name,
            details={"oracle_code": code, "constraint": name},
        )
    if code in ("ORA-02290", "ORA-02291", "ORA-02292", "ORA-01400", "ORA-12899", "ORA-01438"):
        name = constraint_name(exc) if code.startswith("ORA-022") else None
        messages = {
            "ORA-02290": "A check constraint rejected the change.",
            "ORA-02291": "A referenced record does not exist.",
            "ORA-02292": "The record is still referenced by other records.",
            "ORA-01400": "A required value was missing.",
            "ORA-12899": "A value is too long for its column.",
            "ORA-01438": "A number is too large for its column.",
        }
        return ConstraintViolationError(
            _FRIENDLY_CONSTRAINTS.get(name or "", messages[code]),
            constraint=name,
            details={"oracle_code": code, "constraint": name},
        )
    if code in ("ORA-30006", "ORA-00054"):
        return ResourceBusyError(
            "The account is locked by another payment in progress. Please retry.",
            details={"oracle_code": code},
        )
    if code.startswith("ORA-2001"):  # RAISE_APPLICATION_ERROR guards in the payment PL/SQL blocks
        return ConflictError(
            "A consistency guard stopped the payment, so the whole transaction was rolled back.",
            code="CONSISTENCY_GUARD",
            details={"oracle_code": code},
        )
    if code == "ORA-00060":
        return ResourceBusyError(
            "Oracle detected a deadlock and rolled this statement back. Please retry.",
            code="DEADLOCK",
            details={"oracle_code": code},
        )
    if code in _CONNECTIVITY_CODES or code.startswith("DPY-6") or code.startswith("DPY-4011") or isinstance(
        exc, (oracledb.OperationalError, oracledb.InterfaceError)
    ):
        hint = "invalid username or password" if code == "ORA-01017" else "connection failed"
        return ServiceUnavailableError(
            f"The Oracle database is unavailable ({hint}).",
            code="DB_UNAVAILABLE",
            details={"oracle_code": code or None},
        )
    return DatabaseOperationError(
        "The database rejected the operation.",
        details={"oracle_code": code or None},
    )
