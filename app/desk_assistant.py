"""The front desk's assistant for MANAGING bookings: shift a time, change a doctor, cancel, block a doctor's day.

How a request flows (plain rules do the work; the model only helps when the sentence is vague):

    1. find WHO: patient names and "Dr. <name>" are matched against the real tables in code
    2. find WHAT: move / change doctor / cancel / block a doctor's day, plus the day and time, read by rules
    3. vague sentence ("push Priya to after lunch")? ask the model to read it. It sees PATIENT and DOCTOR instead of real
       names, and it can only fill in an action, a day, a time and a specialty
    4. CODE checks the result against the real slots, and builds a plan: "Move Priya from 10:00 AM to 4:00 PM with
       Dr. Rao. Say yes to confirm."
    5. NOTHING changes until the person says yes. Then the same database call the buttons use makes the change
       (one transaction, so nobody is ever double booked), and it is written to the audit trail.

So the model can misread a sentence, but it can never book, move or cancel anything by itself.
"""
import re
import time
from datetime import date, datetime, timedelta

from app import database as db
from app.formulary import SPECIALTIES, SPECIALTY_ALIASES
from app.graphs.common import friendly_time
from app.llm import get_llm
from app.memory import cache
from app.schemas import DeskCommand

PENDING: dict[str, dict] = {}        # person id -> the plan waiting for a yes (kept in memory, forgotten after two minutes)
PENDING_SECONDS = 120

MOVE_WORDS = {"move", "shift", "reschedule", "postpone", "prepone", "push", "delay", "advance", "rebook", "rearrange"}
SWITCH_WORDS = {"switch", "transfer", "reassign", "swap", "replace"}
CANCEL_WORDS = {"cancel", "remove", "delete", "drop"}
AWAY_WORDS = {"leave", "away", "coming", "absent", "unavailable", "off", "sick", "block", "closed", "holiday", "emergency"}
YES = {"yes", "yeah", "yep", "yup", "confirm", "confirmed", "proceed", "sure", "ok", "okay", "correct", "right", "done", "approved"}
NO = {"no", "nope", "stop", "dont", "never", "nevermind", "abort", "cancel", "wait", "undo", "ignore"}
READ_WORDS = {"bookings", "booking", "appointments", "appointment", "history", "previous", "past", "records", "record", "visits", "uploaded", "upload", "uploads",
              "reports", "report", "files", "documents", "slots", "show", "tell", "how", "many", "what", "when", "who", "list", "has", "have", "is", "does", "did", "any"}
TIME_HINTS = {"tomorrow", "today", "morning", "afternoon", "evening", "tonight", "kal", "aaj", "parso", "subah", "shaam", "dopahar", "raat", "later", "earlier"}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.lower().replace("don't", "dont")))


def _clock(starts_at: str) -> str:
    return datetime.strptime(starts_at, "%Y-%m-%d %H:%M").strftime("%I:%M %p").lstrip("0")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _reply(text: str, outcome: str = "done", **extra) -> dict:
    return {"reply": text, "outcome": outcome, **extra}


# ----------------------------------------------------------------------------
# Reading days and times
# ----------------------------------------------------------------------------
TIME_AMPM = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])", re.I)
TIME_24 = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
TIME_AT = re.compile(r"\b(?:at|to|for|by)\s+(\d{1,2})(?::(\d{2}))?\b(?!\s*(?:am|pm|a\.m|p\.m|th|st|nd|rd|-|/|\d))", re.I)


def parse_times(text: str) -> list[tuple[str, int]]:
    """Every time mentioned, as ('16:00', position in the sentence). '4 pm', '4:30pm', '16:30', 'noon', 'at 4' (clinic hours: 1 to 8 means pm)."""
    found: list[tuple[str, int]] = []
    taken: list[tuple[int, int]] = []
    for m in TIME_AMPM.finditer(text):
        hour, minute, half = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()[0]
        if not (1 <= hour <= 12):
            continue
        hour = hour % 12 + (12 if half == "p" else 0)
        found.append((f"{hour:02d}:{minute:02d}", m.start())); taken.append(m.span())
    for m in TIME_24.finditer(text):
        if not any(a <= m.start() < b for a, b in taken):
            found.append((f"{int(m.group(1)):02d}:{m.group(2)}", m.start())); taken.append(m.span())
    for m in re.finditer(r"\bnoon\b", text, re.I):
        found.append(("12:00", m.start()))
    for m in TIME_AT.finditer(text):
        if any(a <= m.start(1) < b for a, b in taken):
            continue
        hour = int(m.group(1))
        if 1 <= hour <= 12:
            hour = hour + 12 if hour < 9 else hour
            found.append((f"{hour:02d}:{int(m.group(2) or 0):02d}", m.start()))
    return sorted(found, key=lambda t: t[1])


