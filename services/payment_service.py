"""Payment processing: one atomic Oracle transaction per payment.

Order of work inside the transaction
------------------------------------
1. Idempotency lookup (PAYMENT_TRANSACTIONS.IDEMPOTENCY_KEY is UNIQUE).
2. Read the two accounts, the sender's profile preference and the gateway.
3. INSERT the payment as INITIATED. A concurrent request with the same key
   blocks on the unique index here and then gets ORA-00001 (or proceeds if the
   first one rolled back).
4. PL/SQL block 1: lock the sender's PAYMENT_PROFILES row (serialises limit
   checks per user), then both BANK_ACCOUNTS rows in ascending ACCOUNT_ID
   order (prevents deadlocks between A->B and B->A), and read fresh values.
5. Python evaluates the business rules on those locked values.
6a. Rejected: mark the payment FAILED and audit it (no money moves), COMMIT.
6b. Accepted: PL/SQL block 2 sets PROCESSING, debits with a guarded UPDATE
   (balance >= amount), credits, writes DR/CR ledger rows, verifies they
   balance, sets SUCCESS and writes the audit rows. Then COMMIT.

Any exception before COMMIT rolls back everything, including the INSERT of
step 3, so no partial financial state can survive. The account row locks are
held only from step 4 to COMMIT (three network round trips), which keeps the
waiting time low when the database is a remote FreeSQL instance.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import oracledb

from config import get_settings
from database.connection import connection, fetch_all, fetch_one
from database.errors import constraint_name, error_code
from services import audit_service
from services.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationFailedError,
)
from services.payment_rules import (
    AccountState,
    GatewayState,
    ProfileState,
    Rejection,
    compute_fee,
    evaluate_payment,
    rejected_description,
    rejection_code,
    same_request,
)
from services.serialization import fit_bytes, money, money_str, serialize_row

log = logging.getLogger(__name__)

FAILURE_POINTS: dict[str, str] = {
    "AFTER_TRANSACTION_INSERT": "After the payment row is inserted, before any balance changes",
    "AFTER_DEBIT": "After the sender is debited, before the receiver is credited",
    "AFTER_BALANCE_UPDATES": "After both balances change, before the ledger entries are written",
    "BEFORE_COMMIT": "After everything is written (status SUCCESS, ledger, audit), just before COMMIT",
}

SQL_FIND_BY_KEY = """
SELECT transaction_id, sender_account_id, receiver_account_id, gateway_id, amount, status
  FROM PAYMENT_TRANSACTIONS
 WHERE idempotency_key = :idempotency_key
"""

SQL_LOAD_PARTIES = """
SELECT a.account_id, a.user_id, a.currency, a.account_status, p.preferred_gateway_id
  FROM BANK_ACCOUNTS a
  LEFT JOIN PAYMENT_PROFILES p ON p.user_id = a.user_id
 WHERE a.account_id IN (:sender_id, :receiver_id)
"""

SQL_LOAD_GATEWAY = """
SELECT gateway_id, gateway_name, gateway_status, fee_percentage
  FROM PAYMENT_GATEWAYS
 WHERE gateway_id = :gateway_id
"""

SQL_INSERT_TRANSACTION = """
INSERT INTO PAYMENT_TRANSACTIONS
       (transaction_ref, idempotency_key, sender_account_id, receiver_account_id, gateway_id,
        amount, fee_amount, currency, status, description)
VALUES (:transaction_ref, :idempotency_key, :sender_id, :receiver_id, :gateway_id,
        :amount, :fee_amount, :currency, 'INITIATED', :description)
RETURNING transaction_id INTO :transaction_id
"""

SQL_TRANSACTION_SUMMARY = """
SELECT t.transaction_id, t.transaction_ref, t.idempotency_key, t.sender_account_id,
       t.receiver_account_id, t.gateway_id, g.gateway_name, t.amount, t.fee_amount, t.currency,
       t.status, t.initiated_at, t.completed_at, t.description
  FROM PAYMENT_TRANSACTIONS t
  LEFT JOIN PAYMENT_GATEWAYS g ON g.gateway_id = t.gateway_id
 WHERE t.transaction_id = :transaction_id
