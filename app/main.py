"""The FastAPI application: every URL the UI (and the tests) can call.

Run it with:   uvicorn app.main:app --reload
Interactive API docs (try every endpoint in the browser):   http://localhost:8000/docs

Login is a DEMO stand-in: send the header  X-User-Id: p1  (patient Priya), d1 (Dr. Rao),
fd1 (front desk Meena), c1 (compliance Kavita), p3 (patient Rahul). A real system would
use OIDC tokens. The important rule is the same: WHO you are comes from the login,
never from anything you type into the chat.
"""
import hmac
import logging
import re
import threading
import time
import uuid
from datetime import date, timedelta
from contextlib import asynccontextmanager
from itertools import chain
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from app import database as db
from app import safety, staff_assistant, stt, supabase_auth
from app.config import settings
from app.formulary import SAMPLE_TRANSCRIPT
from app.graphs.chat import get_chat_graph, get_scribe_graph
from app.graphs.ingest import run_ingest
from app.llm import get_llm
from app.memory import all_lessons, approve_lesson, cache, get_prefs, get_store
from app.metrics import METRICS
from app.tracing import flush_after, flush_traces
from app.schemas import (AskRequest, AssistantRequest, FollowUpRequest, PatientQuestion, ChatRequest, ChatResponse, Citation, DoctorOption, DocumentOut, ResumeRequest,
                         ScribeResponse, ScribeReviewRequest, ScribeStartRequest, SoapNote, TTSRequest)
