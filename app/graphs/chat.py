"""The patient chat graph: a ROUTER in front of the workflows.

    START -> router -> emergency
                    -> share    (send the patient's own words to their doctor)
                    -> visits   (my appointments, follow-ups, uploaded reports: answered from the patient's own rows)
                    -> triage   (a whole compiled graph used as one node: a "subgraph")
                    -> records  (another subgraph)
                    -> welcome

The router is cheap on purpose. Rules decide the common cases for free, and the model
is only asked when the message is truly unclear. That saves a whole model round trip
on most turns, which is the biggest single latency win in a voice app.
"""
import re
from functools import lru_cache
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Command

from app import database as db
from app import safety
from app.graphs.common import emergency, last_user_text, say
from app.formulary import SYMPTOM_CROSSWALK, requested_specialty
from app.graphs.records import build_records_graph
from app.graphs.scribe import build_scribe_graph
from app.graphs.info import NO, YES, clean, info
from app.graphs.share import is_share_request, share
from app.graphs.triage import build_triage_graph
from app.graphs.visits import is_visit_question, visits
from app.llm import get_llm
from app.memory import get_checkpointer, get_prefs, get_store
from app.schemas import Intent
from app.state import ChatState

# Fields cleared at the start of every turn so yesterday's reply never leaks into today's.
TURN_RESET = {"reply": "", "outcome": "", "retrieved": [], "citations": [], "widened": False, "regen": 0, "notice": ""}
RECORD_WORDS = ("report", "result", "cholesterol", "lab ", "scan", "mri", "hba1c", "record", "prescription")
TRIAGE_WORDS = ("pain", "fever", "doctor", "consult", "book", "appointment", "ache", "cough", "sick", "specialist", "cold",
                "unwell", "wrong", "hurt", "see someone", "not feeling", "symptom", "rash", "itch")
# every symptom word the specialty matcher knows also counts as "this is a triage message"
SYMPTOM_WORDS = tuple(word for words in SYMPTOM_CROSSWALK.values() for word in words)
GREETINGS = ("hi", "hello", "hey", "namaste", "good morning", "good evening")


# "What does a cardiologist do?", "who is a GP", "how should I prepare", "can I cancel": a general question, not a symptom
GENERAL_QUESTION = re.compile(
    r"\b(what is|what's|what are|what does|what (a|an|the) [a-z ]{1,40} (is|does|do)|tell me what|can you (tell|explain)|same as|different from|"
    r"who is|who are|explain|difference between|meaning of|tell me about|how does|how do i|how can i|how should i|"
    r"what should i (bring|do before|expect)|what to bring|what do i need|do i need|can i (cancel|bring|reschedule|change|upload|book online|pay)|"
    r"when should i see|which languages|do you (have|offer|accept)|is it (safe|private)|how long (does|will))\b", re.I)
PERSONAL = re.compile(r"\b(i have|i am having|i feel|i've been|i have been|i got|my [a-z]+ (hurts|is hurting|pain)|since (yesterday|morning|last)|for \d+ (days?|weeks?))\b", re.I)
# These are general questions even though they mention reports, records or appointments.
ALWAYS_GENERAL = re.compile(
    r"\b(how (do|can|should) i (upload|share|send|add|submit|prepare|book)|what should i (bring|take)|what to (bring|take)|how (do|can) i get ready|"
    r"are my (records|reports|data|details) (private|safe|secure)|is my (data|information|record) (private|safe|secure)|who can see my|"
    r"who are you|what can you do|what are you|how (much|many)|cost|price|fees?|insurance|cashless|video (consult|call)|telemedicine|home visit|"
    r"what happens if|what if i|will i (be|get)|am i allowed|do i have to|is there (a|any)|can someone else|"
    r"where (is|are) (your|the) (clinic|clinics|hospital|branch)|your (clinics?|timings?|hours|doctors|locations?)|what (time|are your)|which clinics)\b", re.I)
PERSONAL_ACTION = re.compile(r"\b(cancel|reschedule|rebook|change my|move my)\b", re.I)


def is_general_question(text: str) -> bool:
    """A general question about doctors, specialties or the hospital. Questions about the patient's own symptoms or reports are not."""
    low = text.lower()
    if safety.check_red_flags(text) or PERSONAL_ACTION.search(low):
        return False
    if ALWAYS_GENERAL.search(low):
        return not PERSONAL.search(low)
    if PERSONAL.search(low) or any(w in low for w in RECORD_WORDS) or "my upcoming" in low or "my next" in low or "my last" in low or "my past" in low or "my previous" in low:
        return False                                         # "what are my upcoming visits" is about THIS patient, not a general question
    return bool(GENERAL_QUESTION.search(low))


def keyword_intent(text: str) -> str | None:
    """Rule-based routing. Returns None when the message is unclear."""
    low = text.lower()
    if any(word in low for word in RECORD_WORDS):
        return "records"
    if any(word in low for word in TRIAGE_WORDS + SYMPTOM_WORDS):
        return "triage"
    if low.strip(" !.") in GREETINGS:
        return "welcome"
    return None


