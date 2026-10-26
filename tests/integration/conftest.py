"""Fixtures for the Oracle integration tests.

These tests talk to the real database in .env and only run when you ask:
    $env:RUN_ORACLE_INTEGRATION = "1"; python -m pytest tests/integration -v

They create their own clearly labelled rows (emails @it.invalid, account
numbers starting IT, gateway names starting IT-GW-) and never touch existing
rows. The rows are left in place afterwards, like any audited financial
record; filter them out by those prefixes.
"""

import os
import random
import uuid
from decimal import Decimal

import pytest

import config
from database.connection import close_pool, connection, fetch_one, ping

SQL_NEW_USER = """
INSERT INTO USERS (full_name, email, phone_number, kyc_status)
VALUES (:full_name, :email, :phone_number, 'VERIFIED')
RETURNING user_id INTO :user_id
"""

SQL_NEW_ACCOUNT = """
INSERT INTO BANK_ACCOUNTS (user_id, account_number, bank_name, ifsc_code, account_type, current_balance, currency, account_status)
VALUES (:user_id, :account_number, 'Integration Test Bank', 'ITST0000001', 'SAVINGS', :balance, 'INR', :status)
RETURNING account_id INTO :account_id
"""

SQL_NEW_PROFILE = """
INSERT INTO PAYMENT_PROFILES (user_id, preferred_gateway_id, daily_limit, single_tx_limit, two_factor_auth, profile_status)
VALUES (:user_id, NULL, :daily_limit, :single_tx_limit, 'N', 'ACTIVE')
"""

SQL_NEW_GATEWAY = """
INSERT INTO PAYMENT_GATEWAYS (gateway_name, api_endpoint, settlement_currency, fee_percentage, gateway_status)
VALUES (:gateway_name, 'https://gateway.example.invalid/integration-test', 'INR', 1.00, 'ACTIVE')
RETURNING gateway_id INTO :gateway_id
"""

SQL_BALANCE = "SELECT current_balance FROM BANK_ACCOUNTS WHERE account_id = :account_id"


def pytest_collection_modifyitems(items):
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.oracle)


@pytest.fixture(scope="session")
def oracle():
    if os.environ.get("RUN_ORACLE_INTEGRATION") != "1":
        pytest.skip("NOT EXECUTED: set RUN_ORACLE_INTEGRATION=1 to run tests against the real Oracle database")
    os.environ.pop("PRC_SKIP_DOTENV", None)
    config.get_settings.cache_clear()
    settings = config.get_settings()
    if not settings.is_db_configured:
        pytest.skip(f"NOT EXECUTED: Oracle settings missing in .env: {', '.join(settings.missing_db_settings)}")
    ping()
    yield settings
    close_pool()


@pytest.fixture(scope="session")
def run_tag():
    return uuid.uuid4().hex[:8].upper()


@pytest.fixture
def make_account(oracle, run_tag):
    """Create a labelled test user + account (+ generous profile) and return the account id."""
    counter = {"n": 0}

    def create(balance="1000.00", status="ACTIVE", daily_limit="10000000", single_tx_limit="1000000", profile=True):
        counter["n"] += 1
        suffix = f"{run_tag}{uuid.uuid4().hex[:6].upper()}"
        with connection() as conn:
            cur = conn.cursor()
            user_var, account_var = cur.var(int), cur.var(int)
            cur.execute(SQL_NEW_USER, {
                "full_name": f"IT {run_tag} user {counter['n']}",
                "email": f"it-{suffix.lower()}@it.invalid",
                "phone_number": "+99" + "".join(random.choice("0123456789") for _ in range(11)),
                "user_id": user_var,
            })
            user_id = int(user_var.getvalue()[0])
            cur.execute(SQL_NEW_ACCOUNT, {
                "user_id": user_id, "account_number": f"IT{suffix}"[:20], "balance": Decimal(balance),
                "status": status, "account_id": account_var,
            })
            if profile:
                cur.execute(SQL_NEW_PROFILE, {"user_id": user_id, "daily_limit": Decimal(daily_limit),
                                              "single_tx_limit": Decimal(single_tx_limit)})
            conn.commit()
            return int(account_var.getvalue()[0])

    return create


@pytest.fixture
def make_gateway(oracle, run_tag):
    def create():
        with connection() as conn:
            cur = conn.cursor()
            var = cur.var(int)
            cur.execute(SQL_NEW_GATEWAY, {"gateway_name": f"IT-GW-{run_tag}-{uuid.uuid4().hex[:6].upper()}", "gateway_id": var})
            conn.commit()
            return int(var.getvalue()[0])

    return create


@pytest.fixture
def balance(oracle):
    def read(account_id):
        with connection() as conn:
            return fetch_one(conn.cursor(), SQL_BALANCE, {"account_id": account_id})["current_balance"]

    return read


@pytest.fixture
def key():
    return lambda prefix="IT": f"{prefix}-{uuid.uuid4().hex.upper()}"