from app.voice import stream_speech

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
BASE_DIR = Path(__file__).resolve().parent.parent
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_AUDIO_BYTES = 3 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once when the server starts: create tables, build the graphs, log which model we use."""
    db.init_db()
    get_chat_graph()
    get_scribe_graph()
    if stt.enabled():   # load the speech model in the background so the first voice message is not slow
        threading.Thread(target=lambda: _safe_preload(), daemon=True).start()
    logging.getLogger("sahyog").info("Ready. LLM backend: %s", get_llm().name)
    yield
    flush_traces()   # on shutdown: send the last traces so LangSmith does not show finished steps as "in progress"


def _safe_preload() -> None:
    try:
        stt.load_model()
    except Exception as err:   # no model, no internet...: voice input falls back to the browser
        logging.getLogger("sahyog").warning("Speech-to-text not available: %s", err)


app = FastAPI(title="Sahyog Care Companion", version="1.0", lifespan=lifespan)


@app.middleware("http")
async def time_requests(request: Request, call_next):
    """Measure how long every API call takes. /api/metrics reports p50 and p95 from this."""
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    route = request.scope.get("route")
    if request.url.path.startswith("/api") and route:
        METRICS.record_latency(f"{request.method} {route.path}", elapsed_ms)
    response.headers["X-Process-Time-ms"] = f"{elapsed_ms:.1f}"
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"   # pages and scripts: always check for a newer version, so updates show at once
    return response


# ----------------------------------------------------------------------------
# Who is calling? (demo login)
# ----------------------------------------------------------------------------
def current_user(x_user_id: str | None = Header(default=None), authorization: str | None = Header(default=None)) -> dict:
    """Two ways in:
    1. Authorization: Bearer <Supabase token>  - a real signed-in person (always a patient).
    2. X-User-Id: p1 / d1 / ...               - the demo personas (only while ALLOW_DEMO_LOGIN=true)."""
    if authorization and authorization.lower().startswith("bearer "):
        who = supabase_auth.verify_token(authorization[7:].strip())
        if not who:
            raise HTTPException(401, "Your sign-in has expired. Please sign in again.")
        return db.ensure_supabase_user(who["id"], who["name"])
    if not settings.allow_demo_login:
        raise HTTPException(401, "Please sign in.")
    user = db.get_user(x_user_id or "p1")
    if not user:
        raise HTTPException(401, "Unknown user. Try p1, d1, fd1, c1 or p3.")
    return user


def require(*roles: str):
    """Build a dependency that only lets certain roles in, e.g. Depends(require("doctor"))."""
    def check(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(403, f"This needs the role: {' or '.join(roles)}. You are: {user['role']}.")
        return user
    return check


def own_thread(thread_id: str, user: dict) -> None:
    """A conversation belongs to the person who started it (thread ids start with the user id)."""
    if not thread_id.startswith(f"{user['id']}-"):
        raise HTTPException(403, "That conversation belongs to someone else.")


def check_subject(user: dict, about: str | None) -> str:
    """Whose records? Yourself, or someone whose consent row says yes. Otherwise 403."""
    subject = about or user["patient_id"]
    if subject != user["patient_id"] and not db.consent_allows(user["id"], subject, "records:read"):
        raise HTTPException(403, "No valid consent to read those records.")
    return subject


# ----------------------------------------------------------------------------
# Basics
# ----------------------------------------------------------------------------
@app.get("/api/health")
def health():
    """Public and boring on purpose: it only says the server is up. Details are for staff (see /api/health/details)."""
    return {"status": "ok"}


@app.get("/api/health/details")
def health_details(user: dict = Depends(require("frontdesk", "compliance"))):
    return {"status": "ok", "llm": get_llm().name, "model": settings.openrouter_model, "tts": settings.tts_backend,
            "stt": stt.backend(), "memory": cache.stats()["backend"], "tracing": settings.langsmith_tracing, "rows": db.counts()}


@app.get("/api/config")
def public_config():
    """What the browser needs to start Supabase sign-in. The publishable key is meant to be public."""
    return {"supabase_url": settings.supabase_url, "supabase_key": settings.supabase_publishable_key,
            "auth_enabled": supabase_auth.enabled(), "demo_login": settings.allow_demo_login,
            "staff_signup": {"doctor": bool(settings.doctor_signup_code), "frontdesk": bool(settings.frontdesk_signup_code)},
            "stt": "server" if stt.enabled() else "browser"}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    """Who does the server think I am? The pages use the role to decide which portal to show."""
    return {"id": user["id"], "name": user["name"], "role": user["role"], "doctor_id": user.get("doctor_id")}


_CLAIM_TRIES: dict[str, int] = {}   # wrong sign-up codes per person, so a code cannot be guessed


class ClaimRole(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    role: str = Field(max_length=20)
    code: str = Field(default="", max_length=100)
    specialty: str = Field(default="", max_length=60)
    clinic: str = Field(default="", max_length=60)


@app.post("/api/me/role")
def claim_role(body: ClaimRole, user: dict = Depends(require("patient"))):
    """The registration form's "I am a" dropdown. Doctor and front desk need the hospital's sign-up code,
    which is checked here on the server (the browser only sends what the person typed)."""
    expected = {"doctor": settings.doctor_signup_code, "frontdesk": settings.frontdesk_signup_code}.get(body.role)
    if not expected:
        db.add_audit(user["id"], "patient", f"staff role refused: {body.role} sign-up is closed", f"user:{user['id']}")
        raise HTTPException(403, "Staff registration is not open for that role. Ask the hospital administrator.")
    if _CLAIM_TRIES.get(user["id"], 0) >= 5:
        raise HTTPException(429, "Too many wrong codes. Ask the hospital administrator.")
    if not hmac.compare_digest(body.code.encode(), expected.encode()):
        _CLAIM_TRIES[user["id"]] = _CLAIM_TRIES.get(user["id"], 0) + 1
        db.add_audit(user["id"], "patient", f"staff role refused: wrong {body.role} code", f"user:{user['id']}")
        raise HTTPException(403, "That staff code is not right.")
    try:
        staff = db.claim_staff_role(user["id"], body.role, body.specialty, body.clinic)
    except ValueError as err:
        db.add_audit(user["id"], "patient", f"staff role refused: {err}", f"user:{user['id']}")
        raise HTTPException(400, str(err))
    db.add_audit(user["id"], staff["role"], f"registered as {staff['role']}", f"user:{user['id']}")
    return {"id": staff["id"], "name": staff["name"], "role": staff["role"], "doctor_id": staff.get("doctor_id")}


@app.get("/api/personas")
def personas():
    """The demo people you can 'log in' as (only when demo login is switched on)."""
    if not settings.allow_demo_login:
        raise HTTPException(404, "Not found")
    return db.rows("SELECT id, name, role FROM app_user ORDER BY role, id")


@app.get("/api/doctors")
def doctors(specialty: str = "", clinic: str = ""):
    return db.list_doctors(specialty, clinic)


@app.get("/api/doctors/{doctor_id}/slots")
def doctor_slots(doctor_id: str, limit: int = 5):
    if not db.one("SELECT id FROM doctor WHERE id = ?", (doctor_id,)):
        raise HTTPException(404, "No such doctor")
    return db.next_slots(doctor_id, limit=min(limit, 20))


# ----------------------------------------------------------------------------
# Chat: find a doctor, book, ask about records (one door, the router decides)
# ----------------------------------------------------------------------------
ORDINALS = {"first": 0, "1": 0, "one": 0, "second": 1, "2": 1, "two": 1, "third": 2, "3": 2, "three": 2}


def pending_options(graph, config) -> list[dict] | None:
    """If the conversation is paused waiting for a choice, return the options it showed."""
    snapshot = graph.get_state(config)
    if not snapshot.next:
        return None
    for task in snapshot.tasks:
        for interrupt in task.interrupts:
            if isinstance(interrupt.value, dict) and "options" in interrupt.value:
                return interrupt.value["options"]
    return None


def parse_choice(text: str, options: list[dict]) -> int | None:
    """Understand 'the second one' or 'Dr. Mehta'. Returns a slot id, or None if unclear."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    for word in words:
        if word in ORDINALS and ORDINALS[word] < len(options):
            return options[ORDINALS[word]]["slot_id"]
    for option in options:
        if any(part.lower() in words for part in option["name"].replace("Dr.", "").split()):
            return option["slot_id"]
    return None


