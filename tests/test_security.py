"""Security checks on the source code itself.

* SQL is never assembled at request time: every execute()/fetch call receives
  a literal or a module-level UPPER_CASE constant, with values passed as binds.
* No secrets in source control; no credentials or eval() in the frontend.
"""

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_DIRS = ["services", "database", "routes"]
PY_FILES = [p for d in PY_DIRS for p in (ROOT / d).rglob("*.py")] + [ROOT / "app.py", ROOT / "config.py"]
# fetch_all/fetch_one forward their `sql` argument to cursor.execute; that one call is the helper itself.
HELPER_EXEMPT = {("connection.py", "fetch_all"), ("connection.py", "fetch_one")}


def _sql_arg(call: ast.Call):
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name in ("execute", "executemany") and call.args:
        return name, call.args[0]
    if name in ("fetch_all", "fetch_one") and len(call.args) >= 2:
        return name, call.args[1]
    return None, None


def _is_safe_sql(node: ast.AST, module_constants: set[str]) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.Name) and node.id.isupper() and node.id in module_constants:
        return True
    if isinstance(node, ast.Attribute) and node.attr.isupper():  # e.g. ledger_service.SQL_X
        return True
    return False


def test_sql_is_never_built_from_runtime_values():
    problems = []
    for path in PY_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
        for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for call in [n for n in ast.walk(func) if isinstance(n, ast.Call)]:
                kind, arg = _sql_arg(call)
                if kind is None or (path.name, func.name) in HELPER_EXEMPT:
                    continue
                if not _is_safe_sql(arg, constants):
                    problems.append(f"{path.relative_to(ROOT)}:{call.lineno} {kind}({ast.unparse(arg)})")
    assert not problems, problems


def test_sql_constants_contain_no_format_placeholders():
    """A {placeholder} or %s inside SQL would hint at string formatting later."""
    import importlib

    for module_name in ("services.payment_service", "services.reconciliation_service", "services.transaction_service",
                        "services.ledger_service", "services.account_service", "services.audit_service",
                        "services.dashboard_service", "services.concurrency_service", "services.failure_service",
                        "database.schema", "database.connection"):
        module = importlib.import_module(module_name)
        for name, value in vars(module).items():
            if name.startswith(("SQL_", "PLSQL_")) and isinstance(value, str):
                assert "{" not in value and "%s" not in value, f"{module_name}.{name}"


def test_env_file_is_ignored_and_untracked():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in gitignore
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=30).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        tracked = []
    assert ".env" not in tracked


def test_env_example_has_no_values_for_secrets():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_HOST", "ORACLE_SERVICE_NAME", "ORACLE_DSN"):
        assert re.search(rf"^{key}=\s*$", example, re.M), key


def test_no_hardcoded_credentials_in_source():
    pattern = re.compile(r"(password|passwd|secret)\s*=\s*[\"'][^\"']+[\"']", re.I)
    offenders = [str(p.relative_to(ROOT)) for p in PY_FILES if pattern.search(p.read_text(encoding="utf-8"))]
    assert not offenders, offenders


def test_frontend_has_no_eval_or_external_calls():
    for js in (ROOT / "static" / "js").rglob("*.js"):
        text = js.read_text(encoding="utf-8")
        assert "eval(" not in text and "new Function(" not in text, js.name
        assert not re.search(r"fetch\(\s*[\"'`]https?://", text), js.name
        assert "oracledb" not in text.lower(), js.name