"""

# The in-flight (uncommitted) state of one payment, as seen from inside its own
# transaction. Used by the failure-simulation hooks to show what a rollback undoes.
SQL_SNAPSHOT = """
SELECT (SELECT current_balance FROM BANK_ACCOUNTS WHERE account_id = :sender_id) AS sender_balance,
       (SELECT current_balance FROM BANK_ACCOUNTS WHERE account_id = :receiver_id) AS receiver_balance,
       (SELECT COUNT(*) FROM LEDGER_ENTRIES WHERE transaction_id = :transaction_id) AS ledger_entries,
       (SELECT status FROM PAYMENT_TRANSACTIONS WHERE transaction_id = :transaction_id) AS status
  FROM DUAL
"""

# The same snapshot written into OUT binds, for the hooks inside the PL/SQL block.
_SNAPSHOT_INTO = """
    SELECT (SELECT current_balance FROM BANK_ACCOUNTS WHERE account_id = :sender_id),
           (SELECT current_balance FROM BANK_ACCOUNTS WHERE account_id = :receiver_id),
           (SELECT COUNT(*) FROM LEDGER_ENTRIES WHERE transaction_id = :transaction_id),
           (SELECT status FROM PAYMENT_TRANSACTIONS WHERE transaction_id = :transaction_id)
      INTO :snap_sender_balance, :snap_receiver_balance, :snap_ledger_entries, :snap_status
      FROM DUAL;"""

PLSQL_LOCK_AND_READ = """
DECLARE
  v_locked NUMBER;
BEGIN
  BEGIN
    SELECT daily_limit, single_tx_limit, profile_status
      INTO :daily_limit, :single_tx_limit, :profile_status
      FROM PAYMENT_PROFILES
     WHERE user_id = :sender_user_id
       FOR UPDATE WAIT 30;
    :has_profile := 1;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      :has_profile := 0;
  END;

  SELECT account_id INTO v_locked FROM BANK_ACCOUNTS WHERE account_id = :first_account_id FOR UPDATE WAIT 30;
  SELECT account_id INTO v_locked FROM BANK_ACCOUNTS WHERE account_id = :second_account_id FOR UPDATE WAIT 30;

  SELECT current_balance, currency, account_status
    INTO :sender_balance, :sender_currency, :sender_status
    FROM BANK_ACCOUNTS WHERE account_id = :sender_id;
  SELECT current_balance, currency, account_status
    INTO :receiver_balance, :receiver_currency, :receiver_status
    FROM BANK_ACCOUNTS WHERE account_id = :receiver_id;

  SELECT NVL(SUM(t.amount), 0)
    INTO :spent_today
    FROM PAYMENT_TRANSACTIONS t
    JOIN BANK_ACCOUNTS a ON a.account_id = t.sender_account_id
   WHERE a.user_id = :sender_user_id
     AND t.status = 'SUCCESS'
     AND t.initiated_at >= TRUNC(SYSDATE);
END;
"""

PLSQL_REJECT = """
BEGIN
  UPDATE PAYMENT_TRANSACTIONS
     SET status = 'FAILED', completed_at = SYSTIMESTAMP, description = :description
   WHERE transaction_id = :transaction_id AND status = 'INITIATED';
  IF SQL%ROWCOUNT <> 1 THEN
    RAISE_APPLICATION_ERROR(-20015, 'Payment is no longer in INITIATED state');
  END IF;

  INSERT INTO AUDIT_LOGS (table_name, action_type, record_id, account_id, transaction_id, details)
  VALUES ('PAYMENT_TRANSACTIONS', 'INSERT', :transaction_id, :sender_id, :transaction_id, :audit_details);
END;
"""

PLSQL_APPLY_TRANSFER = (
    """
DECLARE
  v_dr NUMBER;
  v_cr NUMBER;
