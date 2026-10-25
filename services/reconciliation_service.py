"""Reconciliation of internal payments against (simulated) gateway settlement records.

Matching key: EXTERNAL_TRANSACTIONS.INTERNAL_TRANSACTION_REF = PAYMENT_TRANSACTIONS.TRANSACTION_REF.
A batch covers one gateway and one settlement date:

* every external record settled that day is matched by reference;
* a matched pair is compared on amount and on status (mapped, see STATUS_MAP);
* an external record with no matching internal payment -> MISSING_IN_INTERNAL;
* a successful internal payment of that day that the gateway never reported
  (on any date) -> MISSING_IN_GATEWAY;
* a second external record for the same payment is a duplicate settlement.
  The schema's CHK_EXCEPTION_TYPE has no type for it, so duplicates are
  reported and audited but only stored as exceptions after the optional
  migration sql/migrations/001 has been approved and applied.

Reconciliation never changes balances and never resolves exceptions by itself.
A discrepancy that was already recorded by an earlier batch (same type, same
internal and external record) is not inserted again.
"""

from __future__ import annotations

import logging
import random
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import oracledb

from config import get_settings
from database.connection import connection, fetch_all, fetch_one
from database.schema import duplicate_type_allowed
from services import audit_service
from services.errors import ConflictError, NotFoundError
from services.serialization import fit_bytes, money, money_str, serialize_row, serialize_rows

log = logging.getLogger(__name__)

# Internal PAYMENT_TRANSACTIONS.STATUS -> the status a gateway should report.
STATUS_MAP = {
    "SUCCESS": "SUCCESS",
    "FAILED": "FAILURE",
    "REVERSED": "REFUNDED",
    "INITIATED": "PENDING",
    "PROCESSING": "PENDING",
}
EXTERNAL_STATUSES = ("SUCCESS", "FAILURE", "PENDING", "REFUNDED")
EXCEPTION_TYPES = ("AMOUNT_MISMATCH", "STATUS_MISMATCH", "MISSING_IN_GATEWAY", "MISSING_IN_INTERNAL")
DUPLICATE_TYPE = "DUPLICATE_EXTERNAL"
SIMULATED_PREFIX = "SIM-"

# ----------------------------------------------------------------------------- SQL

SQL_GATEWAY = """
SELECT gateway_id, gateway_name, gateway_status
  FROM PAYMENT_GATEWAYS
 WHERE gateway_id = :gateway_id
"""

SQL_LOCK_GATEWAY = """
SELECT gateway_id, gateway_name
  FROM PAYMENT_GATEWAYS
 WHERE gateway_id = :gateway_id
   FOR UPDATE WAIT 30
"""

SQL_INSERT_BATCH = """
INSERT INTO RECONCILIATION_BATCHES (gateway_id, batch_date, batch_status)
VALUES (:gateway_id, :batch_date, :batch_status)
RETURNING batch_id INTO :batch_id
"""

SQL_EXTERNAL_FOR_BATCH = """
SELECT e.ext_transaction_id, e.external_reference_id, e.internal_transaction_ref,
       e.reported_amount, e.reported_fee, e.reported_status
  FROM EXTERNAL_TRANSACTIONS e
 WHERE e.gateway_id = :gateway_id
   AND e.settlement_date >= :batch_date
   AND e.settlement_date < :batch_date + 1
 ORDER BY e.ext_transaction_id
"""

SQL_INTERNAL_BY_REF = """
SELECT t.transaction_id, t.transaction_ref, t.gateway_id, t.amount, t.status
  FROM PAYMENT_TRANSACTIONS t
 WHERE t.transaction_ref IN (
       SELECT e.internal_transaction_ref
         FROM EXTERNAL_TRANSACTIONS e
        WHERE e.gateway_id = :gateway_id
          AND e.settlement_date >= :batch_date
          AND e.settlement_date < :batch_date + 1)
"""

