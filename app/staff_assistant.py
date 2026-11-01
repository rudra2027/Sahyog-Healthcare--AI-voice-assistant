"""The voice assistant for STAFF (doctors and the front desk).

Patients talk to the booking assistant (the LangGraph chat). Staff ask different questions, so they get a
different assistant. It answers from the real tables with plain code, so a number, a date or a name can never
be made up by a model:

    doctor      : my schedule, my next patient, a patient's details and visit history, who uploaded a report,
                  my follow-ups, my free times, and what a patient's report says
    front desk  : bookings today, a doctor's bookings, a patient's bookings and uploads, open tickets,
                  free slots for a specialty or doctor

The ONE place a model is used is reading what a report says (the cited-answer records workflow), and a doctor
can only do that for a patient who has an active booking with them (db.treats).
"""
import re
from datetime import date, datetime, timedelta
from functools import lru_cache

from app import database as db
from app import safety
from app import desk_assistant
from app.formulary import SPECIALTIES
from app.graphs.common import friendly_time
from app.graphs.records import build_records_graph

# Words that mean "tell me what is IN the report" (needs the model, with citations).
CONTENT_WORDS = {"say", "says", "show", "shows", "result", "results", "value", "values", "level", "levels", "ldl", "hdl", "cholesterol",
                 "hba1c", "sugar", "glucose", "finding", "findings", "impression", "reading", "readings", "triglycerides", "normal", "high"}
# Words that mean "has it been uploaded / is it there" (answered from the upload list, no model).
STATUS_WORDS = {"upload", "uploads", "uploaded", "uploading", "received", "receive", "submitted", "available", "pending", "arrived", "sent", "shared"}
REPORT_KINDS = ("mri", "ct", "xray", "ecg", "lipid", "hba1c", "thyroid", "blood", "scan", "ultrasound")
KIND_NAMES = {"mri": "an MRI", "ct": "a CT", "xray": "an X-ray", "ecg": "an ECG", "lipid": "a lipid", "hba1c": "an HbA1c", "thyroid": "a thyroid",
              "blood": "a blood", "scan": "a scan", "ultrasound": "an ultrasound"}   # said aloud, so "an MRI", not "a MRI"
HISTORY_WORDS = {"history", "previous", "past", "earlier", "before", "visits", "visited", "seen"}
FOLLOW_WORDS = {"follow", "followup", "followups", "revisit", "return", "back"}
HELP = {
    "doctor": ("I can read out your schedule, tell you who your next patient is, show a patient's details and visit history, "
               "tell you which reports were uploaded, and read what a report says. For example: who is my next patient, "
               "any new reports for me, or tell me about Priya."),
    "frontdesk": ("I can tell you how many bookings there are, list a doctor's or a patient's appointments, read out open tickets, "
                  "and find the next free slot for a specialty or a doctor. For example: how many appointments today, or bookings for Priya."),
    "compliance": "I can tell you how many bookings there are today and read out open tickets.",
}
NOT_NAMES = {"today", "tomorrow", "me", "my", "you", "all", "the", "this", "that", "week", "doctor", "doctors", "patient", "patients",
             "review", "reports", "report", "cardiology", "orthopaedics", "anyone", "everyone", "him", "her", "them"}


LAST_PATIENT: dict[str, str] = {}      # doctor login id -> the patient they were just talking about (so "what does it say?" has an answer)
FOLLOW_UP_WORDS = {"report", "reports", "scan", "mri", "result", "results", "say", "says", "history", "visits", "uploaded", "her", "his", "their", "she", "he", "they", "them", "it"}
GENERIC_WORDS = {"report", "reports", "say", "says", "show", "shows", "tell", "read", "file", "files", "document", "documents", "result", "results", "uploaded",
                 "upload", "contents", "content", "inside", "summary", "summarise", "summarize", "overview", "everything", "all", "latest", "recent", "new"}


@lru_cache
def _records_graph():
    return build_records_graph().compile()


def _reply(text: str, outcome: str = "done", **extra) -> dict:
    return {"reply": text, "outcome": outcome, **extra}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _day_from(text: str) -> tuple[str, str]:
    """('2026-11-02', 'today'): which day does the question mean? Default is today."""
    if "tomorrow" in text.lower():
        return (date.today() + timedelta(days=1)).isoformat(), "tomorrow"
    return date.today().isoformat(), "today"


def _clock(starts_at: str) -> str:
    return datetime.strptime(starts_at, "%Y-%m-%d %H:%M").strftime("%I:%M %p").lstrip("0")


def _tidy(reason: str | None) -> str:
    return (reason or "").strip().rstrip(".")


