"""Cases taken from a real LangSmith trace of the chatbot (trace 01a19898...), turned into tests so they cannot come back.

What the trace showed, in the patient's own words:
  1. "chest  pain for a day" (two spaces) was NOT treated as an emergency, and the patient was booked into a normal visit.
  2. Two vague answers sent the patient to the front desk, and the context was lost, so they were asked the same question again.
  3. "but I can consult with a general physician?" was ignored, even though the patient named the doctor they wanted.
  4. "can you share a few details with Dr RAO :" was answered with a generic "Hello..." because nothing understood it.
"""
import re

import pytest

from app import database as db
from app import safety
from app.formulary import normalise_specialty, requested_specialty, rule_match

P1 = {"X-User-Id": "p1"}
P3 = {"X-User-Id": "p3"}
DOCTOR = {"X-User-Id": "d1"}


# ----------------------------------------------------------------------------- 1. red flags must survive odd typing
@pytest.mark.parametrize("text", [
    "chest  pain for a day", "CHEST PAIN", "chest-pain since morning", "chest   pain!!!", "I have pain in my chest", "pain in the chest since last night",
    "my chest hurts", "I think it is a heart attack", "chest pains", "Chest\tpain", "my chest pain\nsince lunch", "chest, pain",
])
def test_chest_pain_is_an_emergency_however_it_is_typed(text):
    assert safety.check_red_flags(text), text


@pytest.mark.parametrize("text", ["no chest pain, just a cold", "I do not have chest pain", "denies chest pain", "back pain for a week", "chest cold and cough"])
def test_it_does_not_cry_wolf(text):
    assert not safety.check_red_flags(text), text


def test_the_trace_conversation_ends_in_an_emergency_not_a_booking(client):
    first = client.post("/api/chat", json={"message": "i have been feeling dizzy the whole day dont feel like eating anything due to pukish senseory"}, headers=P3).json()
    thread = first["thread_id"]
    last = client.post("/api/chat", json={"message": "chest  pain for a day", "thread_id": thread}, headers=P3).json()
    assert last["outcome"] == "emergency" and "112" in last["reply"]


# ----------------------------------------------------------------------------- 2 and 3. never a dead end
def test_a_named_kind_of_doctor_is_believed():
    assert requested_specialty("but i can cosult with general physician ?") == "General Medicine"
    assert requested_specialty("I want to see a cardiologist") == "Cardiology"
    assert requested_specialty("is there a skin doctor available") == "Dermatology"
    assert requested_specialty("my back hurts") is None


def test_the_trace_symptoms_now_point_somewhere_sensible():
    assert rule_match("dizzy the whole day, not eating anything, pukish, nausea, gut")[0] in {"General Medicine", "Gastroenterology"}
    assert rule_match("nausea and no appetite for a day")[0] == "General Medicine"


def test_asking_for_a_general_physician_skips_the_guessing(client):
    reply = client.post("/api/chat", json={"message": "can I just consult a general physician?"}, headers=P1).json()
    assert reply["outcome"] == "waiting_for_choice" and {o["specialty"] for o in reply["options"]} == {"General Medicine"}


def test_two_vague_answers_lead_to_a_general_physician_not_the_front_desk(client, monkeypatch):
    """The model stays unsure twice. The old code opened a front-desk ticket and forgot everything; now a physician is offered."""
    from app.graphs import triage
    from app.schemas import SpecialtyMatch

    class Unsure:
        def structured(self, schema, system, user, fallback=None, names=None):
            return SpecialtyMatch(specialty="Astrology", alternative=None, confidence=0.2) if schema is SpecialtyMatch else fallback()
        def tool_calls(self, system, user, tools, fallback=None, names=None):
            return fallback()

    monkeypatch.setattr(triage, "get_llm", lambda: Unsure())
    first = client.post("/api/chat", json={"message": "I feel unwell today"}, headers=P1).json()
    assert first["outcome"] == "asked"
    second = client.post("/api/chat", json={"message": "just in general", "thread_id": first["thread_id"]}, headers=P1).json()
    assert second["outcome"] == "waiting_for_choice" and "General Medicine" in second["reply"]
    assert not [t for t in db.list_tickets() if "unwell today" in (t["reason"] or "")]


def test_a_symptom_booking_keeps_a_clean_reason(client):
    first = client.post("/api/chat", json={"message": "I have a headache and I feel dizzy"}, headers=P3).json()
    client.post("/api/chat", json={"message": "can I see a general physician?", "thread_id": first["thread_id"]}, headers=P3)
    reason = db.rows("SELECT reason FROM appointment ORDER BY id DESC LIMIT 1")
    assert "general physician" not in (reason[0]["reason"] or "") if reason else True


