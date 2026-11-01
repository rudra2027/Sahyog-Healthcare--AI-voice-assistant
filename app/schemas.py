"""Pydantic models.

Pydantic classes describe the SHAPE of data. We use them in three places:

1. API requests and responses  (FastAPI validates them automatically)
2. What we ask the LLM to return ("structured output": the model must fill these in)
3. The arguments of tools the LLM may call

Field(description=...) matters: the LLM reads those descriptions to know what to put
in each field.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ----------------------------------------------------------------------------
# 1. What the LLM must return (structured output)
# ----------------------------------------------------------------------------
class SpecialtyMatch(BaseModel):
    """The LLM's answer to: which kind of doctor should this patient see?"""

    specialty: str = Field(description="One medical specialty, e.g. Orthopaedics or Cardiology")
    alternative: str | None = Field(default=None, description="A second specialty that could also help, if any")
    confidence: float = Field(ge=0, le=1, description="0 to 1. Use below 0.6 when the symptoms are too vague")


class Citation(BaseModel):
    """Points at the exact document page an answer came from."""

    doc_id: int = Field(description="The document id shown in the context, e.g. 3")
    page: int = Field(description="The page number shown in the context")


class CitedAnswer(BaseModel):
    """An answer about a patient's record, plus where it came from."""

    answer: str = Field(description="Plain-language answer using ONLY the provided context")
    citations: list[Citation] = Field(description="Every document page the answer used")


class SoapNote(BaseModel):
    """A clinical note in four parts: Subjective, Objective, Assessment, Plan."""

    subjective: str = Field(description="What the patient reported, in the patient's words")
    objective: str = Field(description="Measurements the doctor stated, e.g. blood pressure")
    assessment: str = Field(description="ONLY what the doctor stated. Never invent a diagnosis")
    plan: list[str] = Field(description="One line per action. Never invent a medicine or dose the doctor did not say")


class MedicineList(BaseModel):
    """Medicines (molecule names) the doctor actually said out loud."""

    items: list[str] = Field(description="Lower-case molecule names such as atorvastatin. Empty if none were named")


class Intent(BaseModel):
    """Used only when the keyword router cannot decide where a message belongs."""

    workflow: Literal["triage", "records", "info", "smalltalk"] = Field(
        description=("triage = the patient describes symptoms or wants a doctor, records = questions about their own reports, "
                     "info = a general question about doctors, specialties, the hospital or how things work, smalltalk = greeting or other")
    )


# ----------------------------------------------------------------------------
# 2. Tool arguments (the LLM fills these in when it decides to call a tool)
# ----------------------------------------------------------------------------
class SearchDoctorsArgs(BaseModel):
    specialty: str = Field(description="Specialty to search for, e.g. Orthopaedics")
    clinic: str = Field(default="", description="Optional clinic name to prefer, e.g. Baner")


class StockArgs(BaseModel):
    medicine: str = Field(description="Lower-case molecule name, e.g. atorvastatin")
    clinic: str = Field(default="Baner", description="Pharmacy clinic to check")


# ----------------------------------------------------------------------------
# 3. API requests and responses
# ----------------------------------------------------------------------------
class DoctorOption(BaseModel):
    """One doctor with the next free slot, as shown to the patient."""

    doctor_id: str
    name: str
    specialty: str
    clinic: str
    slot_id: int
    starts_at: str
    risk: float = 0.0


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)   # '   ' counts as empty
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = Field(default=None, description="Omit to start a new conversation")
    about: str | None = Field(default=None, description="Whose records to discuss; needs consent if not yourself")


class ChatResponse(BaseModel):
    thread_id: str
    reply: str
    outcome: Literal["done", "asked", "waiting_for_choice", "handoff", "emergency", "denied"]
    options: list[DoctorOption] = []
    citations: list[Citation] = []
    latency_ms: int = 0


class ResumeRequest(BaseModel):
    thread_id: str
    slot_id: int = Field(description="The slot the patient picked from the options")


class DocumentOut(BaseModel):
    doc_id: int
    filename: str
    status: str
    pages: int = 0


class AskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)   # '   ' counts as empty
    question: str = Field(min_length=1, max_length=1000)
    about: str | None = None


class ScribeStartRequest(BaseModel):
    transcript: str = Field(min_length=10)
    patient_id: str = "p1"


class ScribeReviewRequest(BaseModel):
    thread_id: str
    action: Literal["sign", "reject"]
    plan: list[str] | None = Field(default=None, description="The doctor's edited plan lines")
    comments: str = ""


class ScribeResponse(BaseModel):
    thread_id: str
    status: Literal["waiting_for_review", "signed"]
    draft: SoapNote | None = None
    stock: list[dict] = []
    suggestions: list[dict] = []
    note: dict | None = None


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1500)
    agent: str = "triage"  # each agent has its own voice


class AssistantRequest(BaseModel):
    """A question to the staff assistant (doctor or front desk)."""
    model_config = ConfigDict(str_strip_whitespace=True)   # '   ' counts as empty
    message: str = Field(min_length=1, max_length=1000)
    patient_id: str | None = Field(default=None, max_length=60, description="The patient whose card the doctor has open, so 'what does the report say?' knows who is meant.")


class PatientQuestion(BaseModel):
    """A doctor asking about one patient's reports."""
    model_config = ConfigDict(str_strip_whitespace=True)   # '   ' counts as empty
    question: str = Field(min_length=1, max_length=1000)


class FollowUpRequest(BaseModel):
    """A doctor asking a patient to come back (for example after tests)."""
    patient_id: str
    slot_id: int
    reason: str = Field(default="Follow-up visit", max_length=300)


class GeneralAnswer(BaseModel):
    """A short, spoken answer to a general question, written from the facts we gave the model."""

    answer: str = Field(max_length=600, description="At most three short sentences, plain words, no medical advice")
    specialty: str | None = Field(default=None, description="A specialty from the list that is relevant to the question, if any")


class DeskCommand(BaseModel):
    """What the front desk asked the assistant to DO, as the model reads it. Used only when plain rules cannot tell.
    The names of people are replaced by PATIENT and DOCTOR before the model sees the sentence."""

    action: Literal["move", "change_doctor", "cancel", "block_day", "none"] = Field(
        description=("move = shift the PATIENT's booking to another time, change_doctor = give the PATIENT a different doctor, "
                     "cancel = cancel the booking, block_day = the DOCTOR is away so block their free times, none = anything else"))
    day: str = Field(default="", description="today, tomorrow, a weekday name, or YYYY-MM-DD. Empty when not said.")
    time: str = Field(default="", description="24-hour HH:MM, e.g. 16:00 for 4 pm. Empty when not said. 'after lunch' = 14:00, 'afternoon' = 13:00, 'morning' = 10:00, 'evening' = 16:30. For block_day it is the time the doctor stops being available.")
    specialty: str = Field(default="", description="a medical specialty if one was named, else empty")