def parse_day(text: str) -> str | None:
    """'today', 'tomorrow', 'day after tomorrow', 'friday', '2026-11-04' -> 'YYYY-MM-DD'. None when no day is named."""
    low, today = text.lower(), date.today()
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", low)
    if iso:
        return iso.group(1)
    if "day after tomorrow" in low:
        return (today + timedelta(days=2)).isoformat()
    if "tomorrow" in low:
        return (today + timedelta(days=1)).isoformat()
    if "today" in low or "tonight" in low:
        return today.isoformat()
    for index, name in enumerate(WEEKDAYS):
        if re.search(rf"\b{name}\b", low):
            return (today + timedelta(days=(index - today.weekday()) % 7)).isoformat()
    return None


# ----------------------------------------------------------------------------
# Finding who is meant
# ----------------------------------------------------------------------------
def doctors_named(text: str) -> tuple[list[dict], str]:
    """'Dr. Rao', 'doctor Mehta', 'Dr Meera Rao' -> the matching doctors, and the sentence with those words taken out
    (so a doctor called Rao is not mistaken for a patient called Rao)."""
    doctors, rest, hits = db.list_doctors(), text, []
    for match in re.finditer(r"\b(?:dr|doctor)\.?\s+([a-z]+)(?:\s+([a-z]+))?", text, re.I):
        first, second = match.group(1).lower(), (match.group(2) or "").lower()
        both = [d for d in doctors if {first, second} <= set(re.findall(r"[a-z]+", d["name"].lower())) and second]
        one = [d for d in doctors if first in re.findall(r"[a-z]+", d["name"].lower())[1:]]
        chosen = both or one
        typed = [first] + ([second] if both else [])            # a second word that is not part of a name ("Dr. Rao is away") is just the sentence going on
        exact = [d for d in chosen if re.findall(r"[a-z]+", d["name"].lower())[1:] == typed]       # "Dr. Rao" is Dr. Rao, not Dr. Arjun Rao
        chosen = exact or chosen
        if chosen:
            hits.extend(d for d in chosen if d not in hits)
            rest = rest.replace(match.group(0), " ")
    return hits, rest


def patients_named(text: str) -> list[dict]:
    """People with bookings whose name appears in the sentence. The best match wins; equal matches are all returned."""
    words, scored = set(re.findall(r"[a-z]+", text.lower())), []
    for patient in db.search_patients("", 300):
        parts = [p for p in re.findall(r"[a-z]+", patient["name"].lower()) if len(p) > 2]
        score = sum(p in words for p in parts)
        if score:
            scored.append((score, patient))
    best = max((s for s, _ in scored), default=0)
    return [p for s, p in scored if s == best]


def _specialty_in(text: str) -> str | None:
    low = text.lower()
    for name in SPECIALTIES:
        if name.lower() in low:
            return name
    for alias, name in SPECIALTY_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}s?\b", low):
            return name
    return None


# ----------------------------------------------------------------------------
# Plans: what would change, waiting for a yes
# ----------------------------------------------------------------------------
def _remember(user: dict, plan: dict) -> None:
    PENDING[user["id"]] = {**plan, "expires": time.time() + PENDING_SECONDS}


def _ask(user: dict, summary: str, plan: dict, **extra) -> dict:
    _remember(user, plan)
    return _reply(f"{summary} Say yes to confirm, or no to leave it.", outcome="confirm", **extra)


def _free_on(doctor_id: str, day: str) -> list[dict]:
    return [s for s in db.free_slots(doctor_id, day) if s["taken"] == 0]


