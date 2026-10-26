"""Convert database rows into JSON-safe values without losing monetary precision.

FastAPI would turn Decimal into float; money is therefore sent as a string with
exactly two decimals ("1500.00"), and identifiers/counters as integers.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

CENT = Decimal("0.01")

MONEY_FIELDS = frozenset(
    {
        "amount", "fee_amount", "current_balance", "daily_limit", "single_tx_limit",
        "fee_percentage", "reported_amount", "reported_fee", "discrepancy_amount",
        "total_amount", "total_value", "total_balance", "success_value", "spent_today",
        "dr_total", "cr_total", "balance_before", "balance_after", "internal_amount",
    }
)
DATE_ONLY_FIELDS = frozenset({"batch_date", "settlement_date", "day"})


def money(value: Any) -> Decimal:
    """Parse to Decimal and round half-up to 2 places (the schema's NUMBER(15,2))."""
    if isinstance(value, float):
        value = repr(value)
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def money_str(value: Any) -> str | None:
    return None if value is None else f"{money(value):.2f}"


def _number(value: Decimal) -> int | str:
    if value == value.to_integral_value():
        return int(value)
    return str(value)


def serialize_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key in MONEY_FIELDS:
        return money_str(value)
    if isinstance(value, Decimal):
        return _number(value)
    if isinstance(value, datetime):
        if key in DATE_ONLY_FIELDS:
            return value.date().isoformat()
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return value


def serialize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: serialize_value(key, value) for key, value in row.items()}


def serialize_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [serialize_row(row) for row in rows]  # type: ignore[misc]


def fit_bytes(text: str | None, max_bytes: int) -> str | None:
    """Trim text so it fits a VARCHAR2(n BYTE) column (multi-byte safe)."""
    if text is None:
        return None
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
