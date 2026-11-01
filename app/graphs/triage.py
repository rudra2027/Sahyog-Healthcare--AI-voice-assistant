"""Workflow 1: FIND MY DOCTOR.

    intake -> red_flag_gate -> match -> shortlist -> present -> pick (PAUSE) -> book
                  |              |
               emergency      ask (one clarifying question) / handoff to the front desk

Patterns used here:
* GATE          red_flag_gate is plain code. The model is never asked about emergencies.
* LOOP          if the model is unsure, ask ONE question, then try again next turn.
* PARALLEL      slots and no-show risk are looked up for every doctor at the same time.
* INTERRUPT     `pick` pauses the whole run until the patient chooses. The state is saved
                by the checkpointer, so no server has to keep anything in memory.

A node returns either a dict (the fields to change) or a `Command`, which says
"change these fields AND go to that node next".
"""
import logging
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Command, interrupt

from app import database as db
from app import safety
from app.formulary import SPECIALTIES, normalise_specialty, requested_specialty, rule_match
from app.graphs.common import emergency, friendly_time, last_user_text, patient_names, say
from app.llm import get_llm
from app.memory import get_prefs, set_pref
from app.metrics import METRICS
from app.schemas import DoctorOption, SpecialtyMatch
from app.state import ChatState
from app.tools import assess_no_show_risk, invalidate_slots, run_tools_parallel, search_doctors

log = logging.getLogger(__name__)
CONFIDENCE_THRESHOLD = 0.5   # a trace showed 0.55 ("General Medicine", clearly right) being treated as unsure, so people were asked again and again

MATCH_SYSTEM = (
    "You route patients to a medical specialty. You never diagnose. "
    "Choose ONLY from this exact list: " + ", ".join(SPECIALTIES) + ". "
    "Return the best specialty, an optional alternative (also from the list), and a confidence from 0 to 1. "
    "Use a confidence below 0.6 when the symptoms are vague."
)
TOOL_SYSTEM = "Use the search_doctors tool to find doctors for the requested specialty."


def intake(state: ChatState) -> dict:
    """Collect what the patient said. Symptoms add up across turns (after a clarifying question)."""
    text = last_user_text(state)
    asked_only = (requested_specialty(text) and rule_match(text)[2] <= 0.3) or (state.get("requested") and text.lower().strip(" !.") in ("yes", "yeah", "ok", "okay", "sure", "please", "haan"))
    return {
        "symptoms": (state.get("symptoms", "") if (asked_only and state.get("symptoms")) else f"{state.get('symptoms', '')} {text}").strip(),
        "requested": requested_specialty(text) or state.get("requested", ""),
        "age": safety.extract_age(text) or state.get("age"),
        "active": "triage",  # remember that triage owns this conversation
    }


def red_flag_gate(state: ChatState) -> Command[Literal["emergency", "match"]]:
    """GATE: rules decide, not the model."""
    reasons = safety.check_red_flags(state["symptoms"])
    if reasons:
        return Command(goto="emergency", update={"red_flag_reasons": reasons})
    return Command(goto="match")


