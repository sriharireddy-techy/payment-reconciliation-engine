"""Database connection layer: missing configuration, commit/rollback, error translation.

These tests use a fake pool; they check the application's own transaction
handling, not Oracle itself (see tests/integration for that).
"""

from types import SimpleNamespace

import oracledb
import pytest

from database import connection as db
from database.errors import constraint_name, error_code, translate_db_error
from services.errors import (
    AppError,
    ConflictError,
    ConstraintViolationError,
    DatabaseNotConfiguredError,
    DatabaseOperationError,
    ResourceBusyError,
    ServiceUnavailableError,
)


def oracle_error(cls, full_code, message):
    return cls(SimpleNamespace(full_code=full_code, code=int(full_code[4:]), message=message))


def test_pool_refused_when_not_configured():
    with pytest.raises(DatabaseNotConfiguredError) as info:
        db.get_pool()
    assert info.value.status_code == 503
    assert "ORACLE_PASSWORD" in info.value.details["missing"]


def test_connection_rolls_back_and_releases_on_success(fake_pool):
    with db.connection() as conn:
        pass
    assert conn.closed and conn.rollbacks == 1 and conn.commits == 0


def test_transaction_commits_on_success(fake_pool):
    with db.transaction() as conn:
        pass
    assert conn.commits == 1 and conn.closed


def test_transaction_rolls_back_on_error(fake_pool):
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            raise RuntimeError("boom")
    assert conn.commits == 0 and conn.rollbacks >= 1 and conn.closed


def test_oracle_errors_are_translated_inside_connection(fake_pool):
    err = oracle_error(oracledb.IntegrityError, "ORA-02290", "ORA-02290: check constraint (APP.CHK_ACCOUNT_BALANCE) violated")
    with pytest.raises(ConstraintViolationError) as info:
        with db.connection():
            raise err
    assert info.value.constraint == "CHK_ACCOUNT_BALANCE"
    assert fake_pool.connections[0].rollbacks >= 1


def test_unique_idempotency_violation():
    err = oracle_error(
        oracledb.IntegrityError,
        "ORA-00001",
        "ORA-00001: unique constraint (SQL_X.UQ_IDEMPOTENCY_KEY) violated on table SQL_X.PAYMENT_TRANSACTIONS columns (IDEMPOTENCY_KEY)",
    )
    assert error_code(err) == "ORA-00001"
    assert constraint_name(err) == "UQ_IDEMPOTENCY_KEY"
    app_error = translate_db_error(err)
    assert app_error.status_code == 409 and app_error.code == "DUPLICATE_VALUE"


@pytest.mark.parametrize(
    "code,expected_type,status",
    [
        ("ORA-30006", ResourceBusyError, 409),
        ("ORA-00054", ResourceBusyError, 409),
        ("ORA-00060", ResourceBusyError, 409),
        ("ORA-20011", ConflictError, 409),
        ("ORA-01017", ServiceUnavailableError, 503),
        ("ORA-12514", ServiceUnavailableError, 503),
        ("ORA-00942", DatabaseOperationError, 500),
    ],
)
def test_error_mapping(code, expected_type, status):
    app_error = translate_db_error(oracle_error(oracledb.DatabaseError, code, f"{code}: something"))
    assert isinstance(app_error, expected_type)
    assert app_error.status_code == status


def test_translated_errors_do_not_leak_driver_text():
    secret_text = "ORA-00942: table or view SQL_SECRETSCHEMA.HIDDEN does not exist; password=hunter2 host=10.0.0.5"
    app_error = translate_db_error(oracle_error(oracledb.DatabaseError, "ORA-00942", secret_text))
    body = str(app_error.to_dict())
    assert "hunter2" not in body and "10.0.0.5" not in body and "SECRETSCHEMA" not in body


def test_pool_creation_failure_is_translated(monkeypatch):
    monkeypatch.setenv("ORACLE_USER", "u")
    monkeypatch.setenv("ORACLE_PASSWORD", "p-not-real")
    monkeypatch.setenv("ORACLE_HOST", "db.example.invalid")
    monkeypatch.setenv("ORACLE_SERVICE_NAME", "svc")
    import config

    config.get_settings.cache_clear()

    def failing_pool(settings):
        raise oracle_error(oracledb.OperationalError, "DPY-6005", "DPY-6005: cannot connect to database (p-not-real)")

    monkeypatch.setattr(db, "_create_pool", failing_pool)
    with pytest.raises(AppError) as info:
        db.get_pool()
    assert info.value.status_code == 503
    assert "p-not-real" not in str(info.value.to_dict())
