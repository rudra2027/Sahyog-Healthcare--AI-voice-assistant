"""The "whiteboard" each graph reads from and writes to.

In LangGraph, agents do not send messages to each other. Every step (a "node") reads
the shared state, returns only the fields it wants to change, and LangGraph merges
the change in. The checkpointer saves this state after every step.

`Annotated[list, add_messages]` is a "reducer": it tells LangGraph to APPEND new
messages instead of overwriting the old list.
"""
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class ChatState(TypedDict, total=False):
    """State for the patient chat (router + triage + records). total=False: every key is optional."""

    messages: Annotated[list, add_messages]  # the conversation so far
    patient_id: str      # who is logged in. Comes from the login, NEVER from the model
    about: str           # whose records are being discussed (self, or someone who gave consent)
    active: str          # which workflow owns the conversation right now ("triage", "records" or "")

    # --- triage (find my doctor) ---
    symptoms: str
    age: int | None
    red_flag_reasons: list[str]
    specialty: str
    alternative: str
    confidence: float
    clarify_count: int   # how many clarifying questions we already asked
    candidates: list[dict]
    chosen_slot_id: int
    booking: dict
    notice: str          # a short apology to say before showing options again
    requested: str       # a specialty the patient asked for by name ("a general physician")
    offer: str           # a specialty we just offered to book ("Would you like me to find you a cardiologist?")

    # --- share details with my doctor ---
    share_doctor: str    # which doctor the patient is writing to, while we wait for the words

    # --- records (ask my record) ---
    retrieved: list[dict]
    citations: list[dict]
    widened: bool        # did we already widen the search once?
    regen: int           # how many times we regenerated a bad answer

    # --- what we say back ---
    reply: str
    outcome: str         # done | asked | waiting_for_choice | handoff | emergency | denied


class ScribeState(TypedDict, total=False):
    """State for the doctor's consultation scribe."""

    transcript: str
    patient_id: str
    doctor_id: str
    soap: dict           # the draft note
    medicines: list[str] # molecules the doctor actually said
    stock: list[dict]    # stock check results per medicine
    suggestions: list[dict]
    comments: str        # the doctor's comments when they reject a draft
    loops: int           # how many times the doctor rejected
    decision: dict       # what the doctor chose at the review pause
    note: dict           # the signed note
    lessons: list[dict]  # what the system learned from this signature


class IngestState(TypedDict, total=False):
    """State for the background job that reads an uploaded report."""

    doc_id: int
    patient_id: str
    filename: str
    data: bytes
    pages: list[str]
    chunks: list[dict]