def changes_the_subject(text: str) -> bool:
    """While doctor options are on screen: is this a different request, not a choice? Red flags always count."""
    from app.formulary import requested_specialty
    from app.graphs.share import is_share_request
    from app.graphs.visits import is_visit_question
    low = text.lower()
    return bool(safety.check_red_flags(text) or is_share_request(text) or is_visit_question(text) or requested_specialty(text)
                or any(phrase in low for phrase in ("never mind", "nevermind", "cancel", "stop", "start over", "forget it", "something else", "no thanks"))
                or any(word in low for word in ("report", "result", "mri")))


def to_response(result: dict, thread_id: str, started: float) -> ChatResponse:
    """Turn a graph result into the JSON the app understands."""
    if result.get("__interrupt__"):  # the graph paused: waiting for the patient to choose
        payload = result["__interrupt__"][0].value
        return ChatResponse(thread_id=thread_id, reply=payload["reply"], outcome="waiting_for_choice",
                            options=[DoctorOption(**o) for o in payload["options"]],
                            latency_ms=int((time.perf_counter() - started) * 1000))
    return ChatResponse(thread_id=thread_id, reply=result.get("reply", ""), outcome=result.get("outcome") or "done",
                        citations=[Citation(**c) for c in result.get("citations", [])],
                        latency_ms=int((time.perf_counter() - started) * 1000))


