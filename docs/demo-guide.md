# Faculty demonstration guide

About 15 minutes. Every step uses the web dashboard; each one also has a SQL query in
`sql/03_analysis_queries.sql` that can be run in FreeSQL to show the same result at database level.

## Before the demo (once)

1. `.env` is filled in and `uvicorn app:app --reload` is running. Open <http://127.0.0.1:8000/>.
2. **Database health** shows *Oracle connected*, all 10 tables ✓ and the constraints ✓.
3. If the schema has no suitable accounts yet, run `sql/02_demo_data.sql` in FreeSQL ("Run Script").
   It adds clearly labelled demo rows only (`@demo.invalid`, `DEMO...`, `DEMO-...-SIM`).
4. For step 5, set `ENABLE_FAILURE_SIMULATION=true` in `.env` and restart. Set it back to `false` afterwards.

## 1. Successful payment

**New payment** → From *Demo Customer*, To *Demo Merchant*, amount `500.00`, gateway *DEMO-CARD-SIM* →
**Review & pay** → confirm.

Point out the outcome card: status SUCCESS, the fee (1.80% of 500 = 9.00), the transaction reference and
the idempotency key. All of these were written in **one** Oracle transaction.

## 2. Ledger entries

On the same result card, or via the transaction link:

- one **DR** row on the customer's account and one **CR** row on the merchant's, both 500.00. The badge
  shows **DR = CR**;
- **Ledger** page → **Run check**: total debits = total credits for the whole ledger, and every payment
  follows the rule.

SQL: queries 1 and 2 in `03_analysis_queries.sql`.

## 3. Duplicate request protection

On the result card press **Send the same request again (same key)**.
The answer is *Duplicate request*: the same transaction number comes back, and the balance in the
sender drop-down did **not** drop a second time. In the **Audit log**, search `DUPLICATE_REQUEST_REPLAYED`.

To show business-rule rejection: pay `2500.00` from *Demo Customer*. The per-payment limit is 2,000, so it
is rejected with `SINGLE_TX_LIMIT_EXCEEDED`, recorded as FAILED, and no ledger rows are written.

## 4. Concurrent payments

**Concurrency lab** → paying account *Demo Concurrency Payer* (balance 1,000), amount `300.00`,
10 requests, mode *Different payments* → **Run**.

- Expected: exactly **3** succeed and 7 are rejected with INSUFFICIENT_FUNDS. The balance ends at **100.00**,
  never negative.
- The timeline bars overlap: the requests really ran at the same time.
- All consistency checks are green, and they are computed from the database afterwards.
- Explain the right-hand card: row locks in a fixed order, re-read after locking, guarded UPDATE,
  CHECK constraint.
- Run again in mode *Same request retried*: exactly one payment is created and the others are replayed.

To run it again, top the payer up first: **New payment** from *Demo Treasury* to *Demo Concurrency Payer*.

## 5. Failure and rollback

**Failure & rollback** → choose *Fail here: after the sender is debited* → amount `100.00` → **Run**.

Three columns:
- **Before**: committed balances;
- **Inside the failing transaction**: the sender is already debited by 100 (only that transaction can see this);
- **After rollback**: balances, ledger rows and payment rows are exactly as before.

The checks are green, and an audit record `SIMULATED_FAILURE_ROLLED_BACK` was written *after* the
rollback in its own transaction. Try *before COMMIT* too: even with ledger and SUCCESS status written,
nothing survives without COMMIT.

## 6. Reconciliation exceptions

Make 4 or 5 payments through *DEMO-UPI-SIM* today (or use those already made), then go to **Reconciliation**:

1. **Gateway records** → gateway *DEMO-UPI-SIM*, today's date, keep each anomaly count at 1 →
   **Generate simulated records**. The table shows which record was made to produce which discrepancy;
   every reference starts with `SIM-`.
2. **Run reconciliation** → same gateway and date → **Run batch**. It finds AMOUNT_MISMATCH,
   STATUS_MISMATCH, MISSING_IN_GATEWAY, MISSING_IN_INTERNAL and the duplicate settlement. The duplicate
   is reported, not stored, unless migration 001 has been approved.
3. **Run batch again**: 0 new exceptions; the earlier ones are listed as *already recorded*.
4. **Exceptions** tab → **Review** one → *MANUALLY_RESOLVED* with a note. No balance changes, and the
   decision is audited.

SQL: query 6 (`FULL OUTER JOIN`) shows the same classification in a single statement.

## 7. Audit logs

**Audit log** → filter *Action = SECURITY* to see replays and rollbacks; search `PAYMENT_` for payment
events. Expand *show* to see the JSON details. No passwords or secrets ever appear there.
`PERFORMED_BY` is the database user (the column's default).

## Likely questions

- *Why not just lock in Python?* A Python lock only works inside one process; the database lock works
  for every client and server instance, and survives a crash of the web app.
- *Why READ COMMITTED and not SERIALIZABLE?* Every read the decision depends on is made under a row
  lock, so the outcome is already serial. SERIALIZABLE would raise ORA-08177 conflicts that need retries.
- *What if the server crashes after COMMIT but before replying?* The client retries with the same
  idempotency key and receives the stored result.
- *Are the gateway records real?* No. They are simulated and labelled `SIM-`.
