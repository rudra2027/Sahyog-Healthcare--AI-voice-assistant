"""General questions a patient may ask any hospital assistant: what a kind of doctor does, how booking works, what to bring.
These come from the facts file (app/knowledge.py), so they are correct and the same every time."""
import re

import pytest

from app import knowledge
from app.formulary import SPECIALTIES

P1 = {"X-User-Id": "p1"}

# (question, what the answer must contain)
GENERAL = [
    ("What does a general physician do?", r"(?i)first doctor|family doctor|wide range"),
    ("What is a general physician?", r"(?i)family doctor|first doctor"),
    ("Who is a GP?", r"(?i)family doctor|first doctor"),
    ("What is the difference between a physician and a specialist?", r"(?i)general physician|refer"),
    ("What does a cardiologist do?", r"(?i)heart"),
    ("What is an orthopaedic doctor?", r"(?i)bones|joints"),
    ("What does a dermatologist treat?", r"(?i)skin"),
    ("What does a paediatrician do?", r"(?i)children|babies"),
    ("Tell me about endocrinology", r"(?i)hormone|diabetes|thyroid"),
    ("When should I see a neurologist?", r"(?i)brain|nerves|headache"),
    ("What should I bring to my appointment?", r"(?i)photo id|reports"),
    ("How do I prepare for my visit?", r"(?i)photo id|reports|ten minutes"),
    ("How does booking work?", r"(?i)three doctors|signed in|choose"),
    ("How can I upload my reports?", r"(?i)records section|upload|pdf"),
    ("Where are your clinics?", r"(?i)baner|pune"),
    ("What are your timings?", r"(?i)9 in the morning|5:30"),
    ("Which languages do your doctors speak?", r"(?i)english|hindi"),
    ("Are my records private?", r"(?i)only you|audit"),
    ("Who are you?", r"(?i)sahyog"),
    ("What can you do?", r"(?i)find you a doctor|book"),
    ("How much does a consultation cost?", r"(?i)do not have|front desk"),
    ("Do you accept insurance?", r"(?i)do not have|front desk"),
    ("Do you offer video consultations?", r"(?i)do not have|front desk"),
    ("What is the meaning of a follow-up visit?", r"(?i)come back|follow-up"),
]


@pytest.mark.parametrize("question,pattern", GENERAL, ids=[q[:50] for q, _ in GENERAL])
def test_general_questions_are_answered_from_the_facts(client, question, pattern):
    res = client.post("/api/chat", json={"message": question}, headers=P1).json()
    assert res["outcome"] == "done"
    assert re.search(pattern, res["reply"]), f"{question!r} -> {res['reply']!r}"
    assert not res["reply"].startswith("Hello") and "I am not sure I understood" not in res["reply"]


def test_every_specialty_has_a_plain_description():
    assert set(knowledge.SPECIALTY_INFO) == set(SPECIALTIES)
    for specialty, text in knowledge.SPECIALTY_INFO.items():
        assert len(text.split()) >= 10 and not re.search(r"\d+\s?mg", text), specialty


def test_specialty_names_are_found_however_they_are_said():
    assert knowledge.specialties_mentioned("what does a cardiologist do") == ["Cardiology"]
    assert knowledge.specialties_mentioned("is a skin doctor the same as a dermatologist") == ["Dermatology"]
    assert knowledge.specialties_mentioned("do I need a GP or a physician") == ["General Medicine"]
    assert knowledge.specialties_mentioned("how do I book") == []


def test_unknown_hospital_facts_are_not_invented(client):
    reply = client.post("/api/chat", json={"message": "What is the price of a heart scan at your hospital?"}, headers=P1).json()["reply"]
    assert not re.search(r"(?i)\brs\.?\s?\d|\$\s?\d|\d+\s?(rupees|dollars)", reply)


def test_dose_and_diagnosis_questions_still_go_to_a_doctor(client):
    for question in ("What dose of paracetamol is safe for me?", "Do I have diabetes? what is diabetes treatment"):
        res = client.post("/api/chat", json={"message": question}, headers=P1).json()
        assert not re.search(r"\d+\s?mg", res["reply"]), res["reply"]
    assert client.post("/api/chat", json={"message": "What dose of paracetamol is safe for me?"}, headers=P1).json()["outcome"] in ("handoff", "done")


def test_a_question_about_a_specialty_offers_to_find_that_doctor_and_yes_does_it(client):
    ask = client.post("/api/chat", json={"message": "What does a cardiologist do?"}, headers=P1).json()
    assert "Would you like me to find you a Cardiology doctor?" in ask["reply"]
    yes = client.post("/api/chat", json={"message": "yes please", "thread_id": ask["thread_id"]}, headers=P1).json()
    assert yes["outcome"] == "waiting_for_choice" and {o["specialty"] for o in yes["options"]} == {"Cardiology"}


def test_saying_no_to_the_offer_ends_politely(client):
    ask = client.post("/api/chat", json={"message": "What does a dermatologist treat?"}, headers=P1).json()
    no = client.post("/api/chat", json={"message": "no thanks", "thread_id": ask["thread_id"]}, headers=P1).json()
    assert "No problem" in no["reply"]
    after = client.post("/api/chat", json={"message": "when is my next appointment?", "thread_id": ask["thread_id"]}, headers=P1).json()
    assert re.search(r"(?i)next visit|no upcoming", after["reply"])


def test_a_new_question_after_the_offer_is_answered_not_treated_as_yes(client):
    ask = client.post("/api/chat", json={"message": "What does a general physician do?"}, headers=P1).json()
    other = client.post("/api/chat", json={"message": "what are your timings?", "thread_id": ask["thread_id"]}, headers=P1).json()
    assert re.search(r"(?i)9 in the morning|5:30", other["reply"])


def test_symptom_and_record_questions_are_not_mistaken_for_general_ones(client):
    back = client.post("/api/chat", json={"message": "What can I do about my back pain that I have had for three weeks?"}, headers=P1).json()
    assert back["outcome"] in ("waiting_for_choice", "asked")
    report = client.post("/api/chat", json={"message": "What does my report say about cholesterol?", "about": "p2"}, headers=P1).json()
    assert re.search(r"(?i)162|238|cholesterol", report["reply"])
    named = client.post("/api/chat", json={"message": "can I consult a general physician?"}, headers=P1).json()
    assert named["outcome"] == "waiting_for_choice"          # a request to see one is still booking, not a lecture


def test_chest_pain_in_a_general_question_is_still_an_emergency(client):
    res = client.post("/api/chat", json={"message": "What does a cardiologist do for chest pain?"}, headers=P1).json()
    assert res["outcome"] == "emergency"
