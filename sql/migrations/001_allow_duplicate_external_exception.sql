-- Migration 001 (OPTIONAL - NEEDS THE TEAM'S APPROVAL BEFORE IT IS RUN)
--
-- Why: RECONCILIATION_EXCEPTIONS.EXCEPTION_TYPE is limited by CHK_EXCEPTION_TYPE to
-- AMOUNT_MISMATCH, STATUS_MISMATCH, MISSING_IN_GATEWAY and MISSING_IN_INTERNAL.
-- A duplicate gateway settlement (two gateway records for one payment) fits none
-- of them. Without this migration the app detects duplicates, shows them and
-- writes them to AUDIT_LOGS, but does not store them as exception rows.
--
-- What it does: replaces the CHECK constraint with one that also allows
-- 'DUPLICATE_EXTERNAL'. No data is changed or deleted; existing rows already
-- satisfy the wider rule. The app detects the new rule automatically.
--
-- Impact: a CHECK constraint is briefly dropped and re-created; run it when no
-- reconciliation batch is running.

ALTER TABLE RECONCILIATION_EXCEPTIONS DROP CONSTRAINT chk_exception_type;

ALTER TABLE RECONCILIATION_EXCEPTIONS ADD CONSTRAINT chk_exception_type CHECK (exception_type IN
  ('AMOUNT_MISMATCH','STATUS_MISMATCH','MISSING_IN_GATEWAY','MISSING_IN_INTERNAL','DUPLICATE_EXTERNAL'));

-- Rollback (only if no DUPLICATE_EXTERNAL rows exist yet):
-- ALTER TABLE RECONCILIATION_EXCEPTIONS DROP CONSTRAINT chk_exception_type;
-- ALTER TABLE RECONCILIATION_EXCEPTIONS ADD CONSTRAINT chk_exception_type CHECK (exception_type IN
--   ('AMOUNT_MISMATCH','STATUS_MISMATCH','MISSING_IN_GATEWAY','MISSING_IN_INTERNAL'));
