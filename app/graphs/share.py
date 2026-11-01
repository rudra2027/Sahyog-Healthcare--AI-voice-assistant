"""Workflow 5 (patients): SHARE DETAILS WITH MY DOCTOR. "Can you share a few details with Dr. Rao?"

The patient's own words are saved as a message to a doctor they have a booking with. The doctor sees it on that patient's
card and can ask the assistant about it. Plain code, no model: the words sent are the patient's words, never a rewrite.

Two turns at most:
  1. "share details with Dr. Rao: my chest pain began yesterday"   -> saved at once
  2. "can you share a few details with Dr. Rao?" (nothing said)    -> we send what they already told us in this
                                                                      conversation, or ask what to say
"""
import re

from app import database as db
from app import safety
from app.graphs.common import last_user_text, say
from app.state import ChatState

SHARE_PHRASES = (
    "share", "tell dr", "tell the doctor", "tell my doctor", "send to dr", "send the doctor", "send my doctor", "inform the doctor", "inform dr",
    "let the doctor know", "let dr", "let my doctor know", "message the doctor", "message dr", "notify the doctor", "notify dr",
    "details with dr", "details to dr", "details with the doctor", "pass this on", "forward this to",
)


def is_share_request(text: str) -> bool:
    low = text.lower()
    return any(phrase in low for phrase in SHARE_PHRASES)


def _doctor_named(patient_id: str, text: str) -> dict | None:
    """The patient's own doctor named in the sentence ("Dr RAO"), else their soonest doctor."""
    mine = db.my_doctors(patient_id)
    words = set(re.findall(r"[a-z]+", text.lower()))
    for doctor in mine:
        if any(part in words for part in re.findall(r"[a-z]+", doctor["name"].lower().replace("dr.", "")) if len(part) > 2):
            return doctor
    return mine[0] if mine else None


# "tell my doctor (that) I have been dizzy": the words after the doctor are the message
TELL_PATTERN = re.compile(r"(?:tell|inform|notify|message|let)\s+(?:my\s+|the\s+)?(?:doctor|dr\.?\s*[a-z]+)(?:\s+know)?\s+(?:that\s+)?(.+)", re.I)


def _own_words(text: str) -> str:
    """The patient's message: what comes after a colon ("share with Dr. Rao: my pain began yesterday"), or after
    "tell my doctor (that)...", if there is enough of it."""
    tail = text.split(":", 1)[1].strip() if ":" in text else ""
    if len(tail.split()) < 3:
        match = TELL_PATTERN.search(text)
        tail = match.group(1).strip() if match else ""
    return tail if len(tail.split()) >= 3 else ""


def _from_conversation(state: ChatState) -> str:
    """What the patient already told us in this conversation (their own messages, not our replies), without the request itself."""
    said = [m.content for m in state.get("messages", []) if getattr(m, "type", "") == "human"][:-1]
    useful = [t.strip() for t in said if len(t.split()) >= 3 and not is_share_request(t)]
    return "; ".join(useful[-4:])


def share(state: ChatState) -> dict:
    patient_id, text = state["patient_id"], last_user_text(state)
    pending = state.get("share_doctor", "")

    if pending:                                              # we asked "what would you like to tell them?" and this is the answer
        doctor = next((d for d in db.my_doctors(patient_id) if d["id"] == pending), None)
        words = text
    else:
        doctor = _doctor_named(patient_id, text)
        # "share a few details with Dr. Rao :" ends with a colon: the patient is about to type them, so ask rather than guess
        waiting_for_words = text.strip().endswith((":", "-", "...", "\u2026"))
        words = _own_words(text) or ("" if waiting_for_words else _from_conversation(state))
    if not doctor:
        return say("You do not have a booking with a doctor yet, so there is nobody to send details to. "
                   "Tell me what is wrong and I will find you one.", outcome="done", active="", share_doctor="")

    if not words.strip():                                    # nothing to send yet: ask, and keep the conversation here
        return say(f"Of course. What would you like {doctor['name']} to know? Say it now and I will send your exact words.",
                   outcome="asked", active="share", share_doctor=doctor["id"])

    if safety.check_red_flags(words):                        # a message to a doctor is not an emergency line
        return say(safety.EMERGENCY_REPLY, outcome="emergency", active="", share_doctor="", red_flag_reasons=safety.check_red_flags(words))

    db.add_patient_message(patient_id, doctor["id"], words)
    db.add_audit(patient_id, "patient", "shared details with doctor", f"doctor:{doctor['id']}")
    shown = words if len(words) <= 160 else words[:157] + "..."
    return say(f"Done. I have sent this to {doctor['name']}: {shown}. They will see it with your booking. "
               "You can tell me more at any time.", outcome="done", active="", share_doctor="")
