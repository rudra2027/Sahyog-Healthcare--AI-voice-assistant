"""Who may call what. One table, every endpoint, every kind of person.

For each endpoint we say which roles are allowed. Allowed roles must NOT get 401/403, and everyone else must get 403
(or 401 when nobody is signed in). A new endpoint that is missing from this table is a bug in the test, not a feature.
"""
import pytest

from app.config import settings

ROLES = {"patient": {"X-User-Id": "p1"}, "doctor": {"X-User-Id": "d1"}, "frontdesk": {"X-User-Id": "fd1"}, "compliance": {"X-User-Id": "c1"}}
ANYONE = {"patient", "doctor", "frontdesk", "compliance"}
STAFF = {"doctor", "frontdesk", "compliance"}

# (method, path, json body, roles that may call it)
TABLE = [
    ("GET", "/api/me", None, ANYONE),
    ("GET", "/api/doctor/appointments", None, {"doctor"}),
    ("GET", "/api/doctor/patients", None, {"doctor"}),
    ("GET", "/api/doctor/patients/p2", None, {"doctor"}),
    ("POST", "/api/doctor/patients/p2/ask", {"question": "ldl"}, {"doctor"}),
    ("GET", "/api/doctor/reports", None, {"doctor"}),
    ("GET", "/api/doctor/slots", None, {"doctor"}),
    ("POST", "/api/doctor/followups", {"patient_id": "p2", "slot_id": 999999}, {"doctor"}),
    ("POST", "/api/scribe/start", {"transcript": "Doctor: hello there", "patient_id": "nobody"}, {"doctor"}),
    ("GET", "/api/lessons", None, {"doctor"}),
    ("POST", "/api/assistant", {"message": "help"}, STAFF),
    ("GET", "/api/appointments", None, {"frontdesk", "compliance"}),
    ("POST", "/api/appointments/999999/cancel", None, {"frontdesk"}),
    ("GET", "/api/tickets", None, STAFF),
    ("POST", "/api/tickets/999999/resolve", None, {"frontdesk"}),
    ("GET", "/api/audit", None, {"compliance"}),
    ("GET", "/api/metrics", None, {"frontdesk", "compliance"}),
    ("GET", "/api/health/details", None, {"frontdesk", "compliance"}),
    ("POST", "/api/chat", {"message": "hello"}, {"patient"}),
    ("GET", "/api/records/documents", None, {"patient"}),
    ("POST", "/api/records/ask", {"question": "cholesterol"}, {"patient"}),
    ("GET", "/api/my/visits", None, {"patient"}),
    ("POST", "/api/my/appointments/999999/cancel", None, {"patient"}),
    ("GET", "/api/memory/prefs", None, {"patient"}),
]
CASES = [(m, path, body, role) for m, path, body, allowed in TABLE for role in ROLES]


def call(client, method, path, body, headers):
    return client.request(method, path, json=body, headers=headers)


@pytest.mark.parametrize("method,path,body,role", CASES, ids=[f"{r}:{m} {p}" for m, p, _, r in CASES])
def test_every_role_gets_the_right_answer(client, method, path, body, role):
    allowed = next(roles for m, p, b, roles in TABLE if (m, p) == (method, path))
    status = call(client, method, path, body, ROLES[role]).status_code
    if role in allowed:
        assert status not in (401, 403), f"{role} should be allowed to {method} {path} but got {status}"
    else:
        assert status == 403, f"{role} must NOT {method} {path} but got {status}"


@pytest.mark.parametrize("method,path,body", [(m, p, b) for m, p, b, _ in TABLE], ids=[f"{m} {p}" for m, p, _, _ in TABLE])
def test_nobody_signed_in_gets_nothing(client, monkeypatch, method, path, body):
    monkeypatch.setattr(settings, "allow_demo_login", False)           # real deployments: no demo header, so no identity
    assert call(client, method, path, body, {}).status_code == 401


def test_public_pages_data_reveal_nothing_internal(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    config = client.get("/api/config").json()
    assert set(config) == {"supabase_url", "supabase_key", "auth_enabled", "demo_login", "stt", "staff_signup"}
    assert "secret" not in str(config).lower() and "service_role" not in str(config).lower()


def test_a_forged_identity_header_does_nothing_without_demo_login(client, monkeypatch):
    monkeypatch.setattr(settings, "allow_demo_login", False)
    for who in ("d1", "fd1", "c1", "p1"):
        assert client.get("/api/me", headers={"X-User-Id": who}).status_code == 401


def test_thread_ids_cannot_be_borrowed(client):
    first = client.post("/api/chat", json={"message": "hello"}, headers=ROLES["patient"]).json()
    stolen = client.post("/api/chat", json={"message": "hello", "thread_id": first["thread_id"]}, headers={"X-User-Id": "p3"})
    assert stolen.status_code == 403
