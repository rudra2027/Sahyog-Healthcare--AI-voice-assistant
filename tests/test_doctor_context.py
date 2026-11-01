"""The doctor's assistant knows whose card is open, so "what does the report say?" gets an answer."""
from app import staff_assistant

DOC = {"X-User-Id": "d1"}


def ask(client, text, patient_id=None):
    return client.post("/api/assistant", json={"message": text, "patient_id": patient_id}, headers=DOC).json()


def test_report_question_uses_the_open_card_and_reads_the_report(client):
    staff_assistant.LAST_PATIENT.clear()
    out = ask(client, "what does the report say?", "p2")
    assert "Asha Deshmukh" in out["reply"] and "lipid_report.pdf" in out["reply"] and "LDL" in out["reply"] and "(Source:" in out["reply"]


def test_a_specific_value_is_answered_from_the_report_with_a_source(client):
    out = ask(client, "what is her LDL?", "p2")
    assert "162" in out["reply"] and out["citations"]


def test_without_an_open_card_the_last_patient_discussed_is_used(client):
    staff_assistant.LAST_PATIENT.clear()
    ask(client, "tell me about Asha")
    assert "lipid_report.pdf" in ask(client, "what do her reports say?")["reply"]


def test_another_doctors_patient_is_never_used_as_context(client):
    staff_assistant.LAST_PATIENT.clear()
    from app import database as db
    with db.conn() as c:                                                 # someone with a report but no booking with Dr. Rao
        c.execute("INSERT OR IGNORE INTO patient (id, name, phone, language, age) VALUES ('stranger1', 'Zed Stranger', '', 'English', 30)")
        c.execute("INSERT INTO document (patient_id, filename, status, pages, uploaded_at) VALUES ('stranger1', 'secret.pdf', 'ready', 1, '2026-11-01 10:00')")
        c.execute("INSERT INTO doc_chunk (doc_id, patient_id, page, text) VALUES (last_insert_rowid(), 'stranger1', 1, 'TOP SECRET FINDING')")
    out = ask(client, "what does the report say?", "stranger1")
    assert "SECRET" not in out["reply"] and out["outcome"] == "help"


def test_ordinary_questions_are_not_hijacked_by_the_open_card(client):
    out = ask(client, "who is my next patient?", "p2")
    assert out["reply"].startswith("Your next patient") or "no upcoming" in out["reply"]
