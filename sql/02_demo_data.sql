-- 02_demo_data.sql  (OPT-IN DEMONSTRATION DATA)
--
-- Adds a small, clearly labelled set of demo users, accounts, gateways and
-- payment profiles for the faculty demonstration. Nothing is deleted or
-- updated: every INSERT is skipped when its row already exists, so the script
-- can be run again safely.
--
-- How to recognise demo rows later:
--   * USERS.email ends with @demo.invalid (".invalid" is a reserved, unusable domain)
--   * BANK_ACCOUNTS.account_number starts with DEMO
--   * PAYMENT_GATEWAYS.gateway_name starts with DEMO- and api_endpoint uses example.invalid
--
-- Opening balances are written directly (they are starting positions, not
-- payments, so they have no ledger entries). Every later money movement goes
-- through the application and is double-entry recorded. To top an account up
-- again, make a payment from "Demo Treasury" in the app; never edit balances by hand.
--
-- Run in the FreeSQL worksheet with "Run Script" (F5), then COMMIT is at the end.

-- Gateways -------------------------------------------------------------------
INSERT INTO PAYMENT_GATEWAYS (gateway_name, api_endpoint, settlement_currency, fee_percentage, gateway_status)
SELECT 'DEMO-UPI-SIM', 'https://gateway.example.invalid/upi', 'INR', 0.50, 'ACTIVE' FROM DUAL
 WHERE NOT EXISTS (SELECT 1 FROM PAYMENT_GATEWAYS WHERE gateway_name = 'DEMO-UPI-SIM');

INSERT INTO PAYMENT_GATEWAYS (gateway_name, api_endpoint, settlement_currency, fee_percentage, gateway_status)
SELECT 'DEMO-CARD-SIM', 'https://gateway.example.invalid/card', 'INR', 1.80, 'ACTIVE' FROM DUAL
 WHERE NOT EXISTS (SELECT 1 FROM PAYMENT_GATEWAYS WHERE gateway_name = 'DEMO-CARD-SIM');

-- Users ----------------------------------------------------------------------
INSERT INTO USERS (full_name, email, phone_number, kyc_status)
SELECT 'Demo Treasury', 'treasury@demo.invalid', '+910000000001', 'VERIFIED' FROM DUAL
 WHERE NOT EXISTS (SELECT 1 FROM USERS WHERE email = 'treasury@demo.invalid');

INSERT INTO USERS (full_name, email, phone_number, kyc_status)
SELECT 'Demo Concurrency Payer', 'payer@demo.invalid', '+910000000002', 'VERIFIED' FROM DUAL
 WHERE NOT EXISTS (SELECT 1 FROM USERS WHERE email = 'payer@demo.invalid');

INSERT INTO USERS (full_name, email, phone_number, kyc_status)
SELECT 'Demo Merchant', 'merchant@demo.invalid', '+910000000003', 'VERIFIED' FROM DUAL
 WHERE NOT EXISTS (SELECT 1 FROM USERS WHERE email = 'merchant@demo.invalid');

INSERT INTO USERS (full_name, email, phone_number, kyc_status)
SELECT 'Demo Customer', 'customer@demo.invalid', '+910000000004', 'VERIFIED' FROM DUAL
 WHERE NOT EXISTS (SELECT 1 FROM USERS WHERE email = 'customer@demo.invalid');

INSERT INTO USERS (full_name, email, phone_number, kyc_status)
SELECT 'Demo Frozen Holder', 'frozen@demo.invalid', '+910000000005', 'PENDING' FROM DUAL
 WHERE NOT EXISTS (SELECT 1 FROM USERS WHERE email = 'frozen@demo.invalid');

-- Accounts (opening balances) --------------------------------------------------
INSERT INTO BANK_ACCOUNTS (user_id, account_number, bank_name, ifsc_code, account_type, current_balance, currency, account_status)
SELECT u.user_id, 'DEMO0000000001', 'Demo Bank (simulated)', 'DEMO0000001', 'CURRENT', 1000000, 'INR', 'ACTIVE'
  FROM USERS u WHERE u.email = 'treasury@demo.invalid'
   AND NOT EXISTS (SELECT 1 FROM BANK_ACCOUNTS WHERE account_number = 'DEMO0000000001');

INSERT INTO BANK_ACCOUNTS (user_id, account_number, bank_name, ifsc_code, account_type, current_balance, currency, account_status)
SELECT u.user_id, 'DEMO0000000002', 'Demo Bank (simulated)', 'DEMO0000001', 'SAVINGS', 1000, 'INR', 'ACTIVE'
  FROM USERS u WHERE u.email = 'payer@demo.invalid'
   AND NOT EXISTS (SELECT 1 FROM BANK_ACCOUNTS WHERE account_number = 'DEMO0000000002');