BEGIN
  :stopped_at := NULL;

  UPDATE PAYMENT_TRANSACTIONS SET status = 'PROCESSING'
   WHERE transaction_id = :transaction_id AND status = 'INITIATED';
  IF SQL%ROWCOUNT <> 1 THEN
    RAISE_APPLICATION_ERROR(-20010, 'Payment is no longer in INITIATED state');
  END IF;

  UPDATE BANK_ACCOUNTS SET current_balance = current_balance - :amount
   WHERE account_id = :sender_id AND account_status = 'ACTIVE' AND current_balance >= :amount;
  IF SQL%ROWCOUNT <> 1 THEN
    RAISE_APPLICATION_ERROR(-20011, 'Sender debit guard failed');
  END IF;

  IF :failure_point = 'AFTER_DEBIT' THEN
    """
    + _SNAPSHOT_INTO
    + """
    :stopped_at := 'AFTER_DEBIT';
    RETURN;
  END IF;

  UPDATE BANK_ACCOUNTS SET current_balance = current_balance + :amount
   WHERE account_id = :receiver_id AND account_status = 'ACTIVE';
  IF SQL%ROWCOUNT <> 1 THEN
    RAISE_APPLICATION_ERROR(-20012, 'Receiver credit guard failed');
  END IF;

  IF :failure_point = 'AFTER_BALANCE_UPDATES' THEN
    """
    + _SNAPSHOT_INTO
    + """
    :stopped_at := 'AFTER_BALANCE_UPDATES';
    RETURN;
  END IF;

  INSERT INTO LEDGER_ENTRIES (transaction_id, account_id, entry_type, amount, description)
  VALUES (:transaction_id, :sender_id, 'DR', :amount, :debit_description);
  INSERT INTO LEDGER_ENTRIES (transaction_id, account_id, entry_type, amount, description)
  VALUES (:transaction_id, :receiver_id, 'CR', :amount, :credit_description);

  SELECT NVL(SUM(CASE WHEN entry_type = 'DR' THEN amount END), 0),
         NVL(SUM(CASE WHEN entry_type = 'CR' THEN amount END), 0)
    INTO v_dr, v_cr
    FROM LEDGER_ENTRIES WHERE transaction_id = :transaction_id;
  IF v_dr <> v_cr OR v_dr <> :amount THEN
    RAISE_APPLICATION_ERROR(-20013, 'Ledger entries are not balanced');
  END IF;

  UPDATE PAYMENT_TRANSACTIONS SET status = 'SUCCESS', completed_at = SYSTIMESTAMP
   WHERE transaction_id = :transaction_id AND status = 'PROCESSING';
  IF SQL%ROWCOUNT <> 1 THEN
    RAISE_APPLICATION_ERROR(-20014, 'Payment is no longer in PROCESSING state');
  END IF;

  INSERT INTO AUDIT_LOGS (table_name, action_type, record_id, account_id, transaction_id, details)
  VALUES ('PAYMENT_TRANSACTIONS', 'INSERT', :transaction_id, :sender_id, :transaction_id, :audit_payment);
  INSERT INTO AUDIT_LOGS (table_name, action_type, record_id, account_id, transaction_id, details)
  VALUES ('BANK_ACCOUNTS', 'UPDATE', :sender_id, :sender_id, :transaction_id, :audit_debit);
  INSERT INTO AUDIT_LOGS (table_name, action_type, record_id, account_id, transaction_id, details)
  VALUES ('BANK_ACCOUNTS', 'UPDATE', :receiver_id, :receiver_id, :transaction_id, :audit_credit);
