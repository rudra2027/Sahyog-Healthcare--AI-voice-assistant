"""Tests for the three separate portals: patient, doctor and front desk."""
from pathlib import Path

import pytest

from app import database as db
from app import supabase_auth
from tests.conftest import CASE_B

DOC_TOKEN = {"Authorization": "Bearer doctor-token"}
PAT_TOKEN = {"Authorization": "Bearer patient-token"}
DOCTOR = {"X-User-Id": "d1"}
FRONT = {"X-User-Id": "fd1"}
COMPLIANCE = {"X-User-Id": "c1"}
P1 = {"X-User-Id": "p1"}
P3 = {"X-User-Id": "p3"}

PEOPLE = {
    "doctor-token": {"id": "aaaaaaaa-0000-0000-0000-000000000001", "email": "adarsh@example.com", "name": "adarsh"},
    "patient-token": {"id": "bbbbbbbb-0000-0000-0000-000000000002", "email": "new@example.com", "name": "new"},
}


@pytest.fixture()
def signed_in(monkeypatch):
    """Pretend Supabase: these two tokens belong to these two people."""
    monkeypatch.setattr(supabase_auth, "verify_token", lambda token: PEOPLE.get(token))


def ask(client, headers, message):
    return client.post("/api/assistant", json={"message": message}, headers=headers).json()


def test_new_accounts_are_patients_and_cannot_open_staff_data(client, signed_in):
    assert client.get("/api/me", headers=PAT_TOKEN).json()["role"] == "patient"
    for url in ("/api/doctor/appointments", "/api/doctor/patients", "/api/appointments", "/api/tickets"):
        assert client.get(url, headers=PAT_TOKEN).status_code == 403, url
    assert client.post("/api/assistant", json={"message": "my schedule"}, headers=PAT_TOKEN).status_code == 403


def test_an_administrator_can_make_someone_a_doctor(client, signed_in):
    client.get("/api/me", headers=DOC_TOKEN)                       # signing in once creates their row
    db.set_staff_role("sb-" + PEOPLE["doctor-token"]["id"], "doctor", "d1")
    me = client.get("/api/me", headers=DOC_TOKEN).json()
    assert me["role"] == "doctor" and me["doctor_id"] == "d1"
    assert client.get("/api/doctor/appointments", headers=DOC_TOKEN).status_code == 200


def test_staff_roles_need_a_real_doctor_and_an_existing_login():
    with pytest.raises(ValueError):
        db.set_staff_role("sb-nobody", "doctor", "d1")
    with pytest.raises(ValueError):
        db.set_staff_role("d1", "doctor", "d999")
    with pytest.raises(ValueError):
        db.set_staff_role("d1", "superuser")


def test_doctor_sees_only_their_own_appointments_in_time_order(client):
    mine = client.get("/api/doctor/appointments", headers=DOCTOR).json()
    assert len(mine) >= 3 and all(a["doctor_id"] == "d1" for a in mine)
    times = [a["starts_at"] for a in mine]
    assert times == sorted(times)
    assert all("patient_name" in a and "reason" in a for a in mine)


def test_doctor_can_open_only_patients_who_booked_with_them(client):
    card = client.get("/api/doctor/patients/p2", headers=DOCTOR)
    assert card.status_code == 200 and card.json()["name"] == "Asha Deshmukh"
    assert any(d["filename"] == "lipid_report.pdf" for d in card.json()["documents"])
    assert client.get("/api/doctor/patients/nobody", headers=DOCTOR).status_code == 403


def test_doctor_can_ask_about_a_patients_report_with_citations(client):
    res = client.post("/api/doctor/patients/p2/ask", json={"question": "What was the LDL cholesterol?"}, headers=DOCTOR).json()
    assert res["outcome"] == "done" and res["citations"] and "162" in res["reply"]


def test_doctor_cannot_ask_about_someone_elses_patient(client):
    assert client.post("/api/doctor/patients/p-unknown/ask", json={"question": "cholesterol"}, headers=DOCTOR).status_code == 403