def _times(slots: list[dict], limit: int = 6) -> str:
    return ", ".join(_clock(s["starts_at"]) for s in slots[:limit])


def _nearest(slots: list[dict], day: str, hhmm: str, limit: int = 3) -> list[dict]:
    target = datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M")
    return sorted(slots, key=lambda s: abs(datetime.strptime(s["starts_at"], "%Y-%m-%d %H:%M") - target))[:limit]


def _plan_move(user: dict, patient: dict, doctors: list[dict], cmd: DeskCommand, text: str) -> dict:
    first = patient["name"].split()[0]
    upcoming = db.patient_upcoming(patient["id"])
    if not upcoming:
        return _reply(f"{patient['name']} has no upcoming booking to change.")
    source_time, target_time = None, cmd.time or None
    times = parse_times(text)
    if len(times) >= 2:                                                       # "from 10 am to 4 pm": the first is today's booking, the last is the new time
        source_time, target_time = times[0][0], times[-1][0]
    elif len(times) == 1:
        if re.search(r"from\s*$", text[:times[0][1]], re.I):                # "move Priya's booking from 10 am" names the old time only
            source_time = times[0][0]
        else:
            target_time = times[0][0]
    appt = next((a for a in upcoming if source_time and a["starts_at"].endswith(source_time)), upcoming[0])
    target_day = parse_day(text) or (parse_day(cmd.day) if cmd.day else None) or appt["starts_at"][:10]
    current = f"{first}'s booking is {friendly_time(appt['starts_at'])} with {appt['doctor_name']}"

    change_doctor = cmd.action == "change_doctor"
    chosen = [d for d in doctors if d["id"] != appt["doctor_id"]] if change_doctor else []
    if change_doctor and len(chosen) > 1:
        return _reply("Which doctor? " + ", ".join(f"{d['name']} ({d['specialty']}, {d['clinic']})" for d in chosen[:4]) + ". Say the full name, for example Dr. Meera Rao.", outcome="asked")
    if change_doctor and chosen:
        candidates = chosen
    elif change_doctor:
        specialty = _specialty_in(text) or cmd.specialty or appt["specialty"]
        candidates = [d for d in db.list_doctors(specialty) if d["id"] != appt["doctor_id"]]
        candidates.sort(key=lambda d: d["clinic"] != appt["clinic"])          # the same clinic first
        if not candidates:
            return _reply(f"There is no other {specialty} doctor to move {first} to.")
    else:
        candidates = [next(d for d in db.list_doctors() if d["id"] == appt["doctor_id"])]

    if change_doctor:
        target_time = target_time or appt["starts_at"][11:16]                 # keep the same time of day unless told otherwise
    if not target_time:
        free = _free_on(candidates[0]["id"], target_day)
        label = friendly_time(f"{target_day} 00:00").split(" at")[0]
        if not free:
            return _reply(f"{candidates[0]['name']} has no free times {label}. {current}. Try another day.", outcome="asked")
        return _reply(f"{current}. {candidates[0]['name']} is free {label} at {_times(free)}. Which time should I move it to?", outcome="asked")

    for doctor in candidates:
        slot = next((s for s in _free_on(doctor["id"], target_day) if s["starts_at"].endswith(target_time)), None)
        if slot:
            what = "Change the doctor" if doctor["id"] != appt["doctor_id"] else "Move the booking"
            return _ask(user, f"{what} for {patient['name']}: from {friendly_time(appt['starts_at'])} with {appt['doctor_name']} to "
                              f"{friendly_time(slot['starts_at'])} with {doctor['name']} at {doctor['clinic']}.",
                        {"type": "move", "appointment_id": appt["id"], "slot_id": slot["id"], "patient_id": patient["id"]}, patient_id=patient["id"])
    options = []
    for doctor in candidates[:3]:
        near = _nearest(_free_on(doctor["id"], target_day), target_day, target_time)
        if near:
            options.append(f"{doctor['name']}: {_times(sorted(near, key=lambda s: s['starts_at']))}")
    label = friendly_time(f"{target_day} {target_time}").split(" at")[0]
    return _reply(f"{current}, but {_clock(f'{target_day} {target_time}')} {label} is not free. Nearest free times: " + "; ".join(options) + "." if options
                  else f"{current}. Nobody has a free time {label}. Try another day.", outcome="asked")


