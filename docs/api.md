# API reference

Interactive documentation with request/response schemas: <http://127.0.0.2:8000/docs>.
All errors look like `{"error": {"code": "...", "message": "...", "details": ...}}`.
Amounts are decimal strings with two places.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | App is running (no database access) |
| GET | `/api/health/database` | Connection test + schema/constraint verification (read-only) |
| GET | `/api/dashboard` | KPIs, chart data, recent payments, batches, audit events |
| GET | `/api/accounts` | Accounts with masked numbers, owners, limits |
| GET | `/api/accounts/{id}` | One account, today's spending, ledger totals |
| GET | `/api/gateways` | Payment gateways (no secrets) |
| POST | `/api/payments` | Make a payment (atomic, idempotent) |
| GET | `/api/transactions` | History; filters `status, account_id, gateway_id, search, date_from, date_to, limit, offset` |
| GET | `/api/transactions/{id}` | Details: parties, ledger, audit trail, gateway records, exceptions |
| GET | `/api/transactions/{id}/ledger` | Ledger rows of one payment + DR/CR totals |
| GET | `/api/ledger` | Ledger rows; filters `account_id, transaction_id, entry_type` |
| GET | `/api/ledger/integrity` | Double-entry check over all payments |
| POST | `/api/concurrency/run` | Concurrency lab |
| GET | `/api/failure-simulation` | Enabled? Which failure points exist |
| POST | `/api/failure-simulation/run` | Failing payment + rollback verification (403 unless enabled) |
| POST | `/api/reconciliation/simulate-external` | Generate simulated `SIM-` gateway records |
| POST / GET | `/api/reconciliation/external-records` | Add one simulated record / list records |
| POST / GET | `/api/reconciliation/batches` | Run a batch / list batches |
| GET | `/api/reconciliation/batches/{id}` | Batch with its exceptions |
| GET | `/api/reconciliation/exceptions` | Exceptions; filters `batch_id, gateway_id, exception_type, resolution_status` |
| PATCH | `/api/reconciliation/exceptions/{id}` | Record a review decision |
| GET | `/api/audit-logs` | Audit trail; filters `table_name, action_type, account_id, transaction_id, search` |

## POST /api/payments

```json
{
  "sender_account_id": 2,
  "receiver_account_id": 3,
  "amount": "250.00",
  "idempotency_key": "PAY-3f0c3a5e-2b1d-4a7e-9c55-0d1f6a8b2c41",
  "gateway_id": null,
  "description": "Invoice 17"
}
```

| Status | Meaning |
|---|---|
| 201 | `{"outcome":"SUCCESS","replayed":false,"transaction":{...}}` |
| 200 | Same key and same details as an earlier request: `"replayed": true`, original transaction |
| 422 `VALIDATION_ERROR` | Bad input (amount ≤ 0, more than 2 decimals, same account, bad key, unknown field) |
| 422 `INSUFFICIENT_FUNDS`, `DAILY_LIMIT_EXCEEDED`, `SINGLE_TX_LIMIT_EXCEEDED`, `SENDER_ACCOUNT_NOT_ACTIVE`, `RECEIVER_ACCOUNT_NOT_ACTIVE`, `CURRENCY_MISMATCH`, `GATEWAY_NOT_ACTIVE`, `PROFILE_SUSPENDED` | Rejected by a business rule; recorded as FAILED; `details.transaction` holds the record |
| 404 `ACCOUNT_NOT_FOUND` / `GATEWAY_NOT_FOUND` | Nothing recorded |
| 409 `IDEMPOTENCY_KEY_REUSED` | Key already used for a different payment |
| 409 `RESOURCE_BUSY` / `DEADLOCK` / `CONSISTENCY_GUARD` | Rolled back; safe to retry with the same key |
| 503 `DB_NOT_CONFIGURED` / `DB_UNAVAILABLE` | Database not reachable |

## POST /api/concurrency/run

```json
{"sender_account_id": 2, "receiver_account_id": 3, "amount": "300.00", "num_requests": 10, "mode": "unique_keys"}
```

`mode` is `unique_keys` (different payments, tests overspending) or `same_key` (one key retried, tests
idempotency). The response has counts, balances before and after, `checks` (each `{name, passed, detail}`)
and `requests` with per-request outcome and timing.

## POST /api/failure-simulation/run

```json
{"sender_account_id": 2, "receiver_account_id": 3, "amount": "100.00", "failure_point": "AFTER_DEBIT"}
```

`failure_point` is one of `AFTER_TRANSACTION_INSERT`, `AFTER_DEBIT`, `AFTER_BALANCE_UPDATES` or `BEFORE_COMMIT`.
The response has `before`, `inside_transaction` (uncommitted, read by the failing transaction), `after`,
`audit` and `checks`. It returns 403 unless `ENABLE_FAILURE_SIMULATION=true`.

## POST /api/reconciliation/simulate-external

```json
{"gateway_id": 1, "settlement_date": "2026-10-25", "amount_mismatches": 1, "status_mismatches": 1,
 "missing_in_gateway": 1, "missing_in_internal": 1, "duplicates": 1, "seed": 42}
```

## POST /api/reconciliation/batches

```json
{"gateway_id": 1, "batch_date": "2026-10-25"}
```

The response includes `batch`, `total_records`, `matched_records`, `new_exceptions`,
`previously_recorded`, `duplicates` and `findings_by_type`.
