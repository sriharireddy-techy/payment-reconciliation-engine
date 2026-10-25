"""Oracle connection pool and small query helpers (python-oracledb, Thin mode).

Thin mode talks to Oracle directly over the network, so no Oracle Client or
local database installation is needed. The pool is created lazily on first
use, which lets the web app start (and show a clear message) before .env has
been filled in.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

import oracledb

from config import Settings, get_settings
from database.errors import translate_db_error
from services.errors import DatabaseNotConfiguredError

# Fetch Oracle NUMBER values as decimal.Decimal so money is never a float.
oracledb.defaults.fetch_decimals = True

_pool: oracledb.ConnectionPool | None = None
_pool_init_lock = threading.Lock()  # guards one-time pool creation only


def _create_pool(settings: Settings) -> oracledb.ConnectionPool:
    common = dict(
        user=settings.oracle_user,
        password=settings.oracle_password,
        min=settings.db_pool_min,
        max=settings.db_pool_max,
        increment=1,
        getmode=oracledb.POOL_GETMODE_TIMEDWAIT,
        wait_timeout=settings.db_pool_wait_seconds * 1000,
        ping_interval=60,
    )
    if settings.oracle_dsn:
        return oracledb.create_pool(dsn=settings.oracle_dsn, **common)
    return oracledb.create_pool(
        host=settings.oracle_host,
        port=settings.oracle_port,
        service_name=settings.oracle_service_name,
        **common,
    )


def get_pool() -> oracledb.ConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    settings = get_settings()
    if not settings.is_db_configured:
        raise DatabaseNotConfiguredError(
            "Oracle connection settings are missing. Fill in .env and restart the app.",
            details={"missing": settings.missing_db_settings},
        )
    with _pool_init_lock:
        if _pool is None:
            try:
                _pool = _create_pool(settings)
            except oracledb.Error as exc:
                raise translate_db_error(exc) from exc
    return _pool


def close_pool() -> None:
    global _pool
    with _pool_init_lock:
        if _pool is not None:
            try:
                _pool.close(force=True)
            finally:
                _pool = None


@contextmanager
def connection() -> Iterator[oracledb.Connection]:
    """Borrow a pooled connection. Anything not committed is rolled back on exit.

    oracledb errors raised inside the block are translated into AppError, so
    callers and routes only ever deal with application errors.
    """
    pool = get_pool()
    try:
        conn = pool.acquire()
    except oracledb.Error as exc:
        raise translate_db_error(exc) from exc
    conn.call_timeout = get_settings().db_call_timeout_seconds * 1000
    try:
        yield conn
    except oracledb.Error as exc:
        _safe_rollback(conn)
        raise translate_db_error(exc) from exc
    except BaseException:
        _safe_rollback(conn)
        raise
    else:
        _safe_rollback(conn)  # no-op when the caller committed; protects against leaks
    finally:
        try:
            conn.close()
        except oracledb.Error:
            pass


@contextmanager
def transaction() -> Iterator[oracledb.Connection]:
    """A connection whose work is committed if the block succeeds, else rolled back."""
    with connection() as conn:
        yield conn
        conn.commit()


def _safe_rollback(conn: oracledb.Connection) -> None:
    try:
        conn.rollback()
    except oracledb.Error:
        pass


def _row_factory(cursor: oracledb.Cursor):
    names = [d[0].lower() for d in cursor.description]

    def make(*values: Any) -> dict[str, Any]:
        return dict(zip(names, values))

    return make


def fetch_all(cursor: oracledb.Cursor, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run a query with bind variables and return rows as dicts (lower-case keys)."""
    cursor.execute(sql, params or {})
    cursor.rowfactory = _row_factory(cursor)
    return cursor.fetchall()


def fetch_one(cursor: oracledb.Cursor, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    rows = fetch_all(cursor, sql, params)
    return rows[0] if rows else None


def ping() -> dict[str, Any]:
    """Run a harmless read-only query and report who we are connected as."""
    with connection() as conn:
        cur = conn.cursor()
        row = fetch_one(cur, SQL_PING)
        return {
            "database_user": row["db_user"] if row else None,
            "server_time": row["server_time"].isoformat() if row and row.get("server_time") else None,
            "server_timezone": row["server_tz"] if row else None,
            "server_version": conn.version,
        }


SQL_PING = """
SELECT USER AS db_user,
       CAST(SYSTIMESTAMP AS TIMESTAMP) AS server_time,
       TO_CHAR(SYSTIMESTAMP, 'TZH:TZM') AS server_tz
  FROM DUAL
"""

