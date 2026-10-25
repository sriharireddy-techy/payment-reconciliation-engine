# Concurrent Payment Transaction and Financial Reconciliation Database

An academic DBMS project (BCSE302L case study) that shows how an Oracle database keeps a payment system
correct when payments run at the same time, fail half-way, are retried, or disagree with a payment
gateway's records.

It is a **prototype**: no real money moves, no bank or UPI system is contacted, and every gateway
settlement record is simulated and labelled `SIM-`.

| Area | What the project demonstrates |
|---|---|
| Atomic payments | Payment row, both balance updates, DR/CR ledger rows and audit rows commit together or not at all |
| Double-entry ledger | Every successful payment has one debit and one equal credit; an integrity check proves DR = CR |
| Concurrency | N real simultaneous requests on one account; row locks + guarded UPDATE + CHECK constraint prevent overspending |
| Idempotency | `PAYMENT_TRANSACTIONS.IDEMPOTENCY_KEY` (UNIQUE) makes a retried request return the original result |
| Failure & rollback | A failure is injected at a chosen step before COMMIT; the database is shown before / inside / after |
| Reconciliation | Simulated gateway file vs internal payments: amount, status, missing and duplicate records |
| Audit | Important events written to `AUDIT_LOGS` with a JSON description, never with secrets |

## Technology

FastAPI (Python) &middot; python-oracledb in **Thin mode** (no Oracle Client or local database needed) &middot;
Oracle FreeSQL (26ai) &middot; HTML, Bootstrap 5, vanilla JavaScript, Chart.js &middot; pytest.

## Quick start (Windows PowerShell)

```powershell
cd payment-reconciliation-engine
py -m venv .venv                      # or: python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process RemoteSigned
python -m pip install -r requirements-dev.txt

Copy-Item .env.example .env
notepad .env                          # fill in the FreeSQL connection details (see below)

python -m pytest                      # unit tests (no database needed)
uvicorn app:app --reload              # start the app
```

- Dashboard: <http://127.0.0.1:8000/>
- Swagger API docs: <http://127.0.0.1:8000/docs>

The app starts even without `.env`; every page then explains that the database is not configured,
and **Database health** lists what is missing.

## Connecting to Oracle FreeSQL

FreeSQL schemas accept direct connections from local tools (announced by Oracle in November 2025).
python-oracledb is one of the supported clients, using an EZCONNECT-style address.

1. Sign in at <https://freesql.com> with **the account that owns the 10 project tables**.
2. Open **Schema Connection Details**. Copy the hostname, port, service name and username, and generate a
   connection password there. This password is separate from your Oracle account password.
3. Put them in `.env`:

   ```ini
   ORACLE_USER=...
   ORACLE_PASSWORD=...
   ORACLE_HOST=...
   ORACLE_PORT=1521
   ORACLE_SERVICE_NAME=...
   ```

   If FreeSQL shows a full connect string instead, put it in `ORACLE_DSN` and leave host/port/service empty.
4. Restart `uvicorn` and open **Database health**. It runs a read-only query and compares
   `USER_TAB_COLUMNS` / `USER_CONSTRAINTS` with the columns the application uses.

`.env` is in `.gitignore`. Never commit it, and regenerate the FreeSQL password if it is ever shared.

**Not yet verified:** the live connection has not been tested, because the connection password was not
available while the app was built. The SQL was checked against the real column list exported from the
FreeSQL schema (see *Testing*), but not executed on FreeSQL. FreeSQL's session limits are also unknown;
if the concurrency lab reports connection errors, lower `DB_POOL_MAX` in `.env`.

## Database schema (unchanged)

The application uses the team's existing ten tables exactly as they are: `USERS`, `BANK_ACCOUNTS`,
`PAYMENT_GATEWAYS`, `PAYMENT_PROFILES`, `PAYMENT_TRANSACTIONS`, `LEDGER_ENTRIES`, `EXTERNAL_TRANSACTIONS`,
`RECONCILIATION_BATCHES`, `RECONCILIATION_EXCEPTIONS` and `AUDIT_LOGS`. Their DDL is kept for reference in
[`sql/01_schema_reference.sql`](sql/01_schema_reference.sql). The app never creates, alters, drops or
truncates anything, and never deletes rows.