INSERT INTO BANK_ACCOUNTS (user_id, account_number, bank_name, ifsc_code, account_type, current_balance, currency, account_status)
SELECT u.user_id, 'DEMO0000000003', 'Demo Bank (simulated)', 'DEMO0000002', 'CURRENT', 5000, 'INR', 'ACTIVE'
  FROM USERS u WHERE u.email = 'merchant@demo.invalid'
   AND NOT EXISTS (SELECT 1 FROM BANK_ACCOUNTS WHERE account_number = 'DEMO0000000003');

INSERT INTO BANK_ACCOUNTS (user_id, account_number, bank_name, ifsc_code, account_type, current_balance, currency, account_status)
SELECT u.user_id, 'DEMO0000000004', 'Demo Bank (simulated)', 'DEMO0000002', 'SAVINGS', 25000, 'INR', 'ACTIVE'
  FROM USERS u WHERE u.email = 'customer@demo.invalid'
   AND NOT EXISTS (SELECT 1 FROM BANK_ACCOUNTS WHERE account_number = 'DEMO0000000004');

INSERT INTO BANK_ACCOUNTS (user_id, account_number, bank_name, ifsc_code, account_type, current_balance, currency, account_status)
SELECT u.user_id, 'DEMO0000000005', 'Demo Bank (simulated)', 'DEMO0000002', 'SAVINGS', 2000, 'INR', 'FROZEN'
  FROM USERS u WHERE u.email = 'frozen@demo.invalid'
   AND NOT EXISTS (SELECT 1 FROM BANK_ACCOUNTS WHERE account_number = 'DEMO0000000005');

-- Payment profiles (limits) ------------------------------------------------------
INSERT INTO PAYMENT_PROFILES (user_id, preferred_gateway_id, daily_limit, single_tx_limit, two_factor_auth, profile_status)
SELECT u.user_id, g.gateway_id, 10000000, 1000000, 'Y', 'ACTIVE'
  FROM USERS u CROSS JOIN PAYMENT_GATEWAYS g
 WHERE u.email = 'treasury@demo.invalid' AND g.gateway_name = 'DEMO-UPI-SIM'
   AND NOT EXISTS (SELECT 1 FROM PAYMENT_PROFILES p WHERE p.user_id = u.user_id);

INSERT INTO PAYMENT_PROFILES (user_id, preferred_gateway_id, daily_limit, single_tx_limit, two_factor_auth, profile_status)
SELECT u.user_id, g.gateway_id, 100000, 10000, 'Y', 'ACTIVE'
  FROM USERS u CROSS JOIN PAYMENT_GATEWAYS g
 WHERE u.email = 'payer@demo.invalid' AND g.gateway_name = 'DEMO-UPI-SIM'
   AND NOT EXISTS (SELECT 1 FROM PAYMENT_PROFILES p WHERE p.user_id = u.user_id);

INSERT INTO PAYMENT_PROFILES (user_id, preferred_gateway_id, daily_limit, single_tx_limit, two_factor_auth, profile_status)
SELECT u.user_id, g.gateway_id, 50000, 10000, 'Y', 'ACTIVE'
  FROM USERS u CROSS JOIN PAYMENT_GATEWAYS g
 WHERE u.email = 'merchant@demo.invalid' AND g.gateway_name = 'DEMO-UPI-SIM'
   AND NOT EXISTS (SELECT 1 FROM PAYMENT_PROFILES p WHERE p.user_id = u.user_id);

-- The customer has deliberately low limits, to demonstrate limit rejections.
INSERT INTO PAYMENT_PROFILES (user_id, preferred_gateway_id, daily_limit, single_tx_limit, two_factor_auth, profile_status)
SELECT u.user_id, g.gateway_id, 5000, 2000, 'Y', 'ACTIVE'
  FROM USERS u CROSS JOIN PAYMENT_GATEWAYS g
 WHERE u.email = 'customer@demo.invalid' AND g.gateway_name = 'DEMO-CARD-SIM'
   AND NOT EXISTS (SELECT 1 FROM PAYMENT_PROFILES p WHERE p.user_id = u.user_id);

COMMIT;

-- Check what was created
SELECT a.account_id, u.full_name, a.account_number, a.current_balance, a.account_status
  FROM BANK_ACCOUNTS a JOIN USERS u ON u.user_id = a.user_id
 WHERE a.account_number LIKE 'DEMO%'
 ORDER BY a.account_number;
