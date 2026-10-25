# Architecture

## Layers

```
Browser (Bootstrap + fetch)  ──HTTP/JSON──►  FastAPI routes  ──►  services  ──►  database/connection.py  ──►  Oracle FreeSQL
  static/js/pages/*.js                        routes/*.py         services/*.py    python-oracledb Thin mode       10 tables
```

- **Routes** validate input (Pydantic, `models/schemas.py`) and call one service function. They contain no SQL.
- **Services** own all SQL. Every statement is a module-level constant with `:bind` variables; values are
  never formatted into SQL text (`tests/test_security.py` enforces this).
- **database/connection.py** keeps a connection pool, fetches `NUMBER` as `Decimal`, and turns any
  `oracledb` error into an application error. `database/errors.py` keeps only the ORA code and the
  constraint name, so SQL text, schema names and credentials never reach a response.
- **Frontend** is a single page with hash routes (`#/payment`, `#/transactions/12`, ...). It only shows
  what the API returns; every success or error on screen is a real backend response.

## The payment transaction

One payment = one Oracle transaction (`services/payment_service.py`):

| Step | SQL | Purpose |
|---|---|---|
| 1 | `SELECT ... WHERE idempotency_key = :k` | A retry with a known key returns the stored result |
| 2 | read both accounts, sender profile, gateway | existence checks (404 if missing), fee from `FEE_PERCENTAGE` |
| 3 | `INSERT ... status 'INITIATED'` | claims the idempotency key (UNIQUE index) |
| 4 | PL/SQL block 1: `FOR UPDATE WAIT 30` on the profile, then on both accounts in ascending `ACCOUNT_ID`; re-read balances, today's spending | serialises payments on the same account / user |
| 5 | Python `evaluate_payment()` | status, currency, gateway, per-payment limit, daily limit, balance |
| 6a | rejected: `UPDATE status 'FAILED'` + audit, COMMIT | the attempt is kept; no money moves |
| 6b | PL/SQL block 2: PROCESSING, guarded debit, credit, DR + CR ledger rows, DR = CR check, SUCCESS, 3 audit rows | all money movement |
| 7 | COMMIT | everything becomes visible at once |

Any exception before step 7 rolls back everything, including the INSERT of step 3. No intermediate state
is ever committed.

**Why two PL/SQL blocks.** FreeSQL is a remote cloud database, so each statement costs a network round
trip. Doing steps 4–7 statement by statement would hold the account locks for about twelve round trips.
The two anonymous blocks (no stored objects are created) reduce that to three, so waiting payments queue
for a much shorter time. The alternative, one stored procedure, would need `CREATE PROCEDURE`, a schema
change the team has not approved.

**State transitions.** `INITIATED → PROCESSING → SUCCESS` and `INITIATED → FAILED`. Each update is
written as `... WHERE status = '<expected>'` and checked with `SQL%ROWCOUNT`, so an unexpected state
aborts the transaction (`RAISE_APPLICATION_ERROR -2001x`, returned as `CONSISTENCY_GUARD`).

## Concurrency control

Three layers, from the main mechanism to the last safety net:

1. **Pessimistic row locks.** `SELECT ... FOR UPDATE WAIT 30` on both account rows, always lowest ID
   first. Two payments on the same account run one after the other at the database. Fixed lock order
   means A→B and B→A cannot deadlock. Balances are read *after* the lock is held, so no update is lost.
   The profile row is locked first, so the daily-limit check cannot be passed twice in parallel.
2. **Guarded update.** `UPDATE ... SET current_balance = current_balance - :amount WHERE ... AND
   current_balance >= :amount`, with a check that exactly one row changed.
3. **Declarative constraint.** `CHK_ACCOUNT_BALANCE (current_balance >= 0)` rejects any negative balance.

Oracle's default READ COMMITTED isolation is enough, because every read the decision depends on happens
under a row lock. SERIALIZABLE was considered: it would turn conflicts into `ORA-08177` errors that need
retries, instead of making requests wait their turn. No Python lock is involved (`threading.Lock` is not
used around payments; a unit test checks this).

The **concurrency lab** (`services/concurrency_service.py`) starts N threads that each call the normal
`create_payment()` with their own pooled connection. A `threading.Barrier` releases them together. It then
reads the database back and checks: no negative balance, no more successes than the balance allows, each
balance changed by exactly *amount × successes*, each success has one DR and one CR, rejected payments
have no ledger rows, and API answers match the database. In "same key" mode it checks that only one
payment row exists.

## Idempotency

The key lives in the existing `PAYMENT_TRANSACTIONS.IDEMPOTENCY_KEY` column, which has a UNIQUE
constraint, so no schema change was needed.