@app.post("/api/chat", response_model=ChatResponse)
def chat(body: ChatRequest, user: dict = Depends(require("patient")), _flush: None = Depends(flush_after)):
    started = time.perf_counter()
    thread_id = body.thread_id or f"{user['id']}-{uuid.uuid4().hex[:8]}"
    own_thread(thread_id, user)
    graph, config = get_chat_graph(), {"configurable": {"thread_id": thread_id}}
    subject = check_subject(user, body.about)

    options = pending_options(graph, config)
    if options:  # the graph is paused: treat this message as the patient's choice ("the second one")
        slot_id = parse_choice(body.message, options)
        if slot_id is not None:
            return to_response(graph.invoke(Command(resume=slot_id), config), thread_id, started)
        if changes_the_subject(body.message):
            # They did not choose: they asked something else (or typed a warning sign). Never trap them in the choice.
            graph.invoke(Command(resume="cancel"), config)
        else:
            return ChatResponse(thread_id=thread_id, reply="Please say first, second or third, or tap one of the options.",
                                outcome="waiting_for_choice", options=[DoctorOption(**o) for o in options])

    result = graph.invoke({"messages": [{"role": "user", "content": body.message}],
                           "patient_id": user["patient_id"], "about": subject}, config)
    METRICS.record_latency("chat_turn", (time.perf_counter() - started) * 1000)
    return to_response(result, thread_id, started)


@app.post("/api/chat/resume", response_model=ChatResponse)
def chat_resume(body: ResumeRequest, user: dict = Depends(require("patient")), _flush: None = Depends(flush_after)):
    """The patient tapped an option. Resume the paused graph from its saved checkpoint."""
    started = time.perf_counter()
    own_thread(body.thread_id, user)
    graph, config = get_chat_graph(), {"configurable": {"thread_id": body.thread_id}}
    if not pending_options(graph, config):
        raise HTTPException(409, "Nothing to resume: this conversation is not waiting for a choice.")
    return to_response(graph.invoke(Command(resume=body.slot_id), config), body.thread_id, started)


# ----------------------------------------------------------------------------
# Records
# ----------------------------------------------------------------------------
@app.post("/api/records/upload", response_model=DocumentOut)
async def upload(background: BackgroundTasks, file: UploadFile = File(...), user: dict = Depends(require("patient"))):
    """Store a report. Reading it happens in the background; poll /api/records/status/{id}."""
    name = file.filename or "report.txt"
    if not name.lower().endswith((".pdf", ".txt")):
        raise HTTPException(415, "Please upload a .pdf or .txt report.")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 5 MB.")
    doc_id = db.add_document(user["patient_id"], name, "queued")
    background.add_task(run_ingest, doc_id, user["patient_id"], name, data)
    return DocumentOut(doc_id=doc_id, filename=name, status="queued")


@app.get("/api/records/documents")
def documents(about: str | None = None, user: dict = Depends(require("patient"))):
    subject = check_subject(user, about)
    db.add_audit(user["id"], "patient", "list documents", f"patient:{subject}")
    return db.list_documents(subject)


@app.get("/api/records/status/{doc_id}", response_model=DocumentOut)
def document_status(doc_id: int, user: dict = Depends(require("patient"))):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "No such document")
    check_subject(user, doc["patient_id"])  # same consent rule as reading it
    return DocumentOut(**{k: doc[k] for k in ("doc_id", "filename", "status", "pages")})


@app.post("/api/records/ask", response_model=ChatResponse)
def ask_records(body: AskRequest, user: dict = Depends(require("patient")), _flush: None = Depends(flush_after)):
    """Ask one question about a record (a fresh conversation, straight to the records graph)."""
    started = time.perf_counter()
    subject = check_subject(user, body.about) if body.about else user["patient_id"]
    thread_id = f"{user['id']}-ask-{uuid.uuid4().hex[:8]}"
    result = get_chat_graph().invoke(
        {"messages": [{"role": "user", "content": body.question}], "patient_id": user["patient_id"], "about": subject,
         "active": "records"}, {"configurable": {"thread_id": thread_id}})
    return to_response(result, thread_id, started)


