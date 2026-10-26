"""Application settings, read from environment variables (and a local .env file).

Nothing here holds a real credential: values come from the developer's own .env,
which is excluded from Git. See .env.example for every supported variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class ConfigError(ValueError):
    """Raised when an environment variable has an invalid value."""


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _str(name: str) -> str:
    return os.environ.get(name, "").strip()


@dataclass(frozen=True)
class Settings:
    oracle_user: str = ""
    oracle_password: str = field(default="", repr=False)
    oracle_host: str = ""
    oracle_port: int = 1521
    oracle_service_name: str = ""
    oracle_dsn: str = ""
    db_pool_min: int = 1
    db_pool_max: int = 6
    db_pool_wait_seconds: int = 60
    db_call_timeout_seconds: int = 65
    enable_failure_simulation: bool = False
    concurrency_max_requests: int = 25
    app_actor: str = "web-dashboard"

    @property
    def is_db_configured(self) -> bool:
        has_target = bool(self.oracle_dsn) or bool(self.oracle_host and self.oracle_service_name)
        return bool(self.oracle_user and self.oracle_password and has_target)

    @property
    def missing_db_settings(self) -> list[str]:
        missing = []
        if not self.oracle_user:
            missing.append("ORACLE_USER")
        if not self.oracle_password:
            missing.append("ORACLE_PASSWORD")
        if not self.oracle_dsn:
            if not self.oracle_host:
                missing.append("ORACLE_HOST")
            if not self.oracle_service_name:
                missing.append("ORACLE_SERVICE_NAME")
        return missing


def load_settings() -> Settings:
    """Build settings from the current environment (no caching, no .env loading)."""
    pool_min = _int("DB_POOL_MIN", 1, 0, 20)
    pool_max = _int("DB_POOL_MAX", 6, 1, 50)
    if pool_min > pool_max:
        raise ConfigError("DB_POOL_MIN cannot be larger than DB_POOL_MAX")
    return Settings(
        oracle_user=_str("ORACLE_USER"),
        oracle_password=os.environ.get("ORACLE_PASSWORD", ""),
        oracle_host=_str("ORACLE_HOST"),
        oracle_port=_int("ORACLE_PORT", 1521, 1, 65535),
        oracle_service_name=_str("ORACLE_SERVICE_NAME"),
        oracle_dsn=_str("ORACLE_DSN"),
        db_pool_min=pool_min,
        db_pool_max=pool_max,
        db_pool_wait_seconds=_int("DB_POOL_WAIT_SECONDS", 60, 1, 600),
        db_call_timeout_seconds=_int("DB_CALL_TIMEOUT_SECONDS", 65, 5, 600),
        enable_failure_simulation=_bool("ENABLE_FAILURE_SIMULATION", False),
        concurrency_max_requests=_int("CONCURRENCY_MAX_REQUESTS", 25, 2, 100),
        app_actor=_str("APP_ACTOR") or "web-dashboard",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings for the running app; reads the project .env once (real env vars win)."""
    if not _bool("PRC_SKIP_DOTENV", False):
        load_dotenv(PROJECT_ROOT / ".env", override=False)
    return load_settings()