END;
"""
)


@dataclass(frozen=True)
class PaymentCommand:
    sender_account_id: int
    receiver_account_id: int
    amount: Decimal
    idempotency_key: str
    gateway_id: int | None = None
    description: str | None = None


@dataclass
class PaymentOutcome:
    transaction: dict[str, Any]
    replayed: bool = False
    rejection: Rejection | None = None

    @property
    def status(self) -> str:
        return self.transaction["status"]


class SimulatedFailure(Exception):
    """Raised on purpose by the failure-simulation hooks (never in normal operation)."""

    def __init__(self, point: str, transaction_id: int, snapshot: dict[str, Any]) -> None:
        super().__init__(f"Simulated failure at {point}")
        self.point = point
        self.transaction_id = transaction_id
        self.snapshot = snapshot


class _DuplicateKey(Exception):
    pass


@dataclass
class _Progress:
    """What the in-flight payment has done so far (for the rollback audit record)."""

    transaction_id: int | None = None
    sender_id: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def check_failure_point(failure_point: str | None) -> None:
    """Failure hooks are refused unless ENABLE_FAILURE_SIMULATION=true."""
    if failure_point is None:
        return
    if not get_settings().enable_failure_simulation:
        raise ForbiddenError(
            "Failure simulation is disabled. Set ENABLE_FAILURE_SIMULATION=true in .env to use it.",
            code="FAILURE_SIMULATION_DISABLED",
        )
    if failure_point not in FAILURE_POINTS:
        raise ValidationFailedError(f"Unknown failure point {failure_point!r}.")


def create_payment(command: PaymentCommand, *, failure_point: str | None = None) -> PaymentOutcome:
    check_failure_point(failure_point)
    amount = money(command.amount)
    if amount <= 0:
        raise ValidationFailedError("The amount must be greater than zero.", code="INVALID_AMOUNT")
    if command.sender_account_id == command.receiver_account_id:
        raise ValidationFailedError("Sender and receiver accounts must be different.", code="SAME_ACCOUNT")

    progress = _Progress(sender_id=command.sender_account_id)
    with connection() as conn:
        cur = conn.cursor()
        existing = fetch_one(cur, SQL_FIND_BY_KEY, {"idempotency_key": command.idempotency_key})
        if existing:
            return _replay(conn, existing, command, amount)
        try:
            return _process(conn, command, amount, failure_point, progress)
        except _DuplicateKey:
            # Another request with the same key committed first while we waited
            # on the unique index; return its result instead of paying twice.
            conn.rollback()
            existing = fetch_one(conn.cursor(), SQL_FIND_BY_KEY, {"idempotency_key": command.idempotency_key})
            if existing is None:
                raise ConflictError(
                    "A request with this idempotency key is still being processed. Retry shortly.",
                    code="IDEMPOTENCY_IN_PROGRESS",
                )
            return _replay(conn, existing, command, amount)
        except SimulatedFailure as failure:
            if _rollback_quietly(conn):
                _audit_rollback(conn, progress, command, "SIMULATED_FAILURE_ROLLED_BACK", failure_point=failure.point)
            raise
        except Exception as exc:
            if _rollback_quietly(conn) and progress.transaction_id is not None:
                _audit_rollback(conn, progress, command, "PAYMENT_ROLLED_BACK", error_code=_code_of(exc))
            raise


def _rollback_quietly(conn) -> bool:
    """Roll back; return False if the connection is unusable (Oracle then rolls back itself)."""
    try:
        conn.rollback()
        return True
    except oracledb.Error:
        return False


def _code_of(exc: BaseException) -> str:
    if isinstance(exc, oracledb.Error):
        return error_code(exc) or type(exc).__name__
    return getattr(exc, "code", None) or type(exc).__name__


def _process(
    conn: oracledb.Connection,
    command: PaymentCommand,
    amount: Decimal,
    failure_point: str | None,
    progress: _Progress,
) -> PaymentOutcome:
    cur = conn.cursor()
    sender_id, receiver_id = command.sender_account_id, command.receiver_account_id
    actor = get_settings().app_actor

    parties = {int(r["account_id"]): r for r in fetch_all(cur, SQL_LOAD_PARTIES, {"sender_id": sender_id, "receiver_id": receiver_id})}
    if sender_id not in parties:
        raise NotFoundError(f"Sender account {sender_id} does not exist.", code="ACCOUNT_NOT_FOUND")
    if receiver_id not in parties:
        raise NotFoundError(f"Receiver account {receiver_id} does not exist.", code="ACCOUNT_NOT_FOUND")
    sender_row = parties[sender_id]

    gateway_id = command.gateway_id
    if gateway_id is None and sender_row.get("preferred_gateway_id") is not None:
        gateway_id = int(sender_row["preferred_gateway_id"])
    gateway: GatewayState | None = None
    if gateway_id is not None:
        row = fetch_one(cur, SQL_LOAD_GATEWAY, {"gateway_id": gateway_id})
        if row is None:
            raise NotFoundError(f"Payment gateway {gateway_id} does not exist.", code="GATEWAY_NOT_FOUND")
        gateway = GatewayState(int(row["gateway_id"]), row["gateway_status"], row["fee_percentage"])

    fee = compute_fee(amount, gateway)
    memo = fit_bytes(command.description, 200)
    txn_var = cur.var(int)
    try:
        cur.execute(
            SQL_INSERT_TRANSACTION,
            {
                "transaction_ref": str(uuid.uuid4()),
                "idempotency_key": command.idempotency_key,
                "sender_id": sender_id,
                "receiver_id": receiver_id,
                "gateway_id": gateway_id,
                "amount": amount,
                "fee_amount": fee,
                "currency": sender_row["currency"],
                "description": memo,
                "transaction_id": txn_var,
            },
        )
    except oracledb.IntegrityError as exc:
        if constraint_name(exc) == "UQ_IDEMPOTENCY_KEY":
            raise _DuplicateKey() from exc
        raise
    txn_id = int(txn_var.getvalue()[0])
    progress.transaction_id = txn_id
    ids = {"sender_id": sender_id, "receiver_id": receiver_id, "transaction_id": txn_id}
    _python_checkpoint("AFTER_TRANSACTION_INSERT", failure_point, cur, ids)

    # ---- critical section: rows locked from here until COMMIT/ROLLBACK ----
    out = {
        "daily_limit": cur.var(Decimal),
        "single_tx_limit": cur.var(Decimal),
        "profile_status": cur.var(str, 20),
        "has_profile": cur.var(int),
        "sender_balance": cur.var(Decimal),
        "sender_currency": cur.var(str, 3),
        "sender_status": cur.var(str, 20),
        "receiver_balance": cur.var(Decimal),
        "receiver_currency": cur.var(str, 3),
        "receiver_status": cur.var(str, 20),
        "spent_today": cur.var(Decimal),
    }
    cur.execute(
        PLSQL_LOCK_AND_READ,
        {
            "sender_user_id": int(sender_row["user_id"]),
            "first_account_id": min(sender_id, receiver_id),
            "second_account_id": max(sender_id, receiver_id),
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            **out,
        },
    )
    value = {name: var.getvalue() for name, var in out.items()}
    sender = AccountState(sender_id, value["sender_balance"], value["sender_currency"], value["sender_status"])
    receiver = AccountState(receiver_id, value["receiver_balance"], value["receiver_currency"], value["receiver_status"])
    profile = (
        ProfileState(value["daily_limit"], value["single_tx_limit"], value["profile_status"])
        if value["has_profile"] == 1
        else None
    )
    rejection = evaluate_payment(amount, sender, receiver, profile, value["spent_today"], gateway)

    base_details = {
        "transaction_id": txn_id,
        "idempotency_key": command.idempotency_key,
        "amount": money_str(amount),
        "currency": sender.currency,
        "sender_account_id": sender_id,
        "receiver_account_id": receiver_id,
        "gateway_id": gateway_id,
        "actor": actor,
    }

    if rejection is not None:
        cur.execute(
            PLSQL_REJECT,
            {
                "transaction_id": txn_id,
                "sender_id": sender_id,
                "description": fit_bytes(rejected_description(rejection.code, memo), 255),
                "audit_details": audit_service.details_json(
                    "PAYMENT_REJECTED", reason=rejection.code, message=rejection.message,
                    balance=money_str(sender.balance), **base_details,
                ),
            },
        )
        conn.commit()
        return PaymentOutcome(transaction=load_transaction(cur, txn_id), rejection=rejection)

    stopped_at = cur.var(str, 40)
    snapshot_vars = {
        "snap_sender_balance": cur.var(Decimal),
        "snap_receiver_balance": cur.var(Decimal),
        "snap_ledger_entries": cur.var(int),
        "snap_status": cur.var(str, 20),
    }
    sender_after = money(sender.balance) - amount
    receiver_after = money(receiver.balance) + amount
    cur.execute(
        PLSQL_APPLY_TRANSFER,
        {
            **ids,
            "amount": amount,
            "failure_point": failure_point,
            "stopped_at": stopped_at,
            **snapshot_vars,
            "debit_description": fit_bytes(f"Payment {txn_id} to account {receiver_id}", 200),
            "credit_description": fit_bytes(f"Payment {txn_id} from account {sender_id}", 200),
            "audit_payment": audit_service.details_json(
                "PAYMENT_SUCCESS", fee_amount=money_str(fee), **base_details
            ),
            "audit_debit": audit_service.details_json(
                "BALANCE_DEBITED", account_id=sender_id, transaction_id=txn_id, amount=money_str(amount),
                balance_before=money_str(sender.balance), balance_after=money_str(sender_after), actor=actor,
            ),
            "audit_credit": audit_service.details_json(
                "BALANCE_CREDITED", account_id=receiver_id, transaction_id=txn_id, amount=money_str(amount),
                balance_before=money_str(receiver.balance), balance_after=money_str(receiver_after), actor=actor,
            ),
        },
    )
    if stopped_at.getvalue():
        snap = {name.removeprefix("snap_"): var.getvalue() for name, var in snapshot_vars.items()}
        raise SimulatedFailure(stopped_at.getvalue(), txn_id, _snapshot_dict(snap))
    _python_checkpoint("BEFORE_COMMIT", failure_point, cur, ids)
    conn.commit()
    return PaymentOutcome(transaction=load_transaction(cur, txn_id))


def _python_checkpoint(point: str, failure_point: str | None, cur, ids: dict[str, int]) -> None:
    if failure_point != point:
        return
    row = fetch_one(cur, SQL_SNAPSHOT, ids) or {}
    raise SimulatedFailure(point, ids["transaction_id"], _snapshot_dict(row))


def _snapshot_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "transaction_status": row.get("status"),
        "sender_balance": money_str(row.get("sender_balance")),
        "receiver_balance": money_str(row.get("receiver_balance")),
        "ledger_entries": int(row["ledger_entries"]) if row.get("ledger_entries") is not None else 0,
    }


def _replay(conn, existing: dict[str, Any], command: PaymentCommand, amount: Decimal) -> PaymentOutcome:
    cur = conn.cursor()
    txn_id = int(existing["transaction_id"])
    details = {
        "idempotency_key": command.idempotency_key,
        "original_transaction_id": txn_id,
        "actor": get_settings().app_actor,
    }
    if not same_request(existing, command.sender_account_id, command.receiver_account_id, amount, command.gateway_id):
        audit_service.write(
            cur,
            [audit_service.audit_row(
                "PAYMENT_TRANSACTIONS", "SECURITY", txn_id,
                audit_service.details_json(
                    "IDEMPOTENCY_KEY_CONFLICT",
                    requested_sender=command.sender_account_id,
                    requested_receiver=command.receiver_account_id,
                    requested_amount=money_str(amount), **details,
                ),
                account_id=int(existing["sender_account_id"]), transaction_id=txn_id,
            )],
        )
        conn.commit()
        raise ConflictError(
            "This idempotency key was already used for a different payment. Use a new key for a new payment.",
            code="IDEMPOTENCY_KEY_REUSED",
            details={"transaction_id": txn_id},
        )
    audit_service.write(
        cur,
        [audit_service.audit_row(
            "PAYMENT_TRANSACTIONS", "SECURITY", txn_id,
            audit_service.details_json("DUPLICATE_REQUEST_REPLAYED", original_status=existing["status"], **details),
            account_id=int(existing["sender_account_id"]), transaction_id=txn_id,
        )],
    )
    conn.commit()
    transaction = load_transaction(cur, txn_id)
    rejection = None
    if transaction["status"] == "FAILED":
        code = transaction.get("rejection_code") or "REJECTED"
        rejection = Rejection(code, "The original request with this idempotency key was rejected.")
    return PaymentOutcome(transaction=transaction, replayed=True, rejection=rejection)


def _audit_rollback(conn, progress: _Progress, command: PaymentCommand, event: str, **context: Any) -> None:
    """Record that a payment was rolled back, in a separate transaction (best effort).

    The payment row no longer exists after the rollback, so TRANSACTION_ID stays
    NULL (it is a foreign key) and RECORD_ID keeps the rolled-back identifier.
    """
    if progress.transaction_id is None:
        return
    try:
        audit_service.write(
            conn.cursor(),
            [audit_service.audit_row(
                "PAYMENT_TRANSACTIONS", "SECURITY", progress.transaction_id,
                audit_service.details_json(
                    event,
                    rolled_back_transaction_id=progress.transaction_id,
                    idempotency_key=command.idempotency_key,
                    amount=money_str(command.amount),
                    sender_account_id=command.sender_account_id,
                    receiver_account_id=command.receiver_account_id,
                    actor=get_settings().app_actor,
                    **context,
                ),
                account_id=progress.sender_id,
            )],
        )
        conn.commit()
    except Exception:  # the audit must never hide the original error
        log.exception("Could not write the rollback audit record")
        try:
            conn.rollback()
        except oracledb.Error:
            pass


def load_transaction(cur, transaction_id: int) -> dict[str, Any]:
    row = fetch_one(cur, SQL_TRANSACTION_SUMMARY, {"transaction_id": transaction_id})
    if row is None:
        raise NotFoundError(f"Transaction {transaction_id} does not exist.")
    data = serialize_row(row)
    data["rejection_code"] = rejection_code(row.get("description"))
    return data