# ----------------------------------------------------------------------------
# Doctor scribe
# ----------------------------------------------------------------------------
def scribe_response(result: dict, thread_id: str) -> ScribeResponse:
    if result.get("__interrupt__"):  # paused for the doctor's review
        payload = result["__interrupt__"][0].value
        return ScribeResponse(thread_id=thread_id, status="waiting_for_review", draft=SoapNote(**payload["draft"]),
                              stock=payload["stock"], suggestions=payload["suggestions"])
    note = {**(result.get("note") or {}), "learned": result.get("lessons", [])}
    return ScribeResponse(thread_id=thread_id, status="signed", note=note)


@app.get("/api/scribe/sample")
def scribe_sample():
    return {"transcript": SAMPLE_TRANSCRIPT}


@app.post("/api/scribe/start", response_model=ScribeResponse)
def scribe_start(body: ScribeStartRequest, user: dict = Depends(require("doctor")), _flush: None = Depends(flush_after)):
    if not db.get_patient(body.patient_id):
        raise HTTPException(404, "No such patient")
    if not db.treats(user["id"], body.patient_id):
        raise HTTPException(403, "You can only write notes for patients who have a booking with you.")
    thread_id = f"{user['id']}-scribe-{uuid.uuid4().hex[:8]}"
    db.add_audit(user["id"], "doctor", "consultation scribe", f"patient:{body.patient_id}")
    result = get_scribe_graph().invoke(
        {"transcript": body.transcript, "patient_id": body.patient_id, "doctor_id": user["id"]},
        {"configurable": {"thread_id": thread_id}})
    return scribe_response(result, thread_id)


@app.post("/api/scribe/review", response_model=ScribeResponse)
def scribe_review(body: ScribeReviewRequest, user: dict = Depends(require("doctor")), _flush: None = Depends(flush_after)):
    own_thread(body.thread_id, user)
    graph, config = get_scribe_graph(), {"configurable": {"thread_id": body.thread_id}}
    if not graph.get_state(config).next:
        raise HTTPException(409, "This note is not waiting for review.")
    if body.action == "sign" and body.plan and any("[" in line for line in body.plan):
        raise HTTPException(422, "Resolve every [bracketed] item before signing. A note with gaps cannot be signed.")
    decision = {"action": body.action, "plan": body.plan, "comments": body.comments}
    return scribe_response(graph.invoke(Command(resume=decision), config), body.thread_id)


@app.get("/api/lessons")
def lessons(user: dict = Depends(require("doctor"))):
    """What the scribe has learned about THIS doctor (self-learning memory)."""
    return all_lessons(get_store(), user["id"])


@app.post("/api/lessons/{drug_class}/approve")
def approve(drug_class: str, user: dict = Depends(require("doctor"))):
    """A clinical lesson only turns on when a doctor says so."""
    lesson = approve_lesson(get_store(), user["id"], f"drug:{drug_class}")
    if not lesson:
        raise HTTPException(404, "No such lesson")
    db.add_audit(user["id"], "doctor", "approved lesson", f"lesson:{drug_class}")
    return lesson


# ----------------------------------------------------------------------------
# Doctor portal: my schedule, my patients, their reports
# ----------------------------------------------------------------------------
def my_patient(user: dict, patient_id: str) -> None:
    """A doctor may only open patients who have an active booking with them."""
    if not db.treats(user["id"], patient_id):
        raise HTTPException(403, "This patient does not have a booking with you.")


@app.get("/api/doctor/appointments")
def doctor_appointments(day: str = "", user: dict = Depends(require("doctor"))):
    """My bookings in time order. day = YYYY-MM-DD, 'today', 'tomorrow', or empty for everything coming up."""
    if day == "today":
        day = date.today().isoformat()
    elif day == "tomorrow":
        day = (date.today() + timedelta(days=1)).isoformat()
    return db.list_appointments(200, doctor_id=user["doctor_id"], day=day)


@app.get("/api/doctor/patients")
def doctor_patients(user: dict = Depends(require("doctor"))):
    return db.patients_of_doctor(user["doctor_id"])