def match(state: ChatState) -> Command[Literal["shortlist", "ask", "handoff"]]:
    """The only fuzzy step: which specialty? The model must answer in the SpecialtyMatch shape."""
    symptoms = state["symptoms"]
    if state.get("requested"):                  # the patient named the kind of doctor: believe them (the red-flag gate already ran)
        return Command(goto="shortlist", update={"specialty": state["requested"], "alternative": "", "confidence": 1.0, "requested": ""})
    result = get_llm().structured(
        SpecialtyMatch, MATCH_SYSTEM, f"Symptoms: {symptoms}\nPatient age: {state.get('age')}",
        # if the model fails, keyword rules answer instead
        fallback=lambda: SpecialtyMatch(**dict(zip(("specialty", "alternative", "confidence"), rule_match(symptoms)))),
        names=patient_names(state))
    # The model may spell a specialty differently ("Orthopedics"). Snap it to our own list, or fall back to the keyword rules.
    specialty = normalise_specialty(result.specialty) or normalise_specialty(result.alternative) or rule_match(symptoms)[0]
    alternative = normalise_specialty(result.alternative) or ""
    update = {"specialty": specialty, "alternative": alternative if alternative != specialty else "", "confidence": result.confidence}
    if result.confidence >= CONFIDENCE_THRESHOLD:
        return Command(goto="shortlist", update=update)
    # not sure: ask ONE question. Still unsure after that? A general physician can look at anything, so offer those doctors
    # instead of leaving the patient with nowhere to go (a trace showed people stuck in a loop of questions).
    if state.get("clarify_count", 0) == 0:
        return Command(goto="ask", update=update)
    update.update(specialty="General Medicine", alternative="", confidence=0.6,
                  notice="I could not tell exactly which specialist you need, so I suggest starting with General Medicine. A physician can look at everything and refer you on. ")
    return Command(goto="shortlist", update=update)


def ask(state: ChatState) -> dict:
    """LOOP: one clarifying question. The next message comes back to `intake` with more detail."""
    return say("I want to send you to the right doctor. Where exactly is the problem, and how long has it been going on?",
               outcome="asked", clarify_count=state.get("clarify_count", 0) + 1)


def handoff(state: ChatState) -> dict:
    """Two vague answers in a row: a person at the front desk takes over."""
    ticket_id = db.add_ticket(state["patient_id"], "front_desk", "Could not match a specialty: " + state.get("symptoms", ""))
    db.add_audit(state["patient_id"], "patient", "front-desk hand-off", f"ticket:{ticket_id}")
    return say("I am not sure which specialist fits, so I have asked our front desk to call you shortly.",
               outcome="handoff", active="", symptoms="", clarify_count=0, candidates=[])


def shortlist(state: ChatState, *, store: BaseStore) -> dict:
    """Find up to three doctors using TOOLS. Memory decides which clinic is listed first."""
    specialty = state["specialty"]
    usual_clinic = get_prefs(store, state["patient_id"]).get("usual_clinic", "")  # long-term memory

    # Step 1: the model chooses the tool call. We bind ONLY the one tool this step needs.
    calls = get_llm().tool_calls(
        TOOL_SYSTEM, f"SPECIALTY: {specialty}\nCLINIC: {usual_clinic}", [search_doctors],
        fallback=lambda: [{"name": "search_doctors", "args": {"specialty": specialty, "clinic": usual_clinic}}],
        names=patient_names(state))
    found = run_tools_parallel(calls)
    doctors = (found[0]["result"] if found else None) or []
    if not doctors and state.get("alternative"):
        doctors = db.list_doctors(specialty=state["alternative"])
    doctors = doctors[:8]

    # Step 2 (PARALLEL): next slot for every doctor at once, then risk for every slot at once.
    slots = run_tools_parallel([{"name": "get_next_slot", "args": {"doctor_id": d["id"]}} for d in doctors])
    with_slot = [(d, s["result"]) for d, s in zip(doctors, slots) if s["result"]]
    risks = run_tools_parallel([{"name": "assess_no_show_risk", "args": {"starts_at": s["starts_at"]}} for _, s in with_slot])

    options = [
        DoctorOption(doctor_id=d["id"], name=d["name"], specialty=d["specialty"], clinic=d["clinic"],
                     slot_id=s["id"], starts_at=s["starts_at"], risk=r["result"])
        for (d, s), r in zip(with_slot, risks)]
    # rank: usual clinic first, then earliest time
    options.sort(key=lambda o: (o.clinic != usual_clinic, o.starts_at))
    return {"candidates": [o.model_dump() for o in options[:3]]}


