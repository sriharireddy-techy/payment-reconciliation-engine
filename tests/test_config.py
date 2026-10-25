"""Configuration: environment parsing and missing-setting detection."""

import pytest

from config import ConfigError, load_settings


def test_defaults_without_oracle_settings():
    settings = load_settings()
    assert settings.is_db_configured is False
    assert settings.missing_db_settings == ["ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_HOST", "ORACLE_SERVICE_NAME"]
    assert settings.enable_failure_simulation is False
    assert settings.oracle_port == 1521


def test_host_port_service_configuration(monkeypatch):
    monkeypatch.setenv("ORACLE_USER", "app_user")
    monkeypatch.setenv("ORACLE_PASSWORD", "not-a-real-password")
    monkeypatch.setenv("ORACLE_HOST", "db.example.invalid")
    monkeypatch.setenv("ORACLE_PORT", "1522")
    monkeypatch.setenv("ORACLE_SERVICE_NAME", "freepdb1")
    settings = load_settings()
    assert settings.is_db_configured
    assert settings.missing_db_settings == []
    assert settings.oracle_port == 1522


def test_dsn_replaces_host_and_service(monkeypatch):
    monkeypatch.setenv("ORACLE_USER", "app_user")
    monkeypatch.setenv("ORACLE_PASSWORD", "not-a-real-password")
    monkeypatch.setenv("ORACLE_DSN", "db.example.invalid:1521/freepdb1")
    assert load_settings().is_db_configured


def test_password_never_appears_in_repr(monkeypatch):
    monkeypatch.setenv("ORACLE_PASSWORD", "TopSecretValue42")
    assert "TopSecretValue42" not in repr(load_settings())


@pytest.mark.parametrize(
    "name,value",
    [
        ("ENABLE_FAILURE_SIMULATION", "maybe"),
        ("ORACLE_PORT", "abc"),
        ("ORACLE_PORT", "70000"),
        ("DB_POOL_MAX", "0"),
        ("CONCURRENCY_MAX_REQUESTS", "1"),
    ],
)
def test_invalid_values_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        load_settings()


def test_pool_min_cannot_exceed_max(monkeypatch):
    monkeypatch.setenv("DB_POOL_MIN", "5")
    monkeypatch.setenv("DB_POOL_MAX", "2")
    with pytest.raises(ConfigError):
        load_settings()


def test_failure_simulation_flag(monkeypatch):
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")
    assert load_settings().enable_failure_simulation is True