@app.get("/api/doctor/patients/{patient_id}")
def doctor_patient(patient_id: str, user: dict = Depends(require("doctor"))):
    my_patient(user, patient_id)
    db.add_audit(user["id"], "doctor", "opened patient card", f"patient:{patient_id}")
    card = db.patient_card(patient_id)
    card["messages"] = db.messages_for_doctor(user["doctor_id"], patient_id=patient_id)
    db.mark_messages_read(user["doctor_id"], patient_id)             # opening the card counts as reading them
    return card


@app.post("/api/doctor/patients/{patient_id}/ask")
def doctor_ask_records(patient_id: str, body: PatientQuestion, user: dict = Depends(require("doctor")), _flush: None = Depends(flush_after)):
    """Ask a question about one patient's reports. Answers are cited, and only come from that patient's records."""
    my_patient(user, patient_id)
    result = staff_assistant._records_graph().invoke(
        {"messages": [{"role": "user", "content": body.question}], "patient_id": user["id"], "about": patient_id})
    return {"reply": result.get("reply", ""), "outcome": result.get("outcome") or "done", "citations": result.get("citations", [])}


@app.get("/api/doctor/reports")
def doctor_reports(user: dict = Depends(require("doctor"))):
    """Reports my patients uploaded in the last two weeks."""
    return db.recent_reports_for_doctor(user["doctor_id"])


@app.get("/api/doctor/messages")
def doctor_messages(unread: bool = False, user: dict = Depends(require("doctor"))):
    """What my patients asked the assistant to pass on to me."""
    return db.messages_for_doctor(user["doctor_id"], unread_only=unread)


@app.get("/api/doctor/slots")
def doctor_free_slots(limit: int = 12, user: dict = Depends(require("doctor"))):
    """My next free times (to offer a follow-up)."""
    return db.next_slots(user["doctor_id"], limit=min(limit, 40))


@app.post("/api/doctor/followups")
def doctor_followup(body: FollowUpRequest, user: dict = Depends(require("doctor"))):
    """Ask a patient to come back, for example after tests. It becomes a normal booking marked as a follow-up."""
    my_patient(user, body.patient_id)
    slot = db.get_slot(body.slot_id)
    if not slot or slot["doctor_id"] != user["doctor_id"]:
        raise HTTPException(404, "That time is not one of your slots.")
    try:
        appointment = db.reserve_slot(body.patient_id, body.slot_id, f"followup:{user['id']}:{body.patient_id}:{body.slot_id}",
                                      reason=body.reason, kind="followup")
    except db.SlotTaken:
        raise HTTPException(409, "That time was just taken. Pick another.")
    cache.delete_prefix("slots:")
    db.add_audit(user["id"], "doctor", "scheduled follow-up", f"patient:{body.patient_id}")
    return {**appointment, "starts_at": slot["starts_at"]}


@app.post("/api/assistant")
def assistant(body: AssistantRequest, user: dict = Depends(require("doctor", "frontdesk", "compliance")), _flush: None = Depends(flush_after)):
    """The voice assistant for staff: schedule, patients, reports (doctor) or bookings and tickets (front desk)."""
    started = time.perf_counter()
    result = staff_assistant.answer(user, body.message, body.patient_id)
    result["latency_ms"] = int((time.perf_counter() - started) * 1000)
    return result


# ----------------------------------------------------------------------------
# Front desk and compliance
# ----------------------------------------------------------------------------
@app.get("/api/appointments")
def appointments(day: str = "", doctor_id: str = "", user: dict = Depends(require("frontdesk", "compliance"))):
    return db.list_appointments(200, doctor_id=doctor_id, day=day)


@app.post("/api/appointments/{appointment_id}/cancel")
def cancel(appointment_id: int, user: dict = Depends(require("frontdesk"))):
    """Front-desk override: the assistant never has the last word on a booking."""
    if not db.cancel_appointment(appointment_id):
        raise HTTPException(404, "No active booking with that id")
    db.add_audit(user["id"], "frontdesk", "cancelled booking", f"appointment:{appointment_id}")
    cache.delete_prefix("slots:")  # freed slots must show up again
    return {"cancelled": appointment_id}