def _spoken_list(appointments: list[dict], with_doctor: bool = False) -> str:
    parts = []
    for a in appointments[:6]:
        who = a["patient_name"] + (f" with {a['doctor_name']}" if with_doctor else "")
        tag = " (follow-up)" if a.get("kind") == "followup" else ""
        why = f", for {_tidy(a['reason'])[:60]}" if a.get("reason") else ""
        parts.append(f"{_clock(a['starts_at'])}, {who}{tag}{why}")
    more = f" And {len(appointments) - 6} more." if len(appointments) > 6 else ""
    return ". ".join(parts) + "." + more


def _upload_time(uploaded_at: str) -> str:
    return friendly_time(uploaded_at[:16]) if uploaded_at else "recently"


def _doc_phrase(d: dict) -> str:
    state = "ready" if d["status"] == "ready" else "still being read"
    return f"{d['filename']}, uploaded {_upload_time(d.get('uploaded_at', ''))}, {state}"


def _words(text: str) -> set[str]:
    """Lower-case words, with a few spellings made the same (x-ray, follow-up)."""
    low = text.lower().replace("x-ray", "xray").replace("follow-up", "followup").replace("follow up", "followup")
    return set(re.findall(r"[a-z0-9]+", low))


def _without_name(text: str, patient_name: str) -> str:
    """Take the patient's name out of the question ("Asha's lipid report" -> "lipid report"), so the name does not
    count as a search word that no report contains."""
    for part in re.findall(r"[A-Za-z]+", patient_name):
        text = re.sub("(?<![A-Za-z])" + re.escape(part) + "(?:'s)?(?![A-Za-z])", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _unknown_name(text: str) -> str | None:
    """'tell me about Zorro' -> 'Zorro' when the sentence names someone we could not match."""
    for match in re.finditer(r"(?:about|for|of|on)\s+([A-Za-z]{3,})", text):
        candidate = match.group(1)
        if candidate.lower() not in NOT_NAMES and candidate.lower() not in {s.lower() for s in SPECIALTIES} and candidate[0].isupper():
            return candidate
    return None


# ----------------------------------------------------------------------------
# Doctor
# ----------------------------------------------------------------------------
def _mentioned_patient(doctor_id: str, text: str) -> dict | None:
    """Which of THIS doctor's patients does the sentence name? (first name, last name or both)"""
    words = set(re.findall(r"[a-z]+", text.lower()))
    for patient in db.patients_of_doctor(doctor_id):
        if any(part in words for part in re.findall(r"[a-z]+", patient["name"].lower()) if len(part) > 2):
            return patient
    return None


def _report_overview(patient: dict, docs: list[dict]) -> str:
    """What each uploaded report says, in its own words (the start of each file), with the file and page named. No model: nothing can be invented."""
    ready = [d for d in docs if d["status"] == "ready"]
    if not ready:
        return f"{patient['name']} has no readable report on file yet." if not docs else f"{patient['name']}'s reports are still being read. Try again in a moment."
    parts, sources = [], []
    for doc in ready[:3]:
        chunks = db.rows("SELECT page, text FROM doc_chunk WHERE doc_id = ? AND suspicious = 0 ORDER BY page, id", (doc["doc_id"],))
        if chunks:
            text = re.sub(r"\s+", " ", " ".join(c["text"] for c in chunks)).strip()
            parts.append(f"{doc['filename']}: {text[:800].rstrip()}{'...' if len(text) > 800 else ''}")
            sources.append(f"{doc['filename']} page {chunks[0]['page']}" + (f"-{chunks[-1]['page']}" if chunks[-1]["page"] != chunks[0]["page"] else ""))
    if not parts:
        return f"I could not read any text in {patient['name']}'s reports."
    return f"{patient['name']}'s reports. " + " ".join(parts) + f" (Source: {'; '.join(sources)})"


def _doctor_patient(user: dict, patient: dict, text: str) -> dict:
    words, first = _words(text), patient["name"].split()[0]
    db.add_audit(user["id"], "doctor", "assistant: patient lookup", f"patient:{patient['id']}")
    docs = db.list_documents(patient["id"])
    extra = {"patient_id": patient["id"]}

    kind = next((k for k in REPORT_KINDS if k in words), None)
    asks_if_there = bool(kind) and not (words & CONTENT_WORDS) and bool(words & {"is", "there", "any", "have", "has", "got", "do", "does"}) and not (words & {"say", "says"})
    if words & {"told", "tell", "shared", "share", "message", "messages", "wrote", "said", "note", "notes", "sent"} and not (words & CONTENT_WORDS):
        said = db.messages_for_doctor(user["doctor_id"], patient_id=patient["id"], limit=3)
        if said:
            return _reply(f"{first} wrote: " + " Then: ".join(f"\"{m['text']}\"" for m in said) + ".", **extra)
        if words & {"told", "shared", "message", "messages", "wrote", "sent"}:
            return _reply(f"{first} has not sent you any message.", **extra)
    if words & STATUS_WORDS or "file" in words or asks_if_there:         # "is the MRI uploaded for Rahul?" / "is there an MRI for Priya?"
        matching = [d for d in docs if kind and kind in d["filename"].lower()] if kind else docs
        if matching:
            return _reply(f"Yes. {patient['name']} has " + "; ".join(_doc_phrase(d) for d in matching[:3]) + ".", **extra)
        if kind:
            return _reply(f"I do not see {KIND_NAMES[kind]} report for {patient['name']} yet. "
                          + (f"Reports on file: {', '.join(d['filename'] for d in docs)}." if docs else "No reports have been uploaded."), **extra)
        return _reply(f"No reports have been uploaded for {patient['name']} yet.", **extra)

    if (words & CONTENT_WORDS) or (words & {"report", "reports", "scan", "mri", "record", "records"} and words & {"what", "does", "tell", "read"}):
        if not [t for t in safety.question_terms(_without_name(text, patient["name"])) if t not in GENERIC_WORDS]:
            return _reply(_report_overview(patient, docs), **extra)           # "what does the report say?": no particular value asked for, so read out each report
        result = _records_graph().invoke({"messages": [{"role": "user", "content": _without_name(text, patient["name"])}],
                                          "patient_id": user["id"], "about": patient["id"]})
        return _reply(result.get("reply") or "I could not find that in the records on file.",
                      outcome=result.get("outcome") or "done", citations=result.get("citations", []), **extra)

    card = db.patient_card(patient["id"])
    mine = [v for v in card["visits"] if v["doctor_name"] == user["name"]]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if words & FOLLOW_WORDS:                                              # "schedule a follow-up for Priya"
        coming = [v for v in mine if v["starts_at"] >= now and v.get("kind") == "followup"]
        free = db.next_slots(user["doctor_id"], 3)
        have = f"{first} already has a follow-up {friendly_time(coming[0]['starts_at'])}. " if coming else ""
        offer = ("Your next free times are " + ", ".join(friendly_time(s["starts_at"]) for s in free) + ". Use Schedule a follow-up on "
                 f"{first}'s card to confirm one.") if free else "You have no free times coming up."
        return _reply(have + offer, **extra)

    if words & HISTORY_WORDS:                                             # "Priya's previous visits"
        past = [v for v in mine if v["starts_at"] < now]
        upcoming = [v for v in mine if v["starts_at"] >= now]
        bits = []
        if past:
            bits.append("Past visits with you: " + "; ".join(f"{v['starts_at'][:10]}" + (f", {_tidy(v['reason'])}" if v["reason"] else "") for v in past[-3:]) + ".")
        else:
            bits.append(f"{first} has no earlier visits with you.")
        if upcoming:
            bits.append(f"Next visit {friendly_time(upcoming[0]['starts_at'])}" + (" (follow-up)." if upcoming[0].get("kind") == "followup" else "."))
        notes = [n for n in db.patient_notes(patient["id"], 3) if n["doctor_name"] == user["name"]]
        if notes and notes[0]["assessment"]:
            bits.append(f"Your last note: {notes[0]['assessment'].strip().rstrip('.')}.")
        return _reply(" ".join(bits), **extra)

    upcoming = [v for v in mine if v["starts_at"] >= now]
    bits = [f"{card['name']}, {card['age']} years old." if card["age"] else f"{card['name']}."]
    if upcoming:
        bits.append(f"Next visit {friendly_time(upcoming[0]['starts_at'])}{' (follow-up)' if upcoming[0].get('kind') == 'followup' else ''}.")
        if upcoming[0]["reason"]:
            bits.append(f"Reason for visit: {_tidy(upcoming[0]['reason'])}.")
    ready = [d["filename"] for d in docs if d["status"] == "ready"]
    bits.append(f"Reports on file: {', '.join(ready)}." if ready else "No reports on file.")
    return _reply(" ".join(bits), **extra)


def _context_patient(user: dict, open_card: str | None) -> dict | None:
    """The patient the doctor means without saying a name. Only ever one of THIS doctor's own patients."""
    mine = {p["id"]: p for p in db.patients_of_doctor(user["doctor_id"])}
    for candidate in (open_card, LAST_PATIENT.get(user["id"])):
        if candidate in mine:
            return mine[candidate]
    return None


def _doctor(user: dict, text: str, context_patient: str | None = None) -> dict:
    doctor_id, low, words = user["doctor_id"], text.lower(), _words(text)
    patient = _mentioned_patient(doctor_id, text)
    if not patient and (words & FOLLOW_UP_WORDS) and not (words & {"next", "schedule", "today", "tomorrow", "free", "slots", "new", "any", "anyone", "someone", "recent", "recently", "review", "pending", "latest", "lately", "patients", "messages", "message"}):
        patient = _context_patient(user, context_patient)                  # nobody named: the open card, or the patient just discussed
    if patient:
        LAST_PATIENT[user["id"]] = patient["id"]
        return _doctor_patient(user, patient, text)

    stranger = _unknown_name(text)
    if stranger:
        return _reply(f"I do not see a patient called {stranger} with a booking with you.", outcome="help")

    if (words & {"report", "reports", "upload", "uploads", "uploaded"}) and (words & {"new", "recent", "recently", "review", "anyone", "any", "someone", "uploaded", "pending", "latest", "lately"}):
        recent = db.recent_reports_for_doctor(doctor_id)
        if not recent:
            return _reply("None of your patients have uploaded a report in the last two weeks.")
        listing = "; ".join(f"{r['patient_name']}, {r['filename']}, uploaded {_upload_time(r['uploaded_at'])}" for r in recent[:5])
        return _reply(f"Your patients uploaded {_plural(len(recent), 'report')} recently: {listing}.")

    if (words & {"message", "messages", "told", "shared", "sent"}) and (words & {"patient", "patients", "any", "new", "anyone", "someone", "me"}):
        unread = db.messages_for_doctor(doctor_id, unread_only=True)
        if not unread:
            return _reply("You have no new messages from patients.")
        listing = "; ".join(f"{m['patient_name']}: {m['text'][:90]}" for m in unread[:4])
        return _reply(f"You have {_plural(len(unread), 'new message')} from patients. {listing}.")

    if words & FOLLOW_WORDS:                                              # "which follow-ups do I have?"
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        follow = [a for a in db.list_appointments(100, doctor_id=doctor_id) if a.get("kind") == "followup" and a["starts_at"] >= now]
        if not follow:
            return _reply("You have no follow-up visits scheduled. Open a patient's card and use Schedule a follow-up.")
        return _reply(f"You have {_plural(len(follow), 'follow-up')}. {_spoken_list(follow)}")

    if words & {"free", "open", "availability", "available", "slots", "slot"}:
        free = db.next_slots(doctor_id, 5)
        return _reply("Your next free times: " + ", ".join(friendly_time(s["starts_at"]) for s in free) + "." if free else "You have no free times coming up.")

    if "next" in words:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        upcoming = [a for a in db.list_appointments(50, doctor_id=doctor_id) if a["starts_at"] >= now]
        if not upcoming:
            return _reply("You have no upcoming appointments.")
        a = upcoming[0]
        why = f" Reason: {_tidy(a['reason'])}." if a.get("reason") else ""
        return _reply(f"Your next patient is {a['patient_name']} {friendly_time(a['starts_at'])}.{why}", patient_id=a["patient_id"])

    if "patients" in words and (words & {"my", "list", "many", "all", "who"}) and not (words & {"today", "tomorrow"}):
        mine = db.patients_of_doctor(doctor_id)
        if not mine:
            return _reply("You have no patients with a booking yet.")
        return _reply(f"You have {_plural(len(mine), 'patient')} with a booking: " + ", ".join(p["name"] for p in mine[:8]) + ".")

    if words & {"schedule", "appointment", "appointments", "patients", "today", "tomorrow", "booked", "who", "many", "list", "agenda", "day"}:
        day, label = _day_from(low)
        found = db.list_appointments(50, doctor_id=doctor_id, day=day)
        if not found:
            return _reply(f"You have no appointments {label}.")
        return _reply(f"You have {_plural(len(found), 'appointment')} {label}. {_spoken_list(found)}")
    return _reply(HELP["doctor"], outcome="help")


# ----------------------------------------------------------------------------
# Front desk and compliance
# ----------------------------------------------------------------------------
def _desk(user: dict, text: str) -> dict:
    managed = desk_assistant.handle(user, text)           # move / change doctor / cancel / block a doctor's day (and the yes that follows)
    if managed:
        return managed
    asked = desk_assistant.questions(user, text)          # who is available, a patient's record
    if asked:
        return asked
    low, words = text.lower(), _words(text)
    if words & {"ticket", "tickets", "emergency", "emergencies", "queue"}:
        open_tickets = [t for t in db.list_tickets() if t["status"] == "open"]
        if "emergency" in words or "emergencies" in words:
            open_tickets = [t for t in open_tickets if t["kind"] == "emergency"] or open_tickets
        if not open_tickets:
            return _reply("There are no open tickets.")
        first = "; ".join(f"{t['kind'].replace('_', ' ')} for {t['patient_name'] or 'a patient'}" for t in open_tickets[:3])
        return _reply(f"{_plural(len(open_tickets), 'open ticket')}. Latest: {first}.")

    for person in db.rows("SELECT id, name FROM patient"):                 # a patient named in the question
        if any(part in words for part in re.findall(r"[a-z]+", person["name"].lower()) if len(part) > 2):
            mine = [a for a in db.list_appointments(300) if a["patient_id"] == person["id"]]
            if words & (STATUS_WORDS | {"report", "reports", "document", "documents", "file"}):
                docs = db.list_documents(person["id"])         # the desk sees WHAT was uploaded, never what it says
                return _reply(f"{person['name']} has {_plural(len(docs), 'report')} on file: " + ", ".join(d["filename"] for d in docs[:5]) + "." if docs
                              else f"{person['name']} has not uploaded any reports.")
            if words & {"cancel", "reschedule", "change", "move"}:
                guide = "To cancel, open Bookings, search the name and press Cancel twice. Then the patient can book a new time."
                return _reply((f"{person['name']} has {_plural(len(mine), 'booking')}: {_spoken_list(mine, with_doctor=True)} " if mine
                               else f"{person['name']} has no active bookings. ") + guide)
            if not mine:
                return _reply(f"{person['name']} has no active bookings.")
            return _reply(f"{person['name']} has {_plural(len(mine), 'booking')}. {_spoken_list(mine, with_doctor=True)}")

    wants_slot = bool(words & {"slot", "slots", "free", "available", "availability", "next", "earliest"})
    for specialty in SPECIALTIES:                                          # "next slot for cardiology"
        if specialty.lower() in low and wants_slot:
            options = []
            for doctor in db.list_doctors(specialty)[:3]:
                slots = db.next_slots(doctor["id"], 1)
                if slots:
                    options.append(f"{doctor['name']} at {doctor['clinic']}, {friendly_time(slots[0]['starts_at'])}")
            return _reply(f"Next free {specialty} slots: " + "; ".join(options) + "." if options else f"No free {specialty} slots right now.")

    for doctor in db.list_doctors():                                       # "bookings for Dr. Mehta" / "when is Dr. Rao free"
        surname = doctor["name"].replace("Dr.", "").split()[-1].lower()
        if surname in words:
            if wants_slot:
                free = db.next_slots(doctor["id"], 3)
                return _reply(f"{doctor['name']}'s next free times: " + ", ".join(friendly_time(s["starts_at"]) for s in free) + "." if free else f"{doctor['name']} has no free times coming up.")
            day, label = _day_from(low)
            found = db.list_appointments(50, doctor_id=doctor["id"], day=day)
            if not found:
                return _reply(f"{doctor['name']} has no bookings {label}.")
            return _reply(f"{doctor['name']} has {_plural(len(found), 'booking')} {label}. {_spoken_list(found)}")

    if words & FOLLOW_WORDS:
        follow = [a for a in db.list_appointments(300) if a.get("kind") == "followup"]
        return _reply(f"There {'is' if len(follow) == 1 else 'are'} {_plural(len(follow), 'follow-up visit')} booked. {_spoken_list(follow, with_doctor=True)}" if follow else "No follow-up visits are booked.")

    if words & {"appointments", "bookings", "booked", "today", "tomorrow", "many", "schedule"}:
        day, label = _day_from(low)
        found = db.list_appointments(200, day=day)
        if not found:
            return _reply(f"There are no bookings {label}.")
        return _reply(f"{_plural(len(found), 'booking')} {label}. {_spoken_list(found, with_doctor=True)}")

    stranger = _unknown_name(text)
    if stranger:
        return _reply(f"I could not find a patient called {stranger}.", outcome="help")
    return _reply(HELP.get(user["role"], HELP["frontdesk"]), outcome="help")


def answer(user: dict, text: str, patient_id: str | None = None) -> dict:
    """Entry point: pick the right assistant for the person's role. `patient_id` is the card a doctor has open, if any."""
    if user["role"] == "doctor":
        return _doctor(user, text, patient_id)
    return _desk(user, text)
