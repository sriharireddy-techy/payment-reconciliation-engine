-- 00_inspect_schema.sql  (READ-ONLY)
-- Run in the FreeSQL worksheet of the account that owns the project tables.
-- It only reads the data dictionary; nothing is changed.

-- 1. Columns of the ten project tables
SELECT table_name, column_id, column_name, data_type, data_length,
       data_precision, data_scale, nullable, data_default
  FROM user_tab_columns
 WHERE table_name IN ('USERS','BANK_ACCOUNTS','PAYMENT_GATEWAYS','PAYMENT_PROFILES',
                      'PAYMENT_TRANSACTIONS','LEDGER_ENTRIES','EXTERNAL_TRANSACTIONS',
                      'RECONCILIATION_BATCHES','RECONCILIATION_EXCEPTIONS','AUDIT_LOGS')
 ORDER BY table_name, column_id;

-- 2. Constraints (PK, FK, UNIQUE, CHECK) with their columns
SELECT c.table_name, c.constraint_name, c.constraint_type,
       LISTAGG(cc.column_name, ', ') WITHIN GROUP (ORDER BY cc.position) AS columns,
       c.search_condition_vc, r.table_name AS references_table
  FROM user_constraints c
  LEFT JOIN user_cons_columns cc ON cc.constraint_name = c.constraint_name
  LEFT JOIN user_constraints r ON r.constraint_name = c.r_constraint_name
 WHERE c.table_name IN ('USERS','BANK_ACCOUNTS','PAYMENT_GATEWAYS','PAYMENT_PROFILES',
                        'PAYMENT_TRANSACTIONS','LEDGER_ENTRIES','EXTERNAL_TRANSACTIONS',
                        'RECONCILIATION_BATCHES','RECONCILIATION_EXCEPTIONS','AUDIT_LOGS')
 GROUP BY c.table_name, c.constraint_name, c.constraint_type, c.search_condition_vc, r.table_name
 ORDER BY c.table_name, c.constraint_type, c.constraint_name;

-- 3. Indexes
SELECT i.table_name, i.index_name, i.uniqueness,
       LISTAGG(ic.column_name, ', ') WITHIN GROUP (ORDER BY ic.column_position) AS columns
  FROM user_indexes i
  JOIN user_ind_columns ic ON ic.index_name = i.index_name
 WHERE i.table_name IN ('PAYMENT_TRANSACTIONS','LEDGER_ENTRIES','EXTERNAL_TRANSACTIONS',
                        'RECONCILIATION_EXCEPTIONS','BANK_ACCOUNTS','AUDIT_LOGS')
 GROUP BY i.table_name, i.index_name, i.uniqueness
 ORDER BY i.table_name, i.index_name;

-- 4. Row counts (to tell existing data apart from demo data later)
SELECT 'USERS' AS table_name, COUNT(*) AS row_count FROM USERS
UNION ALL SELECT 'BANK_ACCOUNTS', COUNT(*) FROM BANK_ACCOUNTS
UNION ALL SELECT 'PAYMENT_GATEWAYS', COUNT(*) FROM PAYMENT_GATEWAYS
UNION ALL SELECT 'PAYMENT_PROFILES', COUNT(*) FROM PAYMENT_PROFILES
UNION ALL SELECT 'PAYMENT_TRANSACTIONS', COUNT(*) FROM PAYMENT_TRANSACTIONS
UNION ALL SELECT 'LEDGER_ENTRIES', COUNT(*) FROM LEDGER_ENTRIES
UNION ALL SELECT 'EXTERNAL_TRANSACTIONS', COUNT(*) FROM EXTERNAL_TRANSACTIONS
UNION ALL SELECT 'RECONCILIATION_BATCHES', COUNT(*) FROM RECONCILIATION_BATCHES
UNION ALL SELECT 'RECONCILIATION_EXCEPTIONS', COUNT(*) FROM RECONCILIATION_EXCEPTIONS
UNION ALL SELECT 'AUDIT_LOGS', COUNT(*) FROM AUDIT_LOGS;