def _plan_cancel(user: dict, patient: dict, text: str) -> dict:
    upcoming = db.patient_upcoming(patient["id"])
    if not upcoming:
        return _reply(f"{patient['name']} has no upcoming booking to cancel.")
    times = parse_times(text)
    appt = next((a for a in upcoming if times and a["starts_at"].endswith(times[0][0])), upcoming[0])
    return _ask(user, f"Cancel {patient['name']}'s booking {friendly_time(appt['starts_at'])} with {appt['doctor_name']}? The time becomes free again.",
                {"type": "cancel", "appointment_id": appt["id"], "patient_id": patient["id"]}, patient_id=patient["id"])


def _plan_block(user: dict, doctor: dict, text: str, cmd: DeskCommand) -> dict:
    day = parse_day(text) or (parse_day(cmd.day) if cmd.day else None) or date.today().isoformat()
    times = parse_times(text)
    from_time = times[0][0] if times else (cmd.time or "00:00")
    cutoff = max(f"{day} {from_time}", _now())
    free = [s for s in db.free_slots(doctor["id"], day, 100) if s["taken"] == 0 and s["starts_at"] >= cutoff]
    booked = [a for a in db.list_appointments(100, doctor_id=doctor["id"], day=day) if a["starts_at"] >= cutoff]
    when = friendly_time(f"{day} {from_time}").split(" at")[0] + (f" from {_clock(f'{day} {from_time}')}" if times or cmd.time else "")
    if not free and not booked:
        return _reply(f"{doctor['name']} has nothing left to block {when}.")
    tail = f" {len(booked)} patient{'s are' if len(booked) != 1 else ' is'} already booked and will need moving." if booked else ""
    return _ask(user, f"Block {doctor['name']} {when}: {len(free)} free time{'s' if len(free) != 1 else ''} will close.{tail}",
                {"type": "block", "doctor_id": doctor["id"], "day": day, "from_time": from_time})


# ----------------------------------------------------------------------------
# Doing it (only after a yes)
# ----------------------------------------------------------------------------
def _execute(user: dict, plan: dict) -> dict:
    kind = plan["type"]
    if kind == "move":
        try:
            moved = db.reschedule_appointment(plan["appointment_id"], plan["slot_id"])
        except db.MoveRefused as err:
            return _reply(f"I could not do that: {err} Nothing was changed.", outcome="help")
        db.add_audit(user["id"], "frontdesk", "assistant: changed doctor" if moved["changed_doctor"] else "assistant: shifted booking", f"appointment:{plan['appointment_id']}")
        cache.delete_prefix("slots:")
        doctor = db.one("SELECT name FROM doctor WHERE id = ?", (moved["doctor_id"],))["name"]
        patient = db.get_patient(moved["patient_id"])["name"]
        return _reply(f"Done. {patient} is now {friendly_time(moved['starts_at'])} with {doctor}. The earlier time is free again.", changed=True, patient_id=moved["patient_id"])
    if kind == "cancel":
        if not db.cancel_appointment(plan["appointment_id"]):
            return _reply("That booking is already gone. Nothing was changed.", outcome="help")
        db.add_audit(user["id"], "frontdesk", "assistant: cancelled booking", f"appointment:{plan['appointment_id']}")
        cache.delete_prefix("slots:")
        return _reply("Done. The booking is cancelled and the time is free again.", changed=True, patient_id=plan["patient_id"])
    result = db.block_doctor_day(plan["doctor_id"], plan["day"], plan["from_time"])
    db.add_audit(user["id"], "frontdesk", "assistant: blocked doctor day", f"doctor:{plan['doctor_id']}:{plan['day']}")
    cache.delete_prefix("slots:")
    moving = result["to_move"]
    tail = (" Still booked, so please move them: " + "; ".join(f"{a['patient_name']} {_clock(a['starts_at'])}" for a in moving[:6])
            + ". For example: move " + moving[0]["patient_name"].split()[0] + " to tomorrow at 10 am.") if moving else ""
    return _reply(f"Done. {result['blocked']} free time{'s' if result['blocked'] != 1 else ''} closed.{tail}", changed=True)


