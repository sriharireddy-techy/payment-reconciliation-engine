-- Migration 002 (OPTIONAL - NEEDS THE TEAM'S APPROVAL BEFORE IT IS RUN)
--
-- Why: Oracle creates indexes for PRIMARY KEY and UNIQUE constraints, but not
-- for FOREIGN KEY columns. The application looks up ledger entries by
-- transaction, payments by account and gateway records by internal reference.
-- With a few hundred demo rows this makes no visible difference; with larger
-- data these indexes avoid full table scans.
--
-- What it does: only CREATE INDEX. No table, column, constraint or row changes.
-- The app works the same with or without these indexes.
--
-- Rollback: DROP INDEX <name>; for each index below.

CREATE INDEX ix_ledger_transaction ON LEDGER_ENTRIES (transaction_id);
CREATE INDEX ix_ledger_account ON LEDGER_ENTRIES (account_id);
CREATE INDEX ix_txn_sender ON PAYMENT_TRANSACTIONS (sender_account_id);
CREATE INDEX ix_txn_receiver ON PAYMENT_TRANSACTIONS (receiver_account_id);
CREATE INDEX ix_txn_gateway_initiated ON PAYMENT_TRANSACTIONS (gateway_id, initiated_at);
CREATE INDEX ix_external_internal_ref ON EXTERNAL_TRANSACTIONS (internal_transaction_ref);
CREATE INDEX ix_exception_batch ON RECONCILIATION_EXCEPTIONS (batch_id);
CREATE INDEX ix_audit_transaction ON AUDIT_LOGS (transaction_id);
