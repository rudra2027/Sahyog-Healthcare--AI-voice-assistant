"""The registration form's "I am a" dropdown: doctors and front-desk staff register themselves with a hospital code."""
import uuid

import pytest

from app import supabase_auth
from app.config import settings

PEOPLE: dict[str, dict] = {}


def person(name: str) -> str:
    """A brand-new signed-in person (the test app and its database live for the whole run, so every test needs its own)."""
    token = "tok-" + uuid.uuid4().hex
    PEOPLE[token] = {"id": str(uuid.uuid4()), "email": f"{name.lower()}@x.com", "name": name}
    return token


@pytest.fixture(autouse=True)
def staff_codes(monkeypatch):
    monkeypatch.setattr(supabase_auth, "verify_token", lambda token: PEOPLE.get(token))
    monkeypatch.setattr(settings, "doctor_signup_code", "DOC-CODE")
    monkeypatch.setattr(settings, "frontdesk_signup_code", "DESK-CODE")
    from app import main
    main._CLAIM_TRIES.clear()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def claim(client, token, **body):
    return client.post("/api/me/role", json=body, headers=auth(token))


def test_config_says_which_staff_roles_are_open_but_never_the_codes(client):
    body = client.get("/api/config").json()
    assert body["staff_signup"] == {"doctor": True, "frontdesk": True}
    assert "DOC-CODE" not in str(body) and "DESK-CODE" not in str(body)


def test_front_desk_registers_and_lands_on_the_desk_portal(client):
    tok_b = person("Meera")
    client.get("/api/me", headers=auth(tok_b))                      # first sign-in creates the patient login
    assert claim(client, tok_b, role="frontdesk", code="DESK-CODE").json()["role"] == "frontdesk"
    assert client.get("/api/me", headers=auth(tok_b)).json()["role"] == "frontdesk"
    assert client.get("/api/appointments", headers=auth(tok_b)).status_code == 200
    assert client.get("/api/doctor/patients", headers=auth(tok_b)).status_code == 403     # not a doctor


def test_doctor_registers_with_specialty_and_clinic_and_gets_a_schedule(client):
    tok_a = person("Adarsh")
    doc = client.get("/api/doctors").json()[0]
    client.get("/api/me", headers=auth(tok_a))
    out = claim(client, tok_a, role="doctor", code="DOC-CODE", specialty=doc["specialty"], clinic=doc["clinic"]).json()
    assert out["role"] == "doctor" and out["doctor_id"]
    assert client.get("/api/doctor/appointments", headers=auth(tok_a)).status_code == 200
    mine = [d for d in client.get("/api/doctors").json() if d["id"] == out["doctor_id"]][0]
    assert mine["name"] == "Adarsh" and mine["specialty"] == doc["specialty"]
    assert client.get(f"/api/doctors/{out['doctor_id']}/slots").json()                       # bookable straight away


def test_wrong_code_changes_nothing(client):
    tok_a = person("Adarsh")
    client.get("/api/me", headers=auth(tok_a))
    assert claim(client, tok_a, role="frontdesk", code="guess").status_code == 403
    assert claim(client, tok_a, role="frontdesk", code="DOC-CODE").status_code == 403      # the doctor code does not open the desk
    assert client.get("/api/me", headers=auth(tok_a)).json()["role"] == "patient"


def test_the_code_cannot_be_guessed_by_trying_again_and_again(client):
    tok_a = person("Adarsh")
    client.get("/api/me", headers=auth(tok_a))
    codes = [claim(client, tok_a, role="frontdesk", code=f"try{i}").status_code for i in range(7)]
    assert codes[:5] == [403] * 5 and codes[5:] == [429, 429]
    assert claim(client, tok_a, role="frontdesk", code="DESK-CODE").status_code == 429


def test_roles_with_no_code_configured_stay_closed(client, monkeypatch):
    tok_b = person("Meera")
    monkeypatch.setattr(settings, "frontdesk_signup_code", "")
    client.get("/api/me", headers=auth(tok_b))
    assert claim(client, tok_b, role="frontdesk", code="").status_code == 403
    assert claim(client, tok_b, role="compliance", code="DESK-CODE").status_code == 403    # compliance can never self-register
    assert claim(client, tok_b, role="admin", code="x").status_code == 403


def test_doctor_must_pick_a_real_specialty_and_clinic(client):
    tok_a = person("Adarsh")
    client.get("/api/me", headers=auth(tok_a))
    assert claim(client, tok_a, role="doctor", code="DOC-CODE", specialty="Wizardry", clinic="Baner").status_code == 400
    assert claim(client, tok_a, role="doctor", code="DOC-CODE").status_code == 400
    assert client.get("/api/me", headers=auth(tok_a)).json()["role"] == "patient"


def test_a_staff_member_cannot_switch_roles_again_and_demo_staff_cannot_use_it(client):
    tok_b = person("Meera")
    client.get("/api/me", headers=auth(tok_b))
    claim(client, tok_b, role="frontdesk", code="DESK-CODE")
    assert claim(client, tok_b, role="frontdesk", code="DESK-CODE").status_code == 403     # only patients may claim a role
    assert client.post("/api/me/role", json={"role": "doctor", "code": "DOC-CODE"}, headers={"X-User-Id": "d1"}).status_code == 403


def test_an_account_with_bookings_cannot_become_staff(client):
    tok_c = person("Chandra")
    me = client.get("/api/me", headers=auth(tok_c)).json()
    doctor = client.get("/api/doctors").json()[0]
    slot = client.get(f"/api/doctors/{doctor['id']}/slots").json()[0]
    from app import database as db
    db.reserve_slot(me["id"], slot["id"], "signup-test-key")
    assert claim(client, tok_c, role="frontdesk", code="DESK-CODE").status_code == 400
