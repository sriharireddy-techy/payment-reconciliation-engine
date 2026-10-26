"""Shared test fixtures.

Unit tests run with every Oracle setting blanked out, so they can never touch a
real database even when the developer's .env is filled in. Tests marked
`oracle` (tests/integration) are left alone and use the real .env.
"""

import pytest

import config
from database import connection as db

ORACLE_VARS = ("ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_HOST", "ORACLE_PORT", "ORACLE_SERVICE_NAME", "ORACLE_DSN")


@pytest.fixture(autouse=True)
def isolated_settings(request, monkeypatch):
    if request.node.get_closest_marker("oracle"):
        yield
        return
    for name in ORACLE_VARS:
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "false")
    monkeypatch.setenv("PRC_SKIP_DOTENV", "1")
    config.get_settings.cache_clear()
    monkeypatch.setattr(db, "_pool", None)
    yield
    config.get_settings.cache_clear()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app import app

    return TestClient(app)


@pytest.fixture
def enable_failure_simulation(monkeypatch):
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


class FakeConnection:
    """Records commit/rollback/close calls made by the connection helpers."""

    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.call_timeout = None

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakePool:
    def __init__(self):
        self.connections = []

    def acquire(self):
        conn = FakeConnection()
        self.connections.append(conn)
        return conn


@pytest.fixture
def fake_pool(monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(db, "_pool", pool)
    return pool
