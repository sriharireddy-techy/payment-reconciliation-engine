"""Pure business rules for a payment (no database access, fully unit-tested).

The payment service reads fresh, locked values from Oracle and asks these
functions whether the payment may go ahead. Oracle constraints (CHECK balance
>= 0, unique idempotency key, ...) still act as the final safety net.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from services.serialization import money

# Allowed status transitions for PAYMENT_TRANSACTIONS.STATUS (BR-07).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "INITIATED": frozenset({"PROCESSING", "FAILED"}),
    "PROCESSING": frozenset({"SUCCESS", "FAILED"}),
    "SUCCESS": frozenset({"REVERSED"}),
    "FAILED": frozenset(),
    "REVERSED": frozenset(),
}


def can_transition(current: str, new: str) -> bool:
    return new in ALLOWED_TRANSITIONS.get(current, frozenset())


@dataclass(frozen=True)
class AccountState:
    account_id: int
    balance: Decimal
    currency: str
    status: str


@dataclass(frozen=True)
class ProfileState:
    daily_limit: Decimal
    single_tx_limit: Decimal
    status: str


@dataclass(frozen=True)
class GatewayState:
    gateway_id: int
    status: str
    fee_percentage: Decimal


@dataclass(frozen=True)
class Rejection:
    code: str
    message: str


def compute_fee(amount: Decimal, gateway: GatewayState | None) -> Decimal:
    """Gateway fee = amount x fee_percentage / 100, rounded half-up to paise."""
    if gateway is None:
        return money(0)
    return money(Decimal(amount) * Decimal(gateway.fee_percentage) / Decimal(100))


def evaluate_payment(
    amount: Decimal,
    sender: AccountState,
    receiver: AccountState,
    profile: ProfileState | None,
    spent_today: Decimal,
    gateway: GatewayState | None,
) -> Rejection | None:
    """Return why the payment must be rejected, or None if it may proceed.

    Checks run in a fixed order so the same input always gives the same reason.
    """
    amount = money(amount)
    if amount <= 0:
        return Rejection("INVALID_AMOUNT", "The amount must be greater than zero.")
    if sender.account_id == receiver.account_id:
        return Rejection("SAME_ACCOUNT", "Sender and receiver accounts must be different.")
    if sender.status != "ACTIVE":
        return Rejection("SENDER_ACCOUNT_NOT_ACTIVE", f"Sender account is {sender.status}.")
    if receiver.status != "ACTIVE":
        return Rejection("RECEIVER_ACCOUNT_NOT_ACTIVE", f"Receiver account is {receiver.status}.")
    if sender.currency != receiver.currency:
        return Rejection(
            "CURRENCY_MISMATCH",
            f"Sender holds {sender.currency} but receiver holds {receiver.currency}; currency conversion is not supported.",
        )
    if gateway is not None and gateway.status != "ACTIVE":
        return Rejection("GATEWAY_NOT_ACTIVE", f"The selected gateway is {gateway.status}.")
    if profile is not None:
        if profile.status != "ACTIVE":
            return Rejection("PROFILE_SUSPENDED", "The sender's payment profile is suspended.")
        if amount > money(profile.single_tx_limit):
            return Rejection(
                "SINGLE_TX_LIMIT_EXCEEDED",
                f"Amount {amount} exceeds the per-transaction limit of {money(profile.single_tx_limit)}.",
            )
        if money(spent_today) + amount > money(profile.daily_limit):
            return Rejection(
                "DAILY_LIMIT_EXCEEDED",
                f"Today's successful payments ({money(spent_today)}) plus {amount} exceed the daily limit of "
                f"{money(profile.daily_limit)}.",
            )
    if money(sender.balance) < amount:
        return Rejection(
            "INSUFFICIENT_FUNDS",
            f"Available balance {money(sender.balance)} is less than the amount {amount}.",
        )
    return None


def same_request(existing: dict, sender_id: int, receiver_id: int, amount: Decimal, gateway_id: int | None) -> bool:
    """Does a stored transaction describe the same payment as a retried request?"""
    stored_gateway = existing.get("gateway_id")
    return (
        int(existing["sender_account_id"]) == sender_id
        and int(existing["receiver_account_id"]) == receiver_id
        and money(existing["amount"]) == money(amount)
        and (gateway_id is None or (stored_gateway is not None and int(stored_gateway) == gateway_id))
    )


REJECTION_PREFIX = "[REJECTED:"


def rejected_description(code: str, memo: str | None) -> str:
    """DESCRIPTION text for a FAILED payment: the reason code, then the payer's memo."""
    return f"{REJECTION_PREFIX}{code}] {memo or ''}".strip()


def rejection_code(description: str | None) -> str | None:
    if description and description.startswith(REJECTION_PREFIX):
        end = description.find("]")
        if end > len(REJECTION_PREFIX):
            return description[len(REJECTION_PREFIX):end]
    return None
