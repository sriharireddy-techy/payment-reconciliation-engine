# Testing

## How to run

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest                                   # unit tests; Oracle tests are reported as skipped
$env:RUN_ORACLE_INTEGRATION = "1"
python -m pytest tests/integration -v              # real Oracle (needs a filled-in .env)
Remove-Item Env:RUN_ORACLE_INTEGRATION
```

Unit tests blank every Oracle setting first, so they can never reach a database, even with a filled-in
`.env`. Integration tests run only when `RUN_ORACLE_INTEGRATION=1` is set and the connection settings
exist. Otherwise each one is reported as `SKIPPED ... NOT EXECUTED`, never as passed.

## What is covered

| File | Kind | Covers |
|---|---|---|
| `test_config.py` | unit | env parsing, missing settings, invalid values, password hidden from `repr` |
| `test_connection.py` | unit (fake pool) | not-configured error, commit/rollback/release, ORA→API error mapping, no driver text leaked |
| `test_health.py` | unit | startup, health endpoints, required endpoints exist, frontend API calls and links match real routes |
| `test_payments.py` | unit | validation (amount, decimals, accounts, key, extra fields), every business rule, fee rounding, Decimal maths, state transitions, idempotency comparison, API status codes |
| `test_ledger.py` | unit | double-entry rules: unbalanced, missing entry, wrong account, amount mismatch |
| `test_concurrency.py` | unit | consistency checks catch overspending, double debit, lost update, missing ledger rows, API/DB mismatch; request limits; no Python lock |
| `test_failure_simulation.py` | unit | hooks disabled by default (403), guard logic, checkpoint fires only at its point, public route has no hook |
| `test_reconciliation.py` | unit | match, amount/status mismatch, missing internal/gateway, other gateway, duplicates, repeat runs, simulated-file generator end to end |
| `test_audit.py` | unit | secrets removed, 4000-byte limit, allowed action types, filter validation |
| `test_security.py` | static | SQL only from constants with binds, `.env` untracked and ignored, no credentials or `eval` in code |
| `test_sql_schema.py` | static | all 71 SQL statements parsed (Oracle dialect) and checked against the real column list; no `WEBHOOK_SECRET`, no unmasked account numbers |
| `integration/test_oracle_payments.py` | Oracle | schema, success, rejection, unknown account, frozen account, limits, replay, key conflict, ledger integrity, all 4 failure points |
| `integration/test_oracle_concurrency.py` | Oracle | 10 parallel payments on 1,000 (exactly 3 succeed), same-key retries, opposite-direction transfers without deadlock |
| `integration/test_oracle_reconciliation.py` | Oracle | generated file → batch finds the 4 injected types + duplicate; re-run stores 0 new; balances untouched; manual review |

Mocks are used only where noted. Unit tests do **not** prove Oracle behaviour; only the integration tests
do that.

`test_sql_schema.py` was itself checked by feeding it misspelt columns in a SELECT, an INSERT, an UPDATE
SET and an UPDATE WHERE. All four were caught.

## Results

Run on 25 Oct 2026, Windows 11, Python 3.14.3, in the project's `.venv`:

```
python -m pytest
200 passed, 18 skipped in 1.03s
```

| Group | Passed | Failed | Not executed |
|---|---|---|---|
| Unit and static tests | 200 | 0 | 0 |
| Oracle integration tests | 0 | 0 | **18** (no connection password yet) |

The app was also started with `uvicorn app:app`. `/`, the static files, `/docs` and `/health` answered
200; database endpoints answered 503 `DB_NOT_CONFIGURED`, as expected without `.env`.

**Not yet verified:** a live FreeSQL connection, the PL/SQL blocks running on Oracle, real concurrent
behaviour and reconciliation on Oracle. These are exactly what the 18 integration tests check.
After filling in `.env`, run them and update this table with the actual output.