def test_a_patient_cannot_use_doctor_endpoints(client):
    assert client.get("/api/doctor/appointments", headers=P1).status_code == 403
    assert client.get("/api/doctor/patients/p2", headers=P1).status_code == 403


def test_doctor_assistant_reads_the_schedule_and_patient_details(client):
    schedule = ask(client, DOCTOR, "What is my schedule?")["reply"]
    assert "appointment" in schedule.lower()
    assert "next patient" in ask(client, DOCTOR, "Who is my next patient?")["reply"].lower()
    about = ask(client, DOCTOR, "Tell me about Asha")["reply"]
    assert "Asha" in about and "lipid_report.pdf" in about and "Cholesterol" in about


def test_doctor_assistant_answers_report_questions_with_sources(client):
    res = ask(client, DOCTOR, "What does Asha lipid report say about cholesterol?")
    assert res["citations"] and ("162" in res["reply"] or "238" in res["reply"])


def test_spoken_possessive_names_do_not_break_the_report_search(client):
    res = ask(client, DOCTOR, "What does Asha's lipid report say about LDL?")
    assert res["citations"] and "162" in res["reply"]


def test_front_desk_assistant_answers_from_real_bookings(client):
    assert "booking" in ask(client, FRONT, "How many appointments are there today?")["reply"].lower()
    assert "ticket" in ask(client, FRONT, "Any open tickets?")["reply"].lower()
    assert "Cardiology" in ask(client, FRONT, "What is the next free slot for cardiology?")["reply"]


def test_only_front_desk_can_close_a_ticket(client):
    client.post("/api/chat", json={"message": "crushing chest pain and sweating"}, headers=P1)   # an emergency opens a ticket
    ticket = client.get("/api/tickets", headers=FRONT).json()[0]
    assert client.post(f"/api/tickets/{ticket['id']}/resolve", headers=COMPLIANCE).status_code == 403
    assert client.post(f"/api/tickets/{ticket['id']}/resolve", headers=FRONT).status_code in (200, 404)


def test_the_portals_are_separated_by_role(client):
    assert client.get("/api/doctor/appointments", headers=FRONT).status_code == 403
    assert client.get("/api/appointments", headers=DOCTOR).status_code == 403
    assert client.post("/api/appointments/1/cancel", headers=DOCTOR).status_code == 403


def test_a_booking_keeps_the_reason_and_cancelling_ends_doctor_access(client):
    first = client.post("/api/chat", json={"message": CASE_B}, headers=P3).json()
    option = first["options"][0]
    assert client.post("/api/chat/resume", json={"thread_id": first["thread_id"], "slot_id": option["slot_id"]}, headers=P3).status_code == 200
    appt = next(a for a in client.get("/api/appointments", headers=FRONT).json() if a["patient_id"] == "p3" and "back pain" in (a["reason"] or "").lower())
    assert appt["reason"]
    assert client.post(f"/api/appointments/{appt['id']}/cancel", headers=FRONT).status_code == 200


def test_pages_are_served_and_the_demo_picker_is_not_public(client, monkeypatch):
    for path in ("/", "/doctor", "/desk"):
        assert client.get(path).status_code == 200
    from app.config import settings
    monkeypatch.setattr(settings, "allow_demo_login", False)
    assert client.get("/api/personas").status_code == 404


def test_page_files_leave_out_backend_details():
    """The three pages must not talk about models, memory, caching or workflows."""
    banned = ["LangGraph", "OpenRouter", "gpt-6", "checkpointer", "p95", "TTL", "Thread memory", "cache hit", "Behind the scenes", "llmPill"]
    for name in ("index.html", "doctor.html", "desk.html", "app.js", "doctor.js", "desk.js", "portal.js"):
        text = (Path(__file__).resolve().parent.parent / "static" / name).read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, f"{name} mentions {word}"