| Situation | Behaviour |
|---|---|
| Same key, same details, after the first finished | `200`, `replayed: true`, original transaction; a `DUPLICATE_REQUEST_REPLAYED` audit row |
| Same key, same details, while the first is still running | The second INSERT waits on the unique index; if the first commits it gets ORA-00001 and returns the original; if the first rolled back, it proceeds normally |
| Same key, different sender/receiver/amount/gateway | `409 IDEMPOTENCY_KEY_REUSED`, audited |
| Client timed out and does not know the result | Retry with the **same** key: either the original result comes back, or (if the first attempt rolled back) the payment runs once |
| Payment rejected by a business rule | The FAILED attempt keeps the key; retrying the same key returns the same rejection. A new attempt needs a new key |
| Unexpected error / rollback | Nothing is stored, so the key is free and a retry runs normally |

The browser creates one key per payment form (`crypto.randomUUID()`) and offers a "send the same request
again" button to demonstrate this.

## Failure simulation

`FAILURE_POINTS` in `payment_service.py`: after the INSERT, after the debit, after both balance updates,
and just before COMMIT. Hooks run only when `create_payment()` gets a `failure_point`. Two layers stop
that from happening by accident:

- the public `POST /api/payments` model forbids extra fields, so it cannot carry a failure point;
- `check_failure_point()` refuses any failure point unless `ENABLE_FAILURE_SIMULATION=true` (default false).

At the chosen point the failing transaction reads its own uncommitted state (balances, ledger count,
status) and raises. The service rolls back, then writes a `SIMULATED_FAILURE_ROLLED_BACK` audit row
in a separate short transaction. `TRANSACTION_ID` stays NULL there (the payment row no longer exists and
it is a foreign key); `RECORD_ID` keeps the rolled-back ID. The page shows the database before, inside
the transaction, and after the rollback, plus checks that nothing survived.

## Reconciliation

Matching key: `EXTERNAL_TRANSACTIONS.INTERNAL_TRANSACTION_REF = PAYMENT_TRANSACTIONS.TRANSACTION_REF`.
A batch covers one gateway and one settlement date and runs in one transaction:

| Situation | Result | `DISCREPANCY_AMOUNT` |
|---|---|---|
| Matched, amounts and mapped status equal | matched | – |
| Matched, amounts differ | `AMOUNT_MISMATCH` | reported − internal |
| Matched, status differs (`SUCCESS↔SUCCESS`, `FAILED↔FAILURE`, `REVERSED↔REFUNDED`, in-flight↔`PENDING`) | `STATUS_MISMATCH` | internal amount |
| Gateway record with no internal payment (or one on another gateway) | `MISSING_IN_INTERNAL` | reported amount |
| SUCCESS/REVERSED payment of that day never reported by the gateway | `MISSING_IN_GATEWAY` | internal amount |
| Second gateway record for the same payment | duplicate (stored only after migration 001) | reported amount |

Repeatability: the gateway row is locked during a run, so two runs for one gateway cannot overlap.
A discrepancy already stored by an earlier batch (same type, internal ID and external ID) is not stored
again; the run lists it under "already recorded". Reconciliation never updates balances or resolves
exceptions. Review decisions (`MANUALLY_RESOLVED`, `WRITTEN_OFF`) are made by a person and audited.

Simulated gateway files (`POST /api/reconciliation/simulate-external`) are built from that day's internal
payments, with chosen anomalies injected. Every record's `EXTERNAL_REFERENCE_ID` starts with `SIM-`.

## Audit mapping

`ACTION_TYPE` only allows INSERT, UPDATE, DELETE and SECURITY, so the event name is stored in `DETAILS`
as JSON, e.g. `{"event":"PAYMENT_SUCCESS","amount":"250.00",...}`.

| Event | TABLE_NAME / ACTION_TYPE |
|---|---|
| PAYMENT_SUCCESS, PAYMENT_REJECTED | PAYMENT_TRANSACTIONS / INSERT |
| BALANCE_DEBITED, BALANCE_CREDITED | BANK_ACCOUNTS / UPDATE |
| DUPLICATE_REQUEST_REPLAYED, IDEMPOTENCY_KEY_CONFLICT, SIMULATED_FAILURE_ROLLED_BACK, PAYMENT_ROLLED_BACK | PAYMENT_TRANSACTIONS / SECURITY |
| SIMULATED_EXTERNAL_RECORDS_CREATED, SIMULATED_EXTERNAL_RECORD_ADDED | EXTERNAL_TRANSACTIONS / INSERT |
| RECONCILIATION_BATCH_COMPLETED, RECONCILIATION_BATCH_FAILED | RECONCILIATION_BATCHES / INSERT |
| RECONCILIATION_EXCEPTION_REVIEWED | RECONCILIATION_EXCEPTIONS / UPDATE |

Keys that look secret (password, secret, token, dsn, credential, webhook) are removed before writing.

## Money

`NUMBER(15,2)` columns are fetched as `decimal.Decimal`, every calculation uses `Decimal` with half-up
rounding to paise, and amounts are sent to the browser as strings (`"1500.00"`), never as floats.

## Error responses

All errors have the shape `{"error": {"code", "message", "details"}}`: 422 validation or business
rejection, 404 not found, 409 conflict/busy, 403 failure simulation disabled, 503 database not
configured or unreachable, 500 unexpected (with a reference to the server log; no stack trace is sent).