def router(state: ChatState) -> Command[Literal["emergency", "triage", "records", "visits", "share", "info", "welcome"]]:
    """Decide which workflow handles this message. Order matters: safety first."""
    text = last_user_text(state)
    reasons = safety.check_red_flags(text)                  # 1. red flags beat everything
    if reasons:
        return Command(goto="emergency", update={**TURN_RESET, "red_flag_reasons": reasons})
    if safety.is_clinical_request(text):                    # 1a. "what dose...", "do I have cancer": decided by code, never by the model's mood
        return Command(goto="info", update={**TURN_RESET, "active": "", "offer": ""})
    if state.get("active") == "offer":                      # 1b. we just asked "shall I find you a cardiologist?"
        if clean(text) in YES:
            return Command(goto="triage", update={**TURN_RESET, "active": "triage", "requested": state.get("offer", ""), "offer": "",
                                                  "symptoms": f"Wants to see {state.get('offer', 'a doctor')}"})
        update = {**TURN_RESET, "active": "", "offer": ""}
        if clean(text) in NO:
            return Command(goto="welcome", update={**update, "notice": "declined"})
        state = {**state, "active": ""}                     # anything else is a new question: route it normally below
        text_update = update
    else:
        text_update = TURN_RESET
    if state.get("active") in ("triage", "records", "share"):   # 2. keep the conversation with the same agent
        return Command(goto=state["active"], update=text_update)
    if is_general_question(text):                           # 3a. "what does a general physician do?" is answered from the facts file
        return Command(goto="info", update=text_update)
    if is_share_request(text):                              # 3b. "share details with Dr. Rao": a message to the patient's own doctor
        return Command(goto="share", update=text_update)
    if is_visit_question(text):                             # 3c. "when is my next appointment?" is answered from the patient's own rows
        return Command(goto="visits", update=text_update)
    intent = "triage" if requested_specialty(text) else keyword_intent(text)   # 4. cheap rules ("can I see a general physician?" is triage)
    if intent is None:                                      # 5. only now ask the model
        intent = get_llm().structured(
            Intent, "Decide which workflow a patient message belongs to: triage (symptoms or wants a doctor), records (their own reports), "
                    "info (a general question about doctors, specialties, the hospital or how things work) or smalltalk.", text,
            fallback=lambda: Intent(workflow="smalltalk")).workflow
        intent = "welcome" if intent == "smalltalk" else intent
    return Command(goto=intent, update=text_update)


def welcome(state: ChatState, *, store: BaseStore) -> dict:
    """Greet the patient, or (when the message was not a greeting and nothing matched) say what we can do.
    Long-term memory makes the greeting personal ("your usual clinic is Baner")."""
    text = last_user_text(state)
    if state.get("notice") == "declined":                   # they said no to "shall I find you a cardiologist?"
        return say("No problem. Ask me anything else, or tell me what is wrong and I will find you a doctor.", outcome="done", active="", notice="")
    patient = db.get_patient(state["patient_id"]) or {"name": "there"}
    if text.lower().strip(" !.,?") not in GREETINGS and not text.lower().startswith(tuple(GREETINGS)):
        # a trace showed "can you share details with Dr. Rao" answered with "Hello rudra2..." again: that sounds like nobody listened
        return say("I am not sure I understood that. I can find you a doctor, share details with your doctor, show your visits and follow-ups, "
                   "or answer questions about your reports. What would you like to do?", outcome="done", active="")
    prefs = get_prefs(store, state["patient_id"])
    reply = f"Hello {patient['name'].split()[0]}. I can find you a doctor, book a visit, or answer questions about your reports."
    if prefs.get("usual_clinic"):
        reply += f" Last time you visited {prefs['usual_clinic']}, so I will list that clinic first."
    return say(reply, outcome="done", active="")


@lru_cache
def get_chat_graph():
    """Build the patient chat graph once and reuse it for every request."""
    graph = StateGraph(ChatState)
    graph.add_node("router", router)
    graph.add_node("welcome", welcome)
    graph.add_node("emergency", emergency)
    graph.add_node("visits", visits)
    graph.add_node("share", share)
    graph.add_node("info", info)
    graph.add_node("triage", build_triage_graph().compile())    # subgraphs: compiled graphs used as nodes
    graph.add_node("records", build_records_graph().compile())
    graph.add_edge(START, "router")
    for name in ("welcome", "emergency", "visits", "share", "info", "triage", "records"):
        graph.add_edge(name, END)
    # The checkpointer saves state after every step. The store is the long-term memory.
    return graph.compile(checkpointer=get_checkpointer(), store=get_store())


@lru_cache
def get_scribe_graph():
    return build_scribe_graph().compile(checkpointer=get_checkpointer(), store=get_store())