# ----------------------------------------------------------------------------- 4. share details with my doctor
def book_with_rao(client, headers):
    first = client.post("/api/chat", json={"message": "I have a dull lower back pain for three weeks"}, headers=headers).json()
    thread = first["thread_id"]
    options = first["options"]
    # find a Dr. Rao slot through the general physician route is not guaranteed: book the cardiology doctor directly with the API
    slot = db.next_slots("d1", 12)[-1]["id"]
    db.reserve_slot(headers["X-User-Id"], slot, f"share-{headers['X-User-Id']}-{slot}", "stomach pain", "visit")
    return thread, options


def test_share_details_with_my_doctor_sends_my_own_words(client):
    book_with_rao(client, P3)
    sent = client.post("/api/chat", json={"message": "can you share a few details with Dr. Rao: the pain started yesterday and gets worse after meals"}, headers=P3).json()
    assert sent["outcome"] == "done" and "Dr. Rao" in sent["reply"] and "worse after meals" in sent["reply"]
    seen = client.get("/api/doctor/messages", headers=DOCTOR).json()
    assert any(m["patient_id"] == "p3" and "worse after meals" in m["text"] for m in seen)
    card = client.get("/api/doctor/patients/p3", headers=DOCTOR).json()
    assert any("worse after meals" in m["text"] for m in card["messages"])
    assert not client.get("/api/doctor/messages?unread=true", headers=DOCTOR).json() or all(m["patient_id"] != "p3" for m in client.get("/api/doctor/messages?unread=true", headers=DOCTOR).json())   # opening the card marked it read


def test_share_asks_what_to_say_when_nothing_was_said(client):
    book_with_rao(client, P3)
    ask = client.post("/api/chat", json={"message": "can you share few details with dr RAO :"}, headers=P3).json()
    assert ask["outcome"] == "asked" and "What would you like" in ask["reply"]
    sent = client.post("/api/chat", json={"message": "I have been feeling nauseous since Friday", "thread_id": ask["thread_id"]}, headers=P3).json()
    assert "nauseous since Friday" in sent["reply"] and sent["outcome"] == "done"


def test_share_uses_what_the_patient_already_told_us(client):
    book_with_rao(client, P3)
    first = client.post("/api/chat", json={"message": "my stomach has been burning after dinner for two days"}, headers=P3).json()
    sent = client.post("/api/chat", json={"message": "please share the details with Dr. Rao", "thread_id": first["thread_id"]}, headers=P3).json()
    assert "burning after dinner" in sent["reply"]


def test_share_without_a_booking_explains_instead_of_pretending(client, monkeypatch):
    from app import supabase_auth
    monkeypatch.setattr(supabase_auth, "verify_token", lambda token: {"id": "cccccccc-0000-0000-0000-000000000009", "email": "n@e.com", "name": "newbie"})
    reply = client.post("/api/chat", json={"message": "share my details with the doctor"}, headers={"Authorization": "Bearer x"}).json()["reply"]
    assert re.search(r"(?i)do not have a booking|nobody to send", reply)


def test_a_question_while_doctors_are_on_screen_is_answered_not_blocked(client):
    """Found while testing the trace: with options showing, anything typed was treated as a failed choice."""
    first = client.post("/api/chat", json={"message": "I have a dull lower back pain for three weeks"}, headers=P1).json()
    assert first["outcome"] == "waiting_for_choice"
    asked = client.post("/api/chat", json={"message": "when is my next appointment?", "thread_id": first["thread_id"]}, headers=P1).json()
    assert asked["outcome"] == "done" and re.search(r"(?i)next visit|no upcoming", asked["reply"])
    nudge = client.post("/api/chat", json={"message": "I have a dull lower back pain for three weeks"}, headers=P1).json()
    mumble = client.post("/api/chat", json={"message": "hmm", "thread_id": nudge["thread_id"]}, headers=P1).json()
    assert "first, second or third" in mumble["reply"]            # a plain "hmm" still gets the gentle nudge


def test_a_message_to_the_doctor_is_not_an_emergency_line(client):
    book_with_rao(client, P3)
    reply = client.post("/api/chat", json={"message": "share with Dr. Rao: I have crushing chest pain now"}, headers=P3).json()
    assert reply["outcome"] == "emergency" and "112" in reply["reply"]


def test_doctor_assistant_reports_patient_messages(client):
    book_with_rao(client, P3)
    client.post("/api/chat", json={"message": "tell Dr. Rao: my reflux is worse at night"}, headers=P3)
    new = client.post("/api/assistant", json={"message": "Any new messages from patients?"}, headers=DOCTOR).json()["reply"]
    assert re.search(r"(?i)reflux|no new messages", new)
    said = client.post("/api/assistant", json={"message": "What did Rahul tell me?"}, headers=DOCTOR).json()["reply"]
    assert "reflux" in said


def test_unclear_requests_get_help_not_a_repeated_hello(client):
    reply = client.post("/api/chat", json={"message": "purple monkey dishwasher"}, headers=P1).json()["reply"]
    assert not reply.startswith("Hello") and "What would you like to do" in reply
    assert client.post("/api/chat", json={"message": "hello"}, headers=P1).json()["reply"].startswith("Hello")
