"""Audit logging helpers: secrets are dropped, size limits respected, action types valid."""

import json

import pytest

from services import audit_service


def test_sensitive_keys_are_never_logged():
    text = audit_service.details_json(
        "TEST", password="p1", webhook_secret="s1", api_token="t1", oracle_dsn="d1", amount="5.00",
        nested={"client_secret": "s2", "ok": 1},
    )
    for value in ("p1", "s1", "t1", "d1", "s2"):
        assert value not in text
    data = json.loads(text)
    assert data["event"] == "TEST" and data["amount"] == "5.00" and data["nested"] == {"ok": 1}


def test_details_fit_the_column():
    text = audit_service.details_json("BIG", blob="x" * 10000)
    assert len(text.encode("utf-8")) <= audit_service.DETAILS_MAX_BYTES
    assert json.loads(text)["truncated"] is True


def test_only_schema_action_types_are_allowed():
    for action in ("INSERT", "UPDATE", "DELETE", "SECURITY"):
        assert audit_service.audit_row("T", action, 1, "{}")["action_type"] == action
    with pytest.raises(ValueError):
        audit_service.audit_row("T", "LOGIN", 1, "{}")


def test_event_name_extraction():
    assert audit_service.event_name('{"event":"PAYMENT_SUCCESS"}') == "PAYMENT_SUCCESS"
    assert audit_service.event_name("free text from Review 1") is None
    assert audit_service.event_name(None) is None


def test_audit_filters_are_validated(client):
    assert client.get("/api/audit-logs", params={"table_name": "USERS; DROP TABLE USERS"}).status_code == 422
    assert client.get("/api/audit-logs", params={"action_type": "LOGIN"}).status_code == 422
    assert client.get("/api/audit-logs", params={"limit": 1000}).status_code == 422