def _answer_pending(user: dict, text: str) -> dict | None:
    """A plan is waiting: a short yes runs it, a short no drops it, anything else drops it and is handled as a new request."""
    plan = PENDING.pop(user["id"], None)
    if not plan or plan["expires"] < time.time():
        return None
    words = _words(text)
    if len(words) <= 5 and words & YES and not words & NO:
        return _execute(user, plan)
    if len(words) <= 5 and words & NO and not patients_named(text):
        return _reply("Okay, I left it as it was.")
    return None


# ----------------------------------------------------------------------------
# Reading the sentence
# ----------------------------------------------------------------------------
def _read_with_model(text: str, patient: dict | None, doctors: list[dict]) -> DeskCommand:
    """Vague sentence: let the model read it. It never sees a real name."""
    masked = text
    if patient:
        for part in re.findall(r"[A-Za-z]{3,}", patient["name"]):
            masked = re.sub(rf"\b{part}(?:'s)?\b", "PATIENT", masked, flags=re.I)
    masked = re.sub(r"\b(?:dr|doctor)\.?\s+[A-Za-z]+(?:\s+[A-Za-z]+)?", "DOCTOR", masked, flags=re.I)
    system = ("You read one instruction typed by a hospital front-desk person and fill in the fields. "
              "Names were replaced by PATIENT and DOCTOR. Only read the sentence; do not invent a day or time that was not said.")
    user = f"Today is {date.today().isoformat()} ({date.today().strftime('%A')}).\nInstruction: {masked}"
    return get_llm().structured(DeskCommand, system, user, fallback=lambda: DeskCommand(action="none"))


def _wants_change(words: set[str]) -> bool:
    return bool(words & (MOVE_WORDS | SWITCH_WORDS | CANCEL_WORDS)) or ("change" in words and bool(words & {"time", "slot", "doctor", "doctors", "physician", "booking", "appointment"}))


def handle(user: dict, text: str) -> dict | None:
    """Returns a reply when this is a management request (or the answer to one), else None so the normal questions can run."""
    if user["role"] != "frontdesk":
        if user["role"] == "compliance" and _wants_change(_words(text)) and (patients_named(text) or doctors_named(text)[0]):
            return _reply("Compliance can look but not change bookings. Ask a front-desk colleague.", outcome="help")
        return None
    answered = _answer_pending(user, text)
    if answered:
        return answered

    words = _words(text)
    doctors, without_doctor = doctors_named(text)
    patients = patients_named(without_doctor)
    block_sentence = bool(doctors) and not patients and bool(words & AWAY_WORDS) and not words & {"free", "available", "availability", "slots"}
    if block_sentence:
        if len(doctors) > 1:
            return _reply("Which doctor? " + ", ".join(d["name"] for d in doctors[:4]) + ".", outcome="asked")
        return _plan_block(user, doctors[0], text, _read_with_model(text, None, doctors) if not parse_day(text) and not parse_times(text) else DeskCommand(action="none"))

    wants_other_doctor = ("another" in words or "different" in words or "other" in words) and bool(words & {"doctor", "physician"})
    if patients and len(patients) == 1 and not (_wants_change(words) or wants_other_doctor) and (not words & READ_WORDS or words & TIME_HINTS) and len(words) >= 4:
        read = _read_with_model(text, patients[0], doctors)                      # no verb we know ("Rahul should see someone else"): let the model say what was meant
        if read.action == "none":
            return None
        if read.action == "cancel":
            return _plan_cancel(user, patients[0], text)
        if read.action == "block_day":
            return None
        return _plan_move(user, patients[0], [d for d in doctors if d["id"] != (db.patient_upcoming(patients[0]["id"]) or [{"doctor_id": ""}])[0]["doctor_id"]] if read.action == "change_doctor" else [], read, text)
    if not (_wants_change(words) or wants_other_doctor) or not patients:
        return None

    if len(patients) > 1:
        return _reply("Which patient? " + ", ".join(f"{p['name']} (age {p['age']})" if p["age"] else p["name"] for p in patients[:4]) + ". Say the full name.", outcome="asked")
    patient = patients[0]
    cancel = bool(words & CANCEL_WORDS) and not words & MOVE_WORDS
    if cancel:
        return _plan_cancel(user, patient, text)

    upcoming = db.patient_upcoming(patient["id"])
    current_doctor = upcoming[0]["doctor_id"] if upcoming else None
    other_doctors = [d for d in doctors if d["id"] != current_doctor]
    switch = bool(words & SWITCH_WORDS) or ("change" in words and bool(words & {"doctor", "doctors", "physician"})) \
        or wants_other_doctor or bool(other_doctors)
    cmd = DeskCommand(action="change_doctor" if switch else "move")
    if not parse_times(text) and not switch:                                              # no time to read by rules ("after lunch"): ask the model
        read = _read_with_model(text, patient, doctors)
        if read.action == "cancel":
            return _plan_cancel(user, patient, text)
        if read.action != "none":
            cmd = read
    cmd.specialty = cmd.specialty or ""
    return _plan_move(user, patient, other_doctors if switch else [], cmd, text)


