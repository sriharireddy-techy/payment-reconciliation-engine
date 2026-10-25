"""Every SQL statement in the app uses only real tables and columns.

The expected schema (database/schema.py) mirrors the live FreeSQL schema. Each
SQL_* / PLSQL_* constant is parsed with sqlglot (Oracle dialect); SELECTs are
qualified against the schema, and INSERT/UPDATE target columns are checked
directly. This catches invented or misspelt column names without a database.
It does not prove the SQL runs on Oracle; the integration tests do that.
"""

import importlib
import re

import pytest

sqlglot = pytest.importorskip("sqlglot")
from sqlglot import exp  # noqa: E402
from sqlglot.optimizer.qualify import qualify  # noqa: E402

from database.schema import EXPECTED_COLUMNS  # noqa: E402

MODULES = (
    "services.payment_service", "services.reconciliation_service", "services.transaction_service",
    "services.ledger_service", "services.account_service", "services.audit_service",
    "services.dashboard_service", "services.concurrency_service", "services.failure_service",
)
SCHEMA = {t.lower(): {c.lower(): "VARCHAR" for c in cols} for t, cols in EXPECTED_COLUMNS.items()}
SCHEMA["dual"] = {"dummy": "VARCHAR"}


def collect_statements():
    statements = []
    for module_name in MODULES:
        module = importlib.import_module(module_name)
        for name, value in sorted(vars(module).items()):
            if not isinstance(value, str):
                continue
            if name.startswith("SQL_"):
                statements.append((f"{module_name}.{name}", value))
            elif name.startswith("PLSQL_"):
                for i, chunk in enumerate(value.split(";")):
                    match = re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", chunk)
                    if match:
                        stmt = chunk[match.start():]
                        # SELECT ... INTO <targets> FROM  ->  SELECT ... FROM
                        stmt = re.sub(r"\bINTO\s+[:\w\s,]+?(?=\s+FROM\b)", "", stmt) if stmt.lstrip().startswith("SELECT") else stmt
                        statements.append((f"{module_name}.{name}[{i}]", stmt))
    return statements


STATEMENTS = collect_statements()


def test_statements_were_found():
    assert len(STATEMENTS) > 40


def _check_dml_columns(tree):
    problems = []
    if isinstance(tree, exp.Insert):
        table = tree.this.this.name.lower() if isinstance(tree.this, exp.Schema) else tree.this.name.lower()
        columns = [c.name.lower() for c in tree.this.expressions] if isinstance(tree.this, exp.Schema) else []
        problems += [f"{table}.{c}" for c in columns if c not in SCHEMA.get(table, {})]
    if isinstance(tree, (exp.Update, exp.Delete)):
        table = tree.this.name.lower()
        for column in tree.find_all(exp.Column):
            in_subquery = column.find_ancestor(exp.Subquery, exp.Select)
            if not in_subquery and column.name.lower() not in SCHEMA.get(table, {}) and column.name.upper() not in ("SYSTIMESTAMP", "SYSDATE"):
                problems.append(f"{table}.{column.name.lower()}")
    return problems


@pytest.mark.parametrize("name,sql", STATEMENTS, ids=[s[0] for s in STATEMENTS])
def test_statement_uses_only_known_columns(name, sql):
    tree = sqlglot.parse_one(sql, read="oracle")
    unknown_tables = {t.name.lower() for t in tree.find_all(exp.Table)} - set(SCHEMA)
    assert not unknown_tables, unknown_tables
    assert not _check_dml_columns(tree)
    qualify(tree.copy(), schema=SCHEMA, dialect="oracle", validate_qualify_columns=True, identify=False)


def test_sql_never_touches_columns_the_app_must_not_read():
    for name, sql in STATEMENTS:
        assert "webhook_secret" not in sql.lower(), name
        assert "account_number" not in sql.lower() or "SUBSTR(" in sql, f"{name} exposes full account numbers"
