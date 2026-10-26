-- 03_analysis_queries.sql  (READ-ONLY)
--
-- The checks the application performs, written as plain SQL so they can be
-- run and explained directly in the FreeSQL worksheet during the viva.
-- Replace the bind variables (:gateway_id, :batch_date) when prompted, or type literals.

-- 1. Double entry, whole ledger: total debits must equal total credits.
SELECT SUM(CASE WHEN entry_type = 'DR' THEN amount ELSE 0 END) AS total_debits,
       SUM(CASE WHEN entry_type = 'CR' THEN amount ELSE 0 END) AS total_credits
  FROM LEDGER_ENTRIES;

-- 2. Double entry, per payment: list any payment whose entries do not balance
--    or whose entry count does not suit its status.
SELECT t.transaction_id, t.status, t.amount,
       NVL(SUM(CASE WHEN l.entry_type = 'DR' THEN l.amount END), 0) AS dr_total,
       NVL(SUM(CASE WHEN l.entry_type = 'CR' THEN l.amount END), 0) AS cr_total,
       COUNT(l.entry_id) AS entries
  FROM PAYMENT_TRANSACTIONS t
  LEFT JOIN LEDGER_ENTRIES l ON l.transaction_id = t.transaction_id
 GROUP BY t.transaction_id, t.status, t.amount
HAVING NVL(SUM(CASE WHEN l.entry_type = 'DR' THEN l.amount END), 0)
       <> NVL(SUM(CASE WHEN l.entry_type = 'CR' THEN l.amount END), 0)
    OR (t.status = 'SUCCESS' AND COUNT(l.entry_id) <> 2)
    OR (t.status IN ('FAILED', 'INITIATED', 'PROCESSING') AND COUNT(l.entry_id) > 0);

-- 3. No negative balances (CHK_ACCOUNT_BALANCE should make this always empty).
SELECT account_id, current_balance FROM BANK_ACCOUNTS WHERE current_balance < 0;

-- 4. No idempotency key used twice (UQ_IDEMPOTENCY_KEY should make this always empty).
SELECT idempotency_key, COUNT(*) FROM PAYMENT_TRANSACTIONS
 GROUP BY idempotency_key HAVING COUNT(*) > 1;

-- 5. Concurrency lab runs: outcome of each run (keys look like CONC-<run>-<n>).
SELECT REGEXP_SUBSTR(idempotency_key, '^CONC-[^-]+') AS run_id, status,
       COUNT(*) AS payments, SUM(amount) AS total_amount
  FROM PAYMENT_TRANSACTIONS
 WHERE idempotency_key LIKE 'CONC-%'
 GROUP BY REGEXP_SUBSTR(idempotency_key, '^CONC-[^-]+'), status
 ORDER BY run_id, status;

-- 6. Reconciliation in one statement: FULL OUTER JOIN of one day's gateway
--    records with the gateway's internal payments of that day.
SELECT t.transaction_id, e.ext_transaction_id, t.amount, e.reported_amount,
       t.status AS internal_status, e.reported_status,
       CASE
         WHEN t.transaction_id IS NULL THEN 'MISSING_IN_INTERNAL'
         WHEN e.ext_transaction_id IS NULL THEN 'MISSING_IN_GATEWAY'
         WHEN e.reported_amount <> t.amount THEN 'AMOUNT_MISMATCH'
         WHEN DECODE(t.status, 'SUCCESS', 'SUCCESS', 'FAILED', 'FAILURE', 'REVERSED', 'REFUNDED', 'PENDING')
              <> e.reported_status THEN 'STATUS_MISMATCH'
         ELSE 'MATCHED'
       END AS result
  FROM (SELECT * FROM PAYMENT_TRANSACTIONS
         WHERE gateway_id = :gateway_id
           AND initiated_at >= TO_DATE(:batch_date, 'YYYY-MM-DD')
           AND initiated_at <  TO_DATE(:batch_date, 'YYYY-MM-DD') + 1
           AND status IN ('SUCCESS', 'FAILED', 'REVERSED')) t
  FULL OUTER JOIN
       (SELECT * FROM EXTERNAL_TRANSACTIONS
         WHERE gateway_id = :gateway_id
           AND settlement_date >= TO_DATE(:batch_date, 'YYYY-MM-DD')
           AND settlement_date <  TO_DATE(:batch_date, 'YYYY-MM-DD') + 1) e
    ON e.internal_transaction_ref = t.transaction_ref
 ORDER BY result, t.transaction_id;
-- Note: the application additionally treats a FAILED payment that the gateway
-- never reported as normal (only SUCCESS/REVERSED ones count as missing), and
-- flags a second gateway record for the same payment as a duplicate.

-- 7. Duplicate gateway settlements: more than one record for the same payment.
SELECT gateway_id, internal_transaction_ref, COUNT(*) AS records
  FROM EXTERNAL_TRANSACTIONS
 WHERE internal_transaction_ref IS NOT NULL
 GROUP BY gateway_id, internal_transaction_ref
HAVING COUNT(*) > 1;

-- 8. Simulated versus other gateway records.
SELECT CASE WHEN external_reference_id LIKE 'SIM-%' THEN 'SIMULATED' ELSE 'OTHER' END AS origin,
       COUNT(*) AS records
  FROM EXTERNAL_TRANSACTIONS
 GROUP BY CASE WHEN external_reference_id LIKE 'SIM-%' THEN 'SIMULATED' ELSE 'OTHER' END;
