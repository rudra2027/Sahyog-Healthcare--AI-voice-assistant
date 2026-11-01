"""Tests for the Supabase sign-in path. Supabase itself is replaced by a fake, so no network is needed."""
from app import supabase_auth
from app.config import settings
from tests.conftest import CASE_B

TOKEN = {"Authorization": "Bearer good-token"}
SB_USER = {"id": "11111111-2222-3333-4444-555555555555", "email": "asha@example.com", "name": "asha"}


def fake_verify(token: str):
    """Pretend Supabase: only 'good-token' belongs to someone."""
    return SB_USER if token == "good-token" else None


def test_config_exposes_only_public_values(client, monkeypatch):
    monkeypatch.setattr(settings, "doctor_signup_code", "")
    monkeypatch.setattr(settings, "frontdesk_signup_code", "")
    body = client.get("/api/config").json()
    assert set(body) == {"supabase_url", "supabase_key", "auth_enabled", "demo_login", "stt", "staff_signup"}
    assert body["staff_signup"] == {"doctor": False, "frontdesk": False}      # the codes themselves are never sent


def test_valid_token_becomes_a_patient(client, monkeypatch):
    monkeypatch.setattr(supabase_auth, "verify_token", fake_verify)
    me = client.get("/api/me", headers=TOKEN).json()
    assert me["id"] == f"sb-{SB_USER['id']}" and me["role"] == "patient"


def test_bad_token_is_rejected(client, monkeypatch):
    monkeypatch.setattr(supabase_auth, "verify_token", fake_verify)
    assert client.get("/api/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_signed_in_user_can_chat_and_owns_the_thread(client, monkeypatch):
    monkeypatch.setattr(supabase_auth, "verify_token", fake_verify)
    res = client.post("/api/chat", json={"message": CASE_B}, headers=TOKEN).json()
    assert res["thread_id"].startswith(f"sb-{SB_USER['id']}-")
    # a demo patient cannot continue that conversation
    other = client.post("/api/chat", json={"message": "hello", "thread_id": res["thread_id"]}, headers={"X-User-Id": "p1"})
    assert other.status_code == 403


def test_signed_in_user_cannot_use_staff_endpoints(client, monkeypatch):
    monkeypatch.setattr(supabase_auth, "verify_token", fake_verify)
    assert client.get("/api/appointments", headers=TOKEN).status_code == 403
    assert client.post("/api/scribe/start", json={"transcript": "x", "doctor_id": "d1"}, headers=TOKEN).status_code == 403


def test_demo_login_can_be_turned_off(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "allow_demo_login", False)
    assert client.get("/api/me", headers={"X-User-Id": "p1"}).status_code == 401


def test_token_check_is_skipped_when_supabase_is_not_configured(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "supabase_url", "")
    assert supabase_auth.verify_token("anything") is None


# ---------------------------------------------------------------- speech to text
def test_stt_returns_the_words(client, monkeypatch):
    from app import stt
    monkeypatch.setattr(stt, "transcribe", lambda audio: "back pain for three weeks")
    res = client.post("/api/stt", files={"file": ("voice.webm", b"not-really-audio", "audio/webm")})
    assert res.status_code == 200 and res.json() == {"text": "back pain for three weeks"}


def test_stt_rejects_empty_and_huge_recordings(client):
    assert client.post("/api/stt", files={"file": ("v.webm", b"", "audio/webm")}).status_code == 400
    assert client.post("/api/stt", files={"file": ("v.webm", b"x" * (3 * 1024 * 1024 + 1), "audio/webm")}).status_code == 413


def test_stt_503_when_model_missing_so_browser_can_fall_back(client, monkeypatch):
    from app import stt
    def boom(audio):
        raise stt.SttUnavailable("no model")
    monkeypatch.setattr(stt, "transcribe", boom)
    assert client.post("/api/stt", files={"file": ("v.webm", b"abc", "audio/webm")}).status_code == 503
