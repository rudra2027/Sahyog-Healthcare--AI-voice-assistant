"""Runs every scenario in scenarios.py against the real API, as the right kind of person.
A failing row means a patient, doctor or front-desk person asked something natural and got a bad answer."""
import re

import pytest

from tests import scenarios

P1 = {"X-User-Id": "p1"}
DOCTOR = {"X-User-Id": "d1"}
FRONT = {"X-User-Id": "fd1"}


@pytest.mark.parametrize("question,pattern", scenarios.PATIENT, ids=[q[:50] for q, _ in scenarios.PATIENT])
def test_patient_scenarios(client, question, pattern):
    reply = client.post("/api/chat", json={"message": question}, headers=P1).json()["reply"]
    assert re.search(pattern, reply), f"{question!r} -> {reply!r}"


@pytest.mark.parametrize("question,about,pattern", scenarios.PATIENT_RECORD_QUESTIONS, ids=[q[1] for q in scenarios.PATIENT_RECORD_QUESTIONS])
def test_patient_record_questions(client, question, about, pattern):
    reply = client.post("/api/chat", json={"message": question, "about": about}, headers=P1)
    text = reply.json()["reply"] if reply.status_code == 200 else reply.json()["detail"]
    assert re.search(pattern, text) or reply.status_code == 403, f"{question!r} -> {text!r}"


@pytest.mark.parametrize("question,pattern", scenarios.DOCTOR, ids=[q[:50] for q, _ in scenarios.DOCTOR])
def test_doctor_scenarios(client, question, pattern):
    reply = client.post("/api/assistant", json={"message": question}, headers=DOCTOR).json()["reply"]
    assert re.search(pattern, reply, flags=re.S), f"{question!r} -> {reply!r}"


@pytest.mark.parametrize("question,pattern", scenarios.FRONT_DESK, ids=[q[:50] for q, _ in scenarios.FRONT_DESK])
def test_front_desk_scenarios(client, question, pattern):
    reply = client.post("/api/assistant", json={"message": question}, headers=FRONT).json()["reply"]
    assert re.search(pattern, reply, flags=re.S), f"{question!r} -> {reply!r}"


# ----------------------------------------------------------------------------- the revisit journey, start to finish
def test_revisit_after_tests_journey(client):
    """Priya books Dr. Rao, uploads a report, the doctor sees it, asks her to come back, and she sees and can cancel it."""
    # 1. she uploads a report
    up = client.post("/api/records/upload", files={"file": ("blood_test.txt", b"Haemoglobin 11.2 g/dL. Ferritin low at 9 ng/mL. Advice: iron review.", "text/plain")}, headers=P1)
    assert up.status_code == 200
    # 2. she asks the bot, and the answer comes from her own upload list
    said = client.post("/api/chat", json={"message": "Have my reports been uploaded?"}, headers=P1).json()["reply"]
    assert "blood_test.txt" in said
    # 3. the doctor is told about it and can read what it says
    news = client.post("/api/assistant", json={"message": "Any new reports for me to review?"}, headers=DOCTOR).json()["reply"]
    assert "Priya" in news and "blood_test.txt" in news
    says = client.post("/api/assistant", json={"message": "What does Priya's blood test say about ferritin?"}, headers=DOCTOR).json()
    assert "9" in says["reply"] and says["citations"]
    # 4. the doctor asks her to come back
    slot = client.get("/api/doctor/slots?limit=3", headers=DOCTOR).json()[-1]
    made = client.post("/api/doctor/followups", json={"patient_id": "p1", "slot_id": slot["id"], "reason": "Review iron results"}, headers=DOCTOR)
    assert made.status_code == 200
    # 5. she can see it, and the bot knows
    visits = client.get("/api/my/visits", headers=P1).json()
    follow = [v for v in visits["upcoming"] if v["kind"] == "followup"]
    assert follow and follow[0]["reason"] == "Review iron results"
    reply = client.post("/api/chat", json={"message": "Has my doctor asked me to come back?"}, headers=P1).json()["reply"]
    assert "asked you to come back" in reply and "Review iron results" in reply
    # 6. the doctor and the front desk can see it too
    assert "follow-up" in client.post("/api/assistant", json={"message": "Which follow-ups do I have?"}, headers=DOCTOR).json()["reply"].lower()
    assert "follow-up" in client.post("/api/assistant", json={"message": "Any follow-up visits booked?"}, headers=FRONT).json()["reply"].lower()
    # 7. she cancels it herself; someone else cannot cancel hers
    assert client.post(f"/api/my/appointments/{follow[0]['id']}/cancel", headers={"X-User-Id": "p3"}).status_code == 404
    assert client.post(f"/api/my/appointments/{follow[0]['id']}/cancel", headers=P1).status_code == 200
    assert not [v for v in client.get("/api/my/visits", headers=P1).json()["upcoming"] if v["id"] == follow[0]["id"]]


def test_a_doctor_cannot_book_a_followup_outside_their_own_patients_or_slots(client):
    mine = client.get("/api/doctor/slots?limit=2", headers=DOCTOR).json()
    other_slot = client.get("/api/doctors/d2/slots?limit=1").json()[0]
    assert client.post("/api/doctor/followups", json={"patient_id": "p1", "slot_id": other_slot["id"]}, headers=DOCTOR).status_code == 404   # not their slot
    assert client.post("/api/doctor/followups", json={"patient_id": "nobody", "slot_id": mine[0]["id"]}, headers=DOCTOR).status_code == 403    # not their patient
    assert client.post("/api/doctor/followups", json={"patient_id": "p1", "slot_id": mine[0]["id"]}, headers=P1).status_code == 403            # a patient cannot do it
    first = client.post("/api/doctor/followups", json={"patient_id": "p1", "slot_id": mine[1]["id"]}, headers=DOCTOR)
    assert first.status_code == 200
    again = client.post("/api/doctor/followups", json={"patient_id": "p2", "slot_id": mine[1]["id"]}, headers=DOCTOR)
    assert again.status_code == 409                                                                                                            # slot already taken


def test_signed_notes_show_up_in_the_patients_history(client):
    from tests.test_api import sign, start_note   # reuse the helpers that draft and sign a note
    assert sign(client, start_note(client)["thread_id"], "Atorvastatin 10 mg nightly").status_code == 200
    notes = client.get("/api/my/visits", headers=P1).json()["notes"]
    assert notes and notes[0]["doctor_name"]
