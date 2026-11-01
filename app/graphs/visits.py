"""Workflow 4 (patients): MY VISITS. "When is my next appointment?", "what happened at my last visit?",
"did my doctor ask me to come back?", "are my reports uploaded?".

These are questions about the patient's OWN rows, so the answer comes straight from the tables, in plain code. A model
is never asked, so a date or a doctor's name can never be made up.
"""
import re

from app import database as db
from app.graphs.common import friendly_time, last_user_text, say
from app.state import ChatState

# Phrases that mean "this is about my visits or my uploads", checked BEFORE the records workflow so that
# "have my reports been uploaded?" is answered from the upload list, not by searching report text.
VISIT_PHRASES = (
    "my appointment", "my visit", "next appointment", "next visit", "upcoming", "when is my", "when do i", "my booking",
    "past visit", "previous visit", "last visit", "visit history", "my history", "checkup history", "check-up history",
    "follow up", "follow-up", "followup", "revisit", "come back", "reschedule", "cancel my", "cancel the appointment",
    "uploaded", "did you receive", "has my doctor", "has the doctor", "my reports on file", "which reports",
)


def is_visit_question(text: str) -> bool:
    low = text.lower()
    return any(phrase in low for phrase in VISIT_PHRASES)


def _when(visit: dict) -> str:
    return f"{visit['doctor_name']} at {visit['clinic']}, {friendly_time(visit['starts_at'])}"


def visits_answer(patient_id: str, text: str) -> str:
    low = text.lower()
    words = set(re.findall(r"[a-z]+", low))
    timeline = db.patient_visits(patient_id)
    upcoming, past = timeline["upcoming"], timeline["past"]

    if "cancel" in words or "reschedule" in words or "change" in words:
        if not upcoming:
            return "You have no upcoming visits to change. I can find you a new time. Tell me what the visit is for."
        return (f"Your next visit is with {_when(upcoming[0])}. To cancel or change it, open My visits on the website and press Cancel. "
                "Then tell me what the visit is for and I will find you a new time.")

    if "uploaded" in words or "upload" in words or "received" in words or "report" in words or "reports" in words or "documents" in words:
        docs = db.list_documents(patient_id)
        if not docs:
            return "You have not uploaded any reports yet. You can add one in the Records section, and your doctor will be able to see it."
        parts = [f"{d['filename']} ({'ready' if d['status'] == 'ready' else 'being read'})" for d in docs[:5]]
        return f"You have {len(docs)} report{'s' if len(docs) != 1 else ''} on file: {', '.join(parts)}. Doctors you have a booking with can see them."

    if "follow" in low or "revisit" in words or "back" in words:
        follow = [v for v in upcoming if v["kind"] == "followup"]
        if follow:
            v = follow[0]
            return f"Yes. Your doctor asked you to come back: {_when(v)}. Reason: {(v['reason'] or 'follow-up').strip().rstrip('.')}."
        if upcoming:
            return f"No follow-up has been scheduled by your doctor. Your next visit is with {_when(upcoming[0])}."
        return "No follow-up has been scheduled. If your doctor asked you to come back after tests, tell me and I will find a time."

    if words & {"history", "past", "previous", "last", "happened", "before"}:
        if not past:
            return "You have no past visits on record yet."
        lines = [f"{v['doctor_name']}, {v['starts_at'][:10]}" + (f" ({v['reason'].strip().rstrip('.')})" if v["reason"] else "") for v in past[:3]]
        notes = db.patient_notes(patient_id, 1)
        tail = f" At your last visit the plan was: {'; '.join(notes[0]['plan'][:2])}." if notes and notes[0]["plan"] else ""
        return f"Your recent visits: {'. '.join(lines)}.{tail}"

    if not upcoming:
        return "You have no upcoming visits. Tell me what is wrong and I will find you a doctor."
    first = f"Your next visit is with {_when(upcoming[0])}."
    reason = f" Reason: {upcoming[0]['reason'].strip().rstrip('.')}." if upcoming[0]["reason"] else ""
    more = f" You have {len(upcoming) - 1} more after that." if len(upcoming) > 1 else ""
    return first + reason + more


def visits(state: ChatState) -> dict:
    """Graph node: answer from the patient's own timeline."""
    return say(visits_answer(state["patient_id"], last_user_text(state)), outcome="done", active="")
