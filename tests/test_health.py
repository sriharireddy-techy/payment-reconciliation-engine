"""Application startup, health endpoints, static frontend and route wiring."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_ENDPOINTS = {
    ("get", "/health"),
    ("get", "/api/health/database"),
    ("get", "/api/dashboard"),
    ("get", "/api/accounts"),
    ("get", "/api/transactions"),
    ("get", "/api/transactions/{transaction_id}"),
    ("post", "/api/payments"),
    ("get", "/api/transactions/{transaction_id}/ledger"),
    ("post", "/api/concurrency/run"),
    ("post", "/api/failure-simulation/run"),
    ("post", "/api/reconciliation/batches"),
    ("get", "/api/reconciliation/batches"),
    ("get", "/api/reconciliation/batches/{batch_id}"),
    ("get", "/api/reconciliation/exceptions"),
    ("get", "/api/audit-logs"),
}


def test_app_starts_and_reports_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_database_health_without_configuration(client):
    response = client.get("/api/health/database")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_configured"
    assert body["connected"] is False
    assert "ORACLE_HOST" in body["missing"]


def test_data_endpoints_report_missing_configuration(client):
    for path in ("/api/dashboard", "/api/accounts", "/api/transactions", "/api/audit-logs", "/api/reconciliation/batches"):
        response = client.get(path)
        assert response.status_code == 503, path
        assert response.json()["error"]["code"] == "DB_NOT_CONFIGURED"


def test_every_required_endpoint_is_registered(client):
    paths = client.get("/openapi.json").json()["paths"]
    registered = {(method, path) for path, methods in paths.items() for method in methods}
    registered |= {("get", "/health")}
    missing = REQUIRED_ENDPOINTS - registered
    assert not missing, missing


def test_swagger_docs_available(client):
    assert client.get("/docs").status_code == 200


def test_dashboard_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "/static/js/app.js" in response.text


def test_static_assets_referenced_by_the_page_exist():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    for ref in re.findall(r'(?:src|href)="/static/([^"]+)"', html):
        assert (ROOT / "static" / ref).is_file(), ref


def test_frontend_api_calls_match_backend_routes(client):
    """Every /api/... path used by the JavaScript must exist in the FastAPI app."""
    paths = client.get("/openapi.json").json()["paths"]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", p) + "$") for p in paths]
    used = set()
    for js in (ROOT / "static" / "js").rglob("*.js"):
        text = js.read_text(encoding="utf-8")
        for raw in re.findall(r"api\.(?:get|post|patch)\(\s*[`\"'](/api/[^`\"'?]+)", text):
            used.add(re.sub(r"\$\{[^}]+\}", "1", raw))
    assert used, "no API calls found in the frontend"
    unknown = sorted(u for u in used if not any(p.match(u) for p in patterns))
    assert not unknown, unknown


def test_frontend_routes_are_all_handled():
    """Every in-app link (#/...) must match a route in app.js."""
    app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    route_res = [re.compile(r) for r in re.findall(r"re: /(.+?)/,", app_js)]
    links = set()
    for f in [ROOT / "templates" / "index.html", *(ROOT / "static" / "js").rglob("*.js")]:
        for link in re.findall(r'href="#(/[^"?]*)', f.read_text(encoding="utf-8")):
            links.add(re.sub(r"\$\{[^}]+\}", "1", link))
    for link in links:
        assert any(r.match(link) for r in route_res), link