class MoveRequest(BaseModel):
    slot_id: int


class BlockDayRequest(BaseModel):
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    from_time: str = Field(default="00:00", pattern=r"^\d{2}:\d{2}$")


@app.get("/api/desk/board")
def desk_board(day: str = "", user: dict = Depends(require("frontdesk", "compliance"))):
    """One day at a glance: every doctor with their bookings, free times and blocked times."""
    return db.day_board(day or date.today().isoformat())


@app.get("/api/desk/doctors/{doctor_id}/slots")
def desk_slots(doctor_id: str, day: str = "", user: dict = Depends(require("frontdesk"))):
    """The free and blocked times of one doctor, used to pick where to move a booking."""
    if not db.one("SELECT id FROM doctor WHERE id = ?", (doctor_id,)):
        raise HTTPException(404, "No such doctor")
    return db.free_slots(doctor_id, day)


@app.get("/api/desk/patients")
def desk_patients(q: str = "", user: dict = Depends(require("frontdesk", "compliance"))):
    return db.search_patients(q[:60])


@app.get("/api/desk/patients/{patient_id}")
def desk_patient(patient_id: str, user: dict = Depends(require("frontdesk", "compliance"))):
    """A patient's bookings (all of them, as a table), the files they uploaded (names only) and their tickets."""
    record = db.patient_record(patient_id)
    if not record:
        raise HTTPException(404, "No such patient")
    db.add_audit(user["id"], user["role"], "viewed patient record", f"patient:{patient_id}")
    return record


@app.post("/api/appointments/{appointment_id}/reschedule")
def reschedule(appointment_id: int, body: MoveRequest, user: dict = Depends(require("frontdesk"))):
    """Shift the time (same doctor) or change the doctor (a slot of another doctor). The old time is freed."""
    try:
        moved = db.reschedule_appointment(appointment_id, body.slot_id)
    except db.MoveRefused as err:
        raise HTTPException(409, str(err))
    db.add_audit(user["id"], "frontdesk", "changed doctor" if moved["changed_doctor"] else "shifted booking", f"appointment:{appointment_id}")
    cache.delete_prefix("slots:")
    return moved


@app.post("/api/desk/slots/{slot_id}/{action}")
def block_slot(slot_id: int, action: str, user: dict = Depends(require("frontdesk"))):
    """Block a free slot (doctor away) or open it again."""
    if action not in ("block", "unblock"):
        raise HTTPException(404, "Not found")
    if not db.set_slot_blocked(slot_id, action == "block"):
        raise HTTPException(409, "Only a free slot can be blocked, and only a blocked one can be opened." if action == "block" else "That slot is not blocked.")
    db.add_audit(user["id"], "frontdesk", f"{action}ed slot", f"slot:{slot_id}")
    cache.delete_prefix("slots:")
    return {"slot_id": slot_id, "action": action}


@app.post("/api/desk/doctors/{doctor_id}/block-day")
def block_day(doctor_id: str, body: BlockDayRequest, user: dict = Depends(require("frontdesk"))):
    """The doctor is away: block the free slots of that day. Booked patients are listed so the desk can move them."""
    if not db.one("SELECT id FROM doctor WHERE id = ?", (doctor_id,)):
        raise HTTPException(404, "No such doctor")
    result = db.block_doctor_day(doctor_id, body.day, body.from_time)
    db.add_audit(user["id"], "frontdesk", "blocked doctor day", f"doctor:{doctor_id}:{body.day}")
    cache.delete_prefix("slots:")
    return result


@app.get("/api/tickets")
def tickets(user: dict = Depends(require("frontdesk", "doctor", "compliance"))):
    return db.list_tickets()


