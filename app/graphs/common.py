"""Small helpers and the emergency step that several graphs share."""
from datetime import date, datetime, timedelta

from app import database as db
from app import safety
from app.metrics import METRICS
from app.state import ChatState


def last_user_text(state: ChatState) -> str:
    """The newest thing the patient said."""
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") == "human":  # messages are objects after LangGraph merges them
            return message.content
    return ""


def patient_names(state: ChatState) -> list[str]:
    """Names to scrub out of any prompt, so the model never sees them."""
    people = [db.get_patient(pid) for pid in {state.get("patient_id"), state.get("about")} if pid]
    return [p["name"] for p in people if p]


def friendly_time(starts_at: str) -> str:
    """'2026-11-02 18:30' -> 'tomorrow at 6:30 PM' (easy to say out loud)."""
    moment = datetime.strptime(starts_at, "%Y-%m-%d %H:%M")
    today = date.today()
    if moment.date() == today:
        day = "today"
    elif moment.date() == today + timedelta(days=1):
        day = "tomorrow"
    else:
        day = moment.strftime("%A")
    return f"{day} at {moment.strftime('%I:%M %p').lstrip('0')}"


def say(text: str, **extra) -> dict:
    """Build a state update that speaks `text`: sets the reply and records it in the conversation."""
    return {"reply": text, "messages": [{"role": "assistant", "content": text}], **extra}


def emergency(state: ChatState) -> dict:
    """RED FLAG path. No AI involved: open a ticket, write an audit event, tell the patient to call 112.

    The reply never names a condition. We route and escalate; we never diagnose.
    """
    patient_id = state["patient_id"]
    reasons = state.get("red_flag_reasons") or ["red flag"]
    ticket_id = db.add_ticket(patient_id, "emergency", "; ".join(reasons))
    db.add_audit(patient_id, "patient", "red-flag hand-off", f"ticket:{ticket_id}")
    METRICS.count("red_flags")
    return say(safety.EMERGENCY_REPLY, outcome="emergency", active="", symptoms="", clarify_count=0, candidates=[], red_flag_reasons=reasons)