SQL_INTERNAL_WINDOW = """
SELECT t.transaction_id, t.transaction_ref, t.amount, t.status,
       CASE WHEN EXISTS (SELECT 1
                           FROM EXTERNAL_TRANSACTIONS e
                          WHERE e.gateway_id = t.gateway_id
                            AND e.internal_transaction_ref = t.transaction_ref)
            THEN 1 ELSE 0 END AS has_external
  FROM PAYMENT_TRANSACTIONS t
 WHERE t.gateway_id = :gateway_id
   AND t.status IN ('SUCCESS', 'REVERSED')
   AND t.initiated_at >= :batch_date
   AND t.initiated_at < :batch_date + 1
 ORDER BY t.transaction_id
"""

SQL_EXISTING_EXCEPTIONS = """
SELECT x.exception_type, x.internal_transaction_id, x.ext_transaction_id, x.batch_id, x.resolution_status
  FROM RECONCILIATION_EXCEPTIONS x
  JOIN RECONCILIATION_BATCHES b ON b.batch_id = x.batch_id
 WHERE b.gateway_id = :gateway_id
"""

SQL_INSERT_EXCEPTION = """
INSERT INTO RECONCILIATION_EXCEPTIONS
       (batch_id, internal_transaction_id, ext_transaction_id, exception_type,
        discrepancy_amount, resolution_notes)
VALUES (:batch_id, :internal_transaction_id, :ext_transaction_id, :exception_type,
        :discrepancy_amount, :resolution_notes)
"""

SQL_COMPLETE_BATCH = """
UPDATE RECONCILIATION_BATCHES
   SET total_records = :total_records,
       matched_records = :matched_records,
       exception_records = :exception_records,
       batch_status = 'COMPLETED'
 WHERE batch_id = :batch_id
   AND batch_status = 'PROCESSING'
"""

SQL_LIST_BATCHES = """
SELECT b.batch_id, b.gateway_id, g.gateway_name, b.batch_date, b.total_records, b.matched_records,
       b.exception_records, b.batch_status, b.execution_timestamp,
       (SELECT COUNT(*) FROM RECONCILIATION_EXCEPTIONS x
         WHERE x.batch_id = b.batch_id AND x.resolution_status = 'UNRESOLVED') AS unresolved_count,
       COUNT(*) OVER () AS total_count
  FROM RECONCILIATION_BATCHES b
  JOIN PAYMENT_GATEWAYS g ON g.gateway_id = b.gateway_id
 WHERE (:gateway_id IS NULL OR b.gateway_id = :gateway_id)
 ORDER BY b.batch_id DESC
 OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
"""

SQL_BATCH = """
SELECT b.batch_id, b.gateway_id, g.gateway_name, b.batch_date, b.total_records, b.matched_records,
       b.exception_records, b.batch_status, b.execution_timestamp
  FROM RECONCILIATION_BATCHES b
  JOIN PAYMENT_GATEWAYS g ON g.gateway_id = b.gateway_id
 WHERE b.batch_id = :batch_id
"""

SQL_LIST_EXCEPTIONS = """
SELECT x.exception_id, x.batch_id, b.gateway_id, g.gateway_name, b.batch_date,
       x.internal_transaction_id, t.transaction_ref, t.amount AS internal_amount, t.status AS internal_status,
       x.ext_transaction_id, e.external_reference_id, e.reported_amount, e.reported_status,
       x.exception_type, x.discrepancy_amount, x.resolution_status, x.resolution_notes, x.created_at,
       COUNT(*) OVER () AS total_count
  FROM RECONCILIATION_EXCEPTIONS x
  JOIN RECONCILIATION_BATCHES b ON b.batch_id = x.batch_id
  JOIN PAYMENT_GATEWAYS g ON g.gateway_id = b.gateway_id
  LEFT JOIN PAYMENT_TRANSACTIONS t ON t.transaction_id = x.internal_transaction_id
  LEFT JOIN EXTERNAL_TRANSACTIONS e ON e.ext_transaction_id = x.ext_transaction_id
 WHERE (:exception_id IS NULL OR x.exception_id = :exception_id)
   AND (:batch_id IS NULL OR x.batch_id = :batch_id)
   AND (:gateway_id IS NULL OR b.gateway_id = :gateway_id)
   AND (:exception_type IS NULL OR x.exception_type = :exception_type)
   AND (:resolution_status IS NULL OR x.resolution_status = :resolution_status)
 ORDER BY x.exception_id DESC
 OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
"""