| File | Purpose | Changes data? |
|---|---|---|
| `sql/00_inspect_schema.sql` | Columns, constraints, indexes, row counts | No (read-only) |
| `sql/01_schema_reference.sql` | Team's Review 1 DDL, for reference | Do not run on the existing schema |
| `sql/02_demo_data.sql` | **Opt-in** demo users, accounts, gateways, profiles (`@demo.invalid`, `DEMO...`) | Inserts only, skips existing rows |
| `sql/03_analysis_queries.sql` | Ledger, concurrency and reconciliation checks in plain SQL, for the viva | No (read-only) |
| `sql/migrations/001_...sql` | Allow a `DUPLICATE_EXTERNAL` exception type | **Needs team approval** |
| `sql/migrations/002_...sql` | Indexes on foreign-key columns | **Needs team approval** |

Neither migration is required. Without 001, duplicate gateway records are detected, shown and audited,
but not stored as exception rows, because `CHK_EXCEPTION_TYPE` only allows four types.

## Project structure

```
app.py                  FastAPI app: routers, error handlers, static files
config.py               Settings from environment / .env
database/
  connection.py         Connection pool (Thin mode), transaction helpers, query helpers
  errors.py             ORA-/DPY- errors -> safe API errors (no SQL or credentials leaked)
  schema.py             Expected columns + live schema verification
models/schemas.py       Pydantic request models (all input validation)
routes/                 One router per feature (health, dashboard, accounts, payments,
                        transactions & ledger, concurrency, failure simulation, reconciliation, audit)
services/
  payment_service.py    The atomic payment transaction (locking, idempotency, ledger, audit)
  payment_rules.py      Pure business rules, fee and state-transition logic
  ledger_service.py     Ledger views and the double-entry integrity check
  transaction_service.py  History search and transaction details
  concurrency_service.py  Concurrency lab + consistency checks
  failure_service.py    Failure-injection demo and rollback verification
  reconciliation_service.py  Simulated gateway records, matching, batches, exceptions
  audit_service.py      AUDIT_LOGS writer and viewer
  dashboard_service.py  Live KPIs
  account_service.py    Accounts and gateways (masked numbers, no secrets)
  serialization.py      Exact money formatting (Decimal -> "1500.00")
  errors.py             Application error types
templates/index.html    Single-page dashboard shell
static/                 CSS and JavaScript (api.js, ui.js, charts.js, app.js, pages/*.js)
sql/                    Inspection, reference DDL, demo data, analysis queries, migrations
tests/                  Unit tests; tests/integration = real-Oracle tests (opt-in)
docs/                   Architecture, API, testing evidence, demonstration guide
```

## Testing

```powershell
python -m pytest                                   # 200 unit tests, no database needed
$env:RUN_ORACLE_INTEGRATION = "1"; python -m pytest tests/integration -v   # needs .env
```

The unit tests cover validation, business rules, money arithmetic, error translation, transaction
handling, reconciliation matching, the concurrency consistency checks, security properties of the code,
and a check that every SQL statement uses only columns that exist in the schema.

The integration tests run real payments, real concurrent requests, every failure point and a full
reconciliation on Oracle. They have **not been executed yet** (no connection password). They write
clearly labelled rows (`@it.invalid`, `IT...`, `IT-GW-...`) that stay in the database afterwards.
Details and the latest results: [docs/testing.md](docs/testing.md).

## More documentation

- [docs/architecture.md](docs/architecture.md): how each guarantee is implemented and why
- [docs/api.md](docs/api.md): endpoints and example payloads
- [docs/demo-guide.md](docs/demo-guide.md): step-by-step faculty demonstration
- [docs/testing.md](docs/testing.md): what is tested, how, and the results

## Limitations (honest list)

- Prototype only: no authentication, users or roles in the web app. Anyone who can open the page can make payments.
- No currency conversion: sender and receiver must hold the same currency.
- Gateway fees are recorded on the payment but not moved between accounts, because the schema has no fee or revenue account.
- Opening balances in the demo data are starting positions without ledger entries; only movements made through the app are double-entry recorded.
- Reversal (`REVERSED`) is understood by the ledger check and reconciliation, but the app does not create reversals.
- The daily limit counts successful payments since midnight in the database server's time zone.
- `AUDIT_LOGS.PERFORMED_BY` is the database user, since the prototype has no application login. The acting component is written into `DETAILS`.
- Ledger and audit tables are append-only by convention in this app. Enforcing it in the database (privileges or triggers) is a schema change the team has not approved.
- Timestamps are shown as stored, in database server time.
- Not a production banking system, and it makes no claim of regulatory compliance.