# ----------------------------------------------------------------------------
# Questions the desk asks while managing: who is available, and a patient's record
# ----------------------------------------------------------------------------
def questions(user: dict, text: str) -> dict | None:
    """'Which doctors are free tomorrow?', 'who is free at 3 pm', 'show Priya's history'. Answered from the tables, never by the model."""
    words = _words(text)
    doctors, without_doctor = doctors_named(text)
    patients = patients_named(without_doctor)

    if patients and len(patients) == 1 and words & {"history", "previous", "past", "earlier", "record", "records", "timeline", "visits", "visited"}:
        record = db.patient_record(patients[0]["id"])
        db.add_audit(user["id"], user["role"], "assistant: patient record", f"patient:{patients[0]['id']}")
        now, name = _now(), patients[0]["name"]
        visits = [v for v in record["visits"] if v["status"] == "booked" and v["starts_at"]]
        past = [v for v in visits if v["starts_at"] < now]
        coming = [v for v in visits if v["starts_at"] >= now]
        cancelled = [v for v in record["visits"] if v["status"] == "cancelled"]
        parts = [f"{name} has {len(record['visits'])} booking{'s' if len(record['visits']) != 1 else ''} on record."]
        if coming:
            parts.append("Coming up: " + "; ".join(f"{friendly_time(v['starts_at'])} with {v['doctor_name']}" for v in coming[:3]) + ".")
        if past:
            parts.append("Earlier: " + "; ".join(f"{v['starts_at'][:10]} with {v['doctor_name']}" for v in past[-3:]) + ".")
        if cancelled:
            parts.append(f"{len(cancelled)} cancelled.")
        docs = record["documents"]
        parts.append(f"Uploaded files: {', '.join(d['filename'] for d in docs[:4])}." if docs else "No files uploaded.")
        return _reply(" ".join(parts), patient_id=patients[0]["id"])

    asks_who = words & {"available", "availability", "free", "duty", "working", "present"} and (words & {"doctors", "doctor", "who", "anyone", "which", "specialists"})
    if asks_who and not doctors:
        day = parse_day(text) or date.today().isoformat()
        times = parse_times(text)
        specialty = _specialty_in(text)
        board = db.day_board(day)["doctors"]
        now = _now()
        rows_ = []
        for d in board:
            if specialty and d["specialty"] != specialty:
                continue
            free = [s for s in d["free"] if s["starts_at"] > now and (not times or s["starts_at"].endswith(times[0][0]))]
            if free:
                rows_.append((d, free))
        label = friendly_time(f"{day} 00:00").split(" at")[0] + (f" at {_clock(f'{day} {times[0][0]}')}" if times else "")
        if not rows_:
            return _reply(f"No {specialty + ' ' if specialty else ''}doctor has a free time {label}.")
        listing = "; ".join(f"{d['name']} ({d['specialty']}, {d['clinic']}): {len(free)} free, next {_clock(free[0]['starts_at'])}" for d, free in rows_[:5])
        more = f" And {len(rows_) - 5} more." if len(rows_) > 5 else ""
        return _reply(f"{len(rows_)} doctor{'s' if len(rows_) != 1 else ''} free {label}. {listing}.{more}")
    return None