SQL_LOCK_EXCEPTION = """
SELECT exception_id, batch_id, internal_transaction_id, exception_type, resolution_status
  FROM RECONCILIATION_EXCEPTIONS
 WHERE exception_id = :exception_id
   FOR UPDATE WAIT 30
"""

SQL_UPDATE_EXCEPTION = """
UPDATE RECONCILIATION_EXCEPTIONS
   SET resolution_status = :resolution_status,
       resolution_notes = :resolution_notes
 WHERE exception_id = :exception_id
"""

SQL_LIST_EXTERNAL = """
SELECT e.ext_transaction_id, e.gateway_id, g.gateway_name, e.external_reference_id,
       e.internal_transaction_ref, t.transaction_id AS matched_transaction_id,
       e.reported_amount, e.reported_fee, e.reported_status, e.settlement_date, e.created_at,
       COUNT(*) OVER () AS total_count
  FROM EXTERNAL_TRANSACTIONS e
  JOIN PAYMENT_GATEWAYS g ON g.gateway_id = e.gateway_id
  LEFT JOIN PAYMENT_TRANSACTIONS t ON t.transaction_ref = e.internal_transaction_ref
 WHERE (:gateway_id IS NULL OR e.gateway_id = :gateway_id)
   AND (:settlement_date IS NULL
        OR (e.settlement_date >= CAST(:settlement_date AS DATE)
            AND e.settlement_date < CAST(:settlement_date AS DATE) + 1))
 ORDER BY e.ext_transaction_id DESC
 OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
"""

SQL_SIMULATION_CANDIDATES = """
SELECT t.transaction_id, t.transaction_ref, t.amount, t.fee_amount, t.status
  FROM PAYMENT_TRANSACTIONS t
 WHERE t.gateway_id = :gateway_id
   AND t.status IN ('SUCCESS', 'FAILED', 'REVERSED')
   AND t.initiated_at >= :settlement_date
   AND t.initiated_at < :settlement_date + 1
   AND NOT EXISTS (SELECT 1
                     FROM EXTERNAL_TRANSACTIONS e
                    WHERE e.gateway_id = t.gateway_id
                      AND e.internal_transaction_ref = t.transaction_ref)
 ORDER BY t.transaction_id
 FETCH FIRST 200 ROWS ONLY
"""

SQL_INSERT_EXTERNAL = """
INSERT INTO EXTERNAL_TRANSACTIONS
       (gateway_id, external_reference_id, internal_transaction_ref, reported_amount,
        reported_fee, reported_status, settlement_date)
VALUES (:gateway_id, :external_reference_id, :internal_transaction_ref, :reported_amount,
        :reported_fee, :reported_status, :settlement_date)
RETURNING ext_transaction_id INTO :ext_transaction_id
"""

# ---------------------------------------------------------------- pure matching


@dataclass(frozen=True)
class Finding:
    exception_type: str
    internal_transaction_id: int | None
    ext_transaction_id: int | None
    discrepancy_amount: Decimal
    notes: str

    @property
    def key(self) -> tuple[str, int | None, int | None]:
        return (self.exception_type, self.internal_transaction_id, self.ext_transaction_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "exception_type": self.exception_type,
            "internal_transaction_id": self.internal_transaction_id,
            "ext_transaction_id": self.ext_transaction_id,
            "discrepancy_amount": money_str(self.discrepancy_amount),
            "notes": self.notes,
        }


@dataclass
class MatchResult:
    total_records: int
    matched_records: int
    findings: list[Finding]
    duplicates: list[Finding]


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