def present(state: ChatState) -> Command[Literal["pick", "handoff"]]:
    """Turn the options into a sentence that is easy to hear."""
    options = state.get("candidates", [])
    if not options:
        return Command(goto="handoff")
    ordinal = ["First", "Second", "Third"]
    lines = [f"{ordinal[i]}, {o['name']} at {o['clinic']}, {friendly_time(o['starts_at'])}." for i, o in enumerate(options)]
    urgent = ("Chest pain should be checked soon. If it gets worse, spreads, or you feel breathless or sweaty, call 112. "
              if safety.normalise(state.get("symptoms", "")).find("chest") >= 0 else "")
    intro = (state.get("notice") or "") + urgent + f"I suggest {state['specialty']}. Here are your options. "
    return Command(goto="pick", update=say(intro + " ".join(lines) + " Which one would you like?", outcome="waiting_for_choice", notice=""))


def pick(state: ChatState) -> Command[Literal["book", "__end__"]]:
    """INTERRUPT: pause here. The run stops and returns this payload to the app.

    When the app later calls the graph with Command(resume=slot_id), execution continues
    from this very line and `interrupt()` returns that slot_id.
    """
    slot_id = interrupt({"reply": state["reply"], "options": state["candidates"]})
    if slot_id == "cancel":   # the patient asked something else instead of choosing: let go of the pending choice
        return Command(goto=END, update={"candidates": [], "active": "", "chosen_slot_id": 0, "notice": ""})
    return Command(goto="book", update={"chosen_slot_id": int(slot_id)})


def book(state: ChatState, config: RunnableConfig, *, store: BaseStore) -> Command[Literal["pick", "shortlist", "__end__"]]:
    """Reserve the slot in ONE transaction. Safe to retry (idempotency key = thread + slot)."""
    patient_id, slot_id = state["patient_id"], state["chosen_slot_id"]
    if slot_id not in {o["slot_id"] for o in state["candidates"]}:
        # the choice is not one we offered: ask again, do not book
        return Command(goto="pick", update=say("Please choose one of the options I read out.", outcome="waiting_for_choice"))
    thread_id = config["configurable"]["thread_id"]
    try:
        appointment = db.reserve_slot(patient_id, slot_id, idempotency_key=f"{thread_id}:{slot_id}", reason=state.get("symptoms", ""))
    except db.SlotTaken:
        METRICS.count("slot_conflicts")
        return Command(goto="shortlist", update={"notice": "Sorry, that time was just taken. "})

    slot = db.get_slot(slot_id)
    invalidate_slots(slot["doctor_id"])                       # the cached slot is stale now
    set_pref(store, patient_id, "usual_clinic", slot["clinic"])   # long-term memory: remember the clinic
    set_pref(store, patient_id, "last_specialty", slot["specialty"])
    db.add_audit(patient_id, "patient", "booking", f"appointment:{appointment['id']}")
    METRICS.count("bookings")
    log.info("SMS to patient %s: booked %s", patient_id, slot["starts_at"])  # a real SMS gateway goes here
    reply = f"Booked. {slot['doctor_name']} at {slot['clinic']}, {friendly_time(slot['starts_at'])}. A text message confirmation is on its way."
    return Command(goto=END, update=say(reply, outcome="done", booking=appointment, active="", symptoms="", clarify_count=0, candidates=[], age=None))


def build_triage_graph() -> StateGraph:
    """Wire the nodes together. Nodes that return Command choose their own next step."""
    graph = StateGraph(ChatState)
    for name, fn in [("intake", intake), ("red_flag_gate", red_flag_gate), ("emergency", emergency), ("match", match),
                     ("ask", ask), ("handoff", handoff), ("shortlist", shortlist), ("present", present),
                     ("pick", pick), ("book", book)]:
        graph.add_node(name, fn)
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "red_flag_gate")
    graph.add_edge("shortlist", "present")
    for terminal in ("emergency", "ask", "handoff"):
        graph.add_edge(terminal, END)
    return graph