@app.post("/api/tickets/{ticket_id}/resolve")
def resolve_ticket(ticket_id: int, user: dict = Depends(require("frontdesk"))):
    if not db.resolve_ticket(ticket_id):
        raise HTTPException(404, "No open ticket with that id")
    db.add_audit(user["id"], "frontdesk", "resolved ticket", f"ticket:{ticket_id}")
    return {"resolved": ticket_id}


@app.get("/api/audit")
def audit(record: str = "", user: dict = Depends(require("compliance"))):
    """Compliance's one question, 'who saw this record?', answered with one query."""
    return db.list_audit(record)


@app.get("/api/my/visits")
def my_visits(user: dict = Depends(require("patient"))):
    """The patient's own timeline: coming up, already happened, and what the doctor wrote down."""
    return {**db.patient_visits(user["patient_id"]), "notes": db.patient_notes(user["patient_id"])}


@app.post("/api/my/appointments/{appointment_id}/cancel")
def cancel_my_visit(appointment_id: int, user: dict = Depends(require("patient"))):
    """A patient may cancel their OWN booking (never someone else's)."""
    if not db.cancel_appointment(appointment_id, patient_id=user["patient_id"]):
        raise HTTPException(404, "No active booking of yours with that id")
    db.add_audit(user["id"], "patient", "cancelled own booking", f"appointment:{appointment_id}")
    cache.delete_prefix("slots:")
    return {"cancelled": appointment_id}


@app.get("/api/memory/prefs")
def my_prefs(user: dict = Depends(require("patient"))):
    """What the assistant remembers about me (patients can always see this)."""
    return get_prefs(get_store(), user["patient_id"])


@app.get("/api/metrics")
def metrics(user: dict = Depends(require("frontdesk", "compliance"))):
    """Numbers for the dashboard: latency percentiles, cache hit rate, AI draft quality."""
    return {**METRICS.snapshot(), "cache": cache.stats(), "notes": db.average_edit_distance(),
            "llm": {"backend": get_llm().name, "model": settings.openrouter_model}}


# ----------------------------------------------------------------------------
# Voice
# ----------------------------------------------------------------------------
@app.post("/api/stt")
async def speech_to_text(file: UploadFile = File(...), user: dict = Depends(current_user)):
    """The browser uploads a short voice recording; we answer with the words. Runs on this machine."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty recording.")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Recording is too long. Keep it under about a minute.")
    started = time.perf_counter()
    try:
        text = await run_in_threadpool(stt.transcribe, data)   # the model is CPU-heavy, so keep it off the event loop
    except stt.SttUnavailable as err:
        raise HTTPException(503, f"Speech-to-text is not available: {err}")
    except Exception as err:
        logging.getLogger("sahyog").warning("Transcription failed: %s", err)
        raise HTTPException(422, "Could not read that recording.")
    METRICS.record_latency("stt", (time.perf_counter() - started) * 1000)
    return {"text": text}


@app.post("/api/tts")
async def tts(body: TTSRequest):
    """Speak a sentence. Returns streaming audio so playback starts quickly."""
    audio = stream_speech(body.text, body.agent)
    try:
        first = await audio.__anext__()  # fetch the first chunk now so errors become a clean 503
    except StopAsyncIteration:
        raise HTTPException(503, "The voice engine returned no audio.")
    except Exception as err:
        raise HTTPException(503, f"The voice engine is not available: {err}")

    async def body_stream():
        yield first
        async for chunk in audio:
            yield chunk

    return StreamingResponse(body_stream(), media_type="audio/mpeg")


# ----------------------------------------------------------------------------
# The website
# ----------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/", include_in_schema=False)
def home():
    """The patient site: find a doctor, book, ask about my reports."""
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/doctor", include_in_schema=False)
def doctor_portal():
    """The doctor portal. The page itself is just a shell: every number on it comes from API calls that check the role."""
    return FileResponse(BASE_DIR / "static" / "doctor.html")


@app.get("/desk", include_in_schema=False)
def desk_portal():
    """The front desk portal (same idea: the data calls enforce the role)."""
    return FileResponse(BASE_DIR / "static" / "desk.html")