def classify(
    gateway_id: int,
    external_rows: list[dict[str, Any]],
    internal_by_ref: dict[str, dict[str, Any]],
    internal_window: list[dict[str, Any]],
) -> MatchResult:
    """Compare one day's gateway records with internal payments (no database access)."""
    findings: list[Finding] = []
    duplicates: list[Finding] = []
    matched = 0
    seen_refs: set[str] = set()

    for ext in sorted(external_rows, key=lambda r: int(r["ext_transaction_id"])):
        ext_id = int(ext["ext_transaction_id"])
        reported = money(ext["reported_amount"])
        ref = ext.get("internal_transaction_ref")
        internal = internal_by_ref.get(ref) if ref else None
        if internal is None:
            note = (
                "The gateway record carries no internal reference."
                if not ref
                else f"No internal payment has reference {ref}."
            )
            findings.append(Finding("MISSING_IN_INTERNAL", None, ext_id, reported, note))
            continue
        internal_id = int(internal["transaction_id"])
        if _int(internal.get("gateway_id")) != gateway_id:
            findings.append(
                Finding(
                    "MISSING_IN_INTERNAL", None, ext_id, reported,
                    f"Reference {ref} belongs to payment {internal_id}, which was routed through a different gateway.",
                )
            )
            continue
        if ref in seen_refs:
            duplicates.append(
                Finding(DUPLICATE_TYPE, internal_id, ext_id, reported,
                        f"Second gateway settlement record for payment {internal_id}.")
            )
            continue
        seen_refs.add(ref)

        internal_amount = money(internal["amount"])
        expected_status = STATUS_MAP.get(internal["status"])
        problems: list[Finding] = []
        if reported != internal_amount:
            problems.append(
                Finding("AMOUNT_MISMATCH", internal_id, ext_id, reported - internal_amount,
                        f"Gateway reported {reported}, internal amount is {internal_amount}.")
            )
        if expected_status != ext["reported_status"]:
            problems.append(
                Finding("STATUS_MISMATCH", internal_id, ext_id, internal_amount,
                        f"Internal status {internal['status']} should be reported as {expected_status}; "
                        f"gateway reported {ext['reported_status']}.")
            )
        if problems:
            findings.extend(problems)
        else:
            matched += 1

    missing_in_gateway = 0
    for internal in internal_window:
        if int(internal["has_external"]) == 0 and internal["transaction_ref"] not in seen_refs:
            missing_in_gateway += 1
            findings.append(
                Finding("MISSING_IN_GATEWAY", int(internal["transaction_id"]), None, money(internal["amount"]),
                        f"{internal['status']} payment was never reported by the gateway.")
            )
    return MatchResult(
        total_records=len(external_rows) + missing_in_gateway,
        matched_records=matched,
        findings=findings,
        duplicates=duplicates,
    )


def split_new(findings: list[Finding], existing: dict[tuple, dict[str, Any]]) -> tuple[list[Finding], list[dict[str, Any]]]:
    """Separate findings already stored by an earlier batch from new ones."""
    new: list[Finding] = []
    previous: list[dict[str, Any]] = []
    for finding in findings:
        if finding.key in existing:
            prior = existing[finding.key]
            previous.append({**finding.as_dict(), "batch_id": prior["batch_id"],
                             "resolution_status": prior["resolution_status"]})
        else:
            new.append(finding)
    return new, previous


def plan_simulated_records(
    candidates: list[dict[str, Any]],
    *,
    amount_mismatches: int,
    status_mismatches: int,
    missing_in_gateway: int,
    missing_in_internal: int,
    duplicates: int,
    rng: random.Random,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build simulated gateway records for internal payments, with chosen anomalies.

    Returns (records to insert, omitted payments). Each record carries an
    `injected` label saying which discrepancy it was made to produce.
    """
    pool = list(candidates)
    rng.shuffle(pool)
    successes = [c for c in pool if c["status"] == "SUCCESS"]
    omitted = successes[:missing_in_gateway]
    omitted_ids = {c["transaction_id"] for c in omitted}
    remaining = [c for c in pool if c["transaction_id"] not in omitted_ids]

    records: list[dict[str, Any]] = []
    for index, txn in enumerate(remaining):
        amount = money(txn["amount"])
        status = STATUS_MAP.get(txn["status"], "PENDING")
        injected = "MATCH"
        if index < amount_mismatches:
            delta = money(rng.choice(["-25.00", "-10.00", "-1.00", "5.00", "50.00"]))
            amount = amount + delta if amount + delta > 0 else amount + abs(delta)
            injected = "AMOUNT_MISMATCH"
        elif index < amount_mismatches + status_mismatches:
            status = rng.choice([s for s in EXTERNAL_STATUSES if s != status])
            injected = "STATUS_MISMATCH"
        records.append(
            {
                "external_reference_id": _simulated_ref(),
                "internal_transaction_ref": txn["transaction_ref"],
                "reported_amount": amount,
                "reported_fee": money(txn.get("fee_amount") or 0),
                "reported_status": status,
                "transaction_id": int(txn["transaction_id"]),
                "injected": injected,
            }
        )
    for original in [r for r in records if r["injected"] == "MATCH"][:duplicates]:
        records.append({**original, "external_reference_id": _simulated_ref(), "injected": DUPLICATE_TYPE})
    for _ in range(missing_in_internal):
        records.append(
            {
                "external_reference_id": _simulated_ref(),
                "internal_transaction_ref": str(uuid.uuid4()),
                "reported_amount": money(rng.randrange(100, 5000)),
                "reported_fee": money(0),
                "reported_status": "SUCCESS",
                "transaction_id": None,
                "injected": "MISSING_IN_INTERNAL",
            }
        )
    return records, omitted


def _simulated_ref(kind: str = "G") -> str:
    return f"{SIMULATED_PREFIX}{kind}-{uuid.uuid4().hex[:16].upper()}"


# --------------------------------------------------------------- database work


def _gateway(cur, gateway_id: int) -> dict[str, Any]:
    row = fetch_one(cur, SQL_GATEWAY, {"gateway_id": gateway_id})
    if row is None:
        raise NotFoundError(f"Payment gateway {gateway_id} does not exist.", code="GATEWAY_NOT_FOUND")
    return row


def _insert_external(cur, gateway_id: int, settlement_date: date, records: list[dict[str, Any]]) -> list[int]:
    ids: list[int] = []
    for record in records:
        id_var = cur.var(int)
        cur.execute(
            SQL_INSERT_EXTERNAL,
            {
                "gateway_id": gateway_id,
                "external_reference_id": record["external_reference_id"],
                "internal_transaction_ref": record["internal_transaction_ref"],
                "reported_amount": money(record["reported_amount"]),
                "reported_fee": money(record["reported_fee"]),
                "reported_status": record["reported_status"],
                "settlement_date": settlement_date,
                "ext_transaction_id": id_var,
            },
        )
        ids.append(int(id_var.getvalue()[0]))
    return ids


def generate_simulated_external(
    *,
    gateway_id: int,
    settlement_date: date,
    amount_mismatches: int = 1,
    status_mismatches: int = 1,
    missing_in_gateway: int = 1,
    missing_in_internal: int = 1,
    duplicates: int = 1,
    seed: int | None = None,
) -> dict[str, Any]:
    """Create clearly labelled (SIM-...) gateway records for one gateway and day."""
    with connection() as conn:
        cur = conn.cursor()
        gateway = _gateway(cur, gateway_id)
        candidates = fetch_all(
            cur, SQL_SIMULATION_CANDIDATES, {"gateway_id": gateway_id, "settlement_date": settlement_date}
        )
        records, omitted = plan_simulated_records(
            candidates,
            amount_mismatches=amount_mismatches,
            status_mismatches=status_mismatches,
            missing_in_gateway=missing_in_gateway,
            missing_in_internal=missing_in_internal,
            duplicates=duplicates,
            rng=random.Random(seed),
        )
        ids = _insert_external(cur, gateway_id, settlement_date, records)
        if ids:
            audit_service.write(
                cur,
                [audit_service.audit_row(
                    "EXTERNAL_TRANSACTIONS", "INSERT", ids[0],
                    audit_service.details_json(
                        "SIMULATED_EXTERNAL_RECORDS_CREATED",
                        gateway_id=gateway_id,
                        settlement_date=settlement_date,
                        records=len(ids),
                        ext_transaction_ids=[ids[0], ids[-1]],
                        injected={k: sum(1 for r in records if r["injected"] == k)
                                  for k in {r["injected"] for r in records}},
                        omitted_payments=[int(c["transaction_id"]) for c in omitted],
                        simulated=True,
                        actor=get_settings().app_actor,
                    ),
                )],
            )
        conn.commit()
    created = []
    for record, ext_id in zip(records, ids):
        created.append({
            "ext_transaction_id": ext_id,
            "external_reference_id": record["external_reference_id"],
            "internal_transaction_ref": record["internal_transaction_ref"],
            "transaction_id": record["transaction_id"],
            "reported_amount": money_str(record["reported_amount"]),
            "reported_fee": money_str(record["reported_fee"]),
            "reported_status": record["reported_status"],
            "injected": record["injected"],
            "is_simulated": True,
        })
    return {
        "gateway_id": gateway_id,
        "gateway_name": gateway["gateway_name"],
        "settlement_date": settlement_date.isoformat(),
        "candidates": len(candidates),
        "created": created,
        "omitted_payments": [
            {"transaction_id": int(c["transaction_id"]), "transaction_ref": c["transaction_ref"],
             "amount": money_str(c["amount"]), "injected": "MISSING_IN_GATEWAY"}
            for c in omitted
        ],
        "note": "Simulated records: every external_reference_id starts with SIM-. No real gateway was contacted.",
    }


def add_external_record(
    *,
    gateway_id: int,
    settlement_date: date,
    reported_amount: Decimal,
    reported_status: str,
    reported_fee: Decimal = Decimal("0"),
    internal_transaction_ref: str | None = None,
) -> dict[str, Any]:
    """Add one hand-made simulated gateway record (to stage a specific discrepancy)."""
    record = {
        "external_reference_id": _simulated_ref("M"),
        "internal_transaction_ref": internal_transaction_ref,
        "reported_amount": reported_amount,
        "reported_fee": reported_fee,
        "reported_status": reported_status,
    }
    with connection() as conn:
        cur = conn.cursor()
        _gateway(cur, gateway_id)
        ext_id = _insert_external(cur, gateway_id, settlement_date, [record])[0]
        audit_service.write(
            cur,
            [audit_service.audit_row(
                "EXTERNAL_TRANSACTIONS", "INSERT", ext_id,
                audit_service.details_json(
                    "SIMULATED_EXTERNAL_RECORD_ADDED", gateway_id=gateway_id,
                    external_reference_id=record["external_reference_id"],
                    internal_transaction_ref=internal_transaction_ref,
                    reported_amount=money_str(reported_amount), reported_status=reported_status,
                    simulated=True, actor=get_settings().app_actor,
                ),
            )],
        )
        conn.commit()
    return {
        "ext_transaction_id": ext_id,
        "external_reference_id": record["external_reference_id"],
        "internal_transaction_ref": internal_transaction_ref,
        "reported_amount": money_str(reported_amount),
        "reported_fee": money_str(reported_fee),
        "reported_status": reported_status,
        "settlement_date": settlement_date.isoformat(),
        "is_simulated": True,
    }


def run_batch(*, gateway_id: int, batch_date: date) -> dict[str, Any]:
    """Run one reconciliation batch in a single transaction (all or nothing)."""
    with connection() as conn:
        cur = conn.cursor()
        try:
            # Serialise runs for the same gateway so two batches cannot record
            # the same discrepancy twice at the same moment.
            gateway = fetch_one(cur, SQL_LOCK_GATEWAY, {"gateway_id": gateway_id})
            if gateway is None:
                raise NotFoundError(f"Payment gateway {gateway_id} does not exist.", code="GATEWAY_NOT_FOUND")
            batch_var = cur.var(int)
            cur.execute(SQL_INSERT_BATCH, {"gateway_id": gateway_id, "batch_date": batch_date,
                                           "batch_status": "PROCESSING", "batch_id": batch_var})
            batch_id = int(batch_var.getvalue()[0])
            scope = {"gateway_id": gateway_id, "batch_date": batch_date}
            external_rows = fetch_all(cur, SQL_EXTERNAL_FOR_BATCH, scope)
            internal_by_ref = {r["transaction_ref"]: r for r in fetch_all(cur, SQL_INTERNAL_BY_REF, scope)}
            internal_window = fetch_all(cur, SQL_INTERNAL_WINDOW, scope)
            existing = {
                (r["exception_type"], _int(r["internal_transaction_id"]), _int(r["ext_transaction_id"])): r
                for r in fetch_all(cur, SQL_EXISTING_EXCEPTIONS, {"gateway_id": gateway_id})
            }
            result = classify(gateway_id, external_rows, internal_by_ref, internal_window)
            store_duplicates = duplicate_type_allowed(cur)
            to_store = result.findings + (result.duplicates if store_duplicates else [])
            new, previous = split_new(to_store, existing)
            _insert_exceptions(cur, batch_id, new)
            cur.execute(
                SQL_COMPLETE_BATCH,
                {
                    "batch_id": batch_id,
                    "total_records": result.total_records,
                    "matched_records": result.matched_records,
                    "exception_records": len(new),
                },
            )
            by_type = {t: sum(1 for f in result.findings if f.exception_type == t) for t in EXCEPTION_TYPES}
            audit_service.write(
                cur,
                [audit_service.audit_row(
                    "RECONCILIATION_BATCHES", "INSERT", batch_id,
                    audit_service.details_json(
                        "RECONCILIATION_BATCH_COMPLETED", gateway_id=gateway_id, batch_date=batch_date,
                        total_records=result.total_records, matched_records=result.matched_records,
                        new_exceptions=len(new), previously_recorded=len(previous),
                        findings_by_type=by_type, duplicate_external_records=len(result.duplicates),
                        duplicates_stored=store_duplicates, actor=get_settings().app_actor,
                    ),
                )],
            )
            conn.commit()
        except Exception as exc:
            try:
                conn.rollback()
            except oracledb.Error:
                raise exc
            if not isinstance(exc, NotFoundError):
                _record_failed_batch(conn, gateway_id, batch_date, exc)
            raise
    return {
        "batch": get_batch(batch_id)["batch"],
        "total_records": result.total_records,
        "matched_records": result.matched_records,
        "new_exceptions": [f.as_dict() for f in new],
        "previously_recorded": previous,
        "duplicates": [f.as_dict() for f in result.duplicates],
        "duplicates_stored": store_duplicates,
        "findings_by_type": by_type,
        "note": None if store_duplicates else (
            "Duplicate gateway records are reported here but not stored: CHK_EXCEPTION_TYPE allows only four "
            "exception types. Optional migration sql/migrations/001 adds DUPLICATE_EXTERNAL."
        ),
    }


def _insert_exceptions(cur, batch_id: int, findings: list[Finding]) -> None:
    if not findings:
        return
    batch = cur.connection.cursor()
    batch.setinputsizes(
        internal_transaction_id=oracledb.DB_TYPE_NUMBER,
        ext_transaction_id=oracledb.DB_TYPE_NUMBER,
        discrepancy_amount=oracledb.DB_TYPE_NUMBER,
        resolution_notes=500,
    )
    batch.executemany(
        SQL_INSERT_EXCEPTION,
        [
            {
                "batch_id": batch_id,
                "internal_transaction_id": f.internal_transaction_id,
                "ext_transaction_id": f.ext_transaction_id,
                "exception_type": f.exception_type,
                "discrepancy_amount": f.discrepancy_amount,
                "resolution_notes": fit_bytes(f.notes, 500),
            }
            for f in findings
        ],
    )


def _record_failed_batch(conn, gateway_id: int, batch_date: date, exc: BaseException) -> None:
    """Keep evidence of a failed run: a FAILED batch row plus an audit record."""
    try:
        cur = conn.cursor()
        batch_var = cur.var(int)
        cur.execute(SQL_INSERT_BATCH, {"gateway_id": gateway_id, "batch_date": batch_date,
                                       "batch_status": "FAILED", "batch_id": batch_var})
        batch_id = int(batch_var.getvalue()[0])
        audit_service.write(
            cur,
            [audit_service.audit_row(
                "RECONCILIATION_BATCHES", "INSERT", batch_id,
                audit_service.details_json(
                    "RECONCILIATION_BATCH_FAILED", gateway_id=gateway_id, batch_date=batch_date,
                    error=getattr(exc, "code", type(exc).__name__), actor=get_settings().app_actor,
                ),
            )],
        )
        conn.commit()
    except Exception:
        log.exception("Could not record the failed reconciliation batch")


def list_batches(*, gateway_id: int | None = None, limit: int = 25, offset: int = 0) -> dict[str, Any]:
    with connection() as conn:
        rows = fetch_all(conn.cursor(), SQL_LIST_BATCHES, {"gateway_id": gateway_id, "limit": limit, "offset": offset})
    return _page(rows, limit, offset)


def get_batch(batch_id: int) -> dict[str, Any]:
    with connection() as conn:
        cur = conn.cursor()
        batch = fetch_one(cur, SQL_BATCH, {"batch_id": batch_id})
        if batch is None:
            raise NotFoundError(f"Reconciliation batch {batch_id} does not exist.", code="BATCH_NOT_FOUND")
        exceptions = fetch_all(cur, SQL_LIST_EXCEPTIONS, {
            "exception_id": None, "batch_id": batch_id, "gateway_id": None, "exception_type": None,
            "resolution_status": None, "limit": 500, "offset": 0,
        })
    page = _page(exceptions, 500, 0)
    return {"batch": serialize_row(batch), "exceptions": page["items"]}


def list_exceptions(
    *, batch_id: int | None = None, gateway_id: int | None = None, exception_type: str | None = None,
    resolution_status: str | None = None, limit: int = 50, offset: int = 0,
) -> dict[str, Any]:
    params = {
        "exception_id": None, "batch_id": batch_id, "gateway_id": gateway_id, "exception_type": exception_type,
        "resolution_status": resolution_status, "limit": limit, "offset": offset,
    }
    with connection() as conn:
        rows = fetch_all(conn.cursor(), SQL_LIST_EXCEPTIONS, params)
    return _page(rows, limit, offset)


def update_exception(exception_id: int, *, resolution_status: str, resolution_notes: str | None) -> dict[str, Any]:
    """Manual review of an exception. It records a decision; it never moves money."""
    with connection() as conn:
        cur = conn.cursor()
        current = fetch_one(cur, SQL_LOCK_EXCEPTION, {"exception_id": exception_id})
        if current is None:
            raise NotFoundError(f"Reconciliation exception {exception_id} does not exist.", code="EXCEPTION_NOT_FOUND")
        if current["resolution_status"] == "AUTO_RESOLVED":
            raise ConflictError("AUTO_RESOLVED exceptions cannot be changed manually.")
        cur.execute(SQL_UPDATE_EXCEPTION, {
            "exception_id": exception_id,
            "resolution_status": resolution_status,
            "resolution_notes": fit_bytes(resolution_notes, 500),
        })
        audit_service.write(
            cur,
            [audit_service.audit_row(
                "RECONCILIATION_EXCEPTIONS", "UPDATE", exception_id,
                audit_service.details_json(
                    "RECONCILIATION_EXCEPTION_REVIEWED", batch_id=int(current["batch_id"]),
                    exception_type=current["exception_type"],
                    old_status=current["resolution_status"], new_status=resolution_status,
                    actor=get_settings().app_actor,
                ),
                transaction_id=_int(current["internal_transaction_id"]),
            )],
        )
        conn.commit()
        rows = fetch_all(cur, SQL_LIST_EXCEPTIONS, {
            "exception_id": exception_id, "batch_id": None, "gateway_id": None, "exception_type": None,
            "resolution_status": None, "limit": 1, "offset": 0,
        })
    item = serialize_row(rows[0])
    item.pop("total_count", None)
    return item


def list_external(
    *, gateway_id: int | None = None, settlement_date: date | None = None, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    params = {"gateway_id": gateway_id, "settlement_date": settlement_date, "limit": limit, "offset": offset}
    with connection() as conn:
        rows = fetch_all(conn.cursor(), SQL_LIST_EXTERNAL, params)
    page = _page(rows, limit, offset)
    for item in page["items"]:
        item["is_simulated"] = str(item["external_reference_id"]).startswith(SIMULATED_PREFIX)
    return page


def _page(rows: list[dict[str, Any]], limit: int, offset: int) -> dict[str, Any]:
    total = int(rows[0]["total_count"]) if rows else 0
    items = serialize_rows(rows)
    for item in items:
        item.pop("total_count", None)
    return {"items": items, "total": total, "limit": limit, "offset": offset}
