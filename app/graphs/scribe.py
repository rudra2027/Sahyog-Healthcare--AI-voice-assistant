"""Workflow 3: THE CONSULTATION SCRIBE (the doctor's side).

    segment -> draft -> ground -> stock_check -> review (PAUSE) -> save_signed
                 ^                                  |
                 +---------- reject -----------------+   (redraft with the doctor's comments, max 2 times)

Rules this graph enforces in CODE:
* Nothing is saved until the doctor signs. Before that the note is only a draft.
* The model structures what the doctor SAID. It never prescribes. A medicine that is not
  in the transcript is replaced by "[molecule not stated: doctor to specify]".
* The doctor's edits teach the system (see `learn`), but only as suggestions.
"""
import difflib
import re
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Command, interrupt

from app import database as db
from app.formulary import CLASS_WORDS, MEDICINES
from app.llm import FakeLLM, get_llm
from app.memory import active_lessons, record_lesson
from app.metrics import METRICS
from app.schemas import MedicineList, SoapNote
from app.state import ScribeState
from app.tools import run_tools_parallel

SOAP_SYSTEM = (
    "You write a SOAP note (subjective, objective, assessment, plan) from a doctor-patient transcript. "
    "Use ONLY what was said. Never invent a diagnosis, a medicine or a dose. "
    "If the doctor named a medicine CLASS but no molecule, write the class followed by "
    "[molecule and dose not stated: doctor to specify]. "
    "If a line starts with SUGGESTION, you may use it as: <suggestion> [suggested from your earlier notes; doctor to confirm]."
)
BRACKET = re.compile(r"\[[^\]]*\]")


def segment(state: ScribeState) -> dict:
    """Clean the transcript. (A real system would also split speakers from audio.)"""
    return {"transcript": state["transcript"].strip(), "loops": state.get("loops", 0)}


def draft(state: ScribeState, *, store: BaseStore) -> dict:
    """Ask the model for a SOAP note. Active lessons for THIS doctor are offered as suggestions."""
    suggestions = [{"class": lesson["key"].split(":", 1)[1], "text": lesson["suggestion"]}
                   for lesson in active_lessons(store, state["doctor_id"])]
    prompt = f"TRANSCRIPT:\n{state['transcript']}\n"
    prompt += "".join(f"SUGGESTION {s['class']} => {s['text']}\n" for s in suggestions)
    if state.get("comments"):
        prompt += f"DOCTOR COMMENTS: {state['comments']}\n"
    patient = db.get_patient(state["patient_id"])
    soap = get_llm().structured(
        SoapNote, SOAP_SYSTEM, prompt,
        fallback=lambda: FakeLLM._soap(prompt),  # rules write a basic draft if the model fails
        names=[patient["name"]] if patient else [])
    return {"soap": soap.model_dump(), "suggestions": suggestions}


def ground(state: ScribeState) -> dict:
    """Block invented medicines. A molecule must appear in the transcript, or it is removed."""
    transcript = state["transcript"].lower()
    said = get_llm().structured(
        MedicineList, "List the medicine molecule names the doctor said.", state["transcript"],
        fallback=lambda: MedicineList(items=[m for m in MEDICINES if m in transcript]))
    medicines = [m.lower() for m in said.items if m.lower() in transcript]  # keep only what was really said
    plan = []
    for line in state["soap"]["plan"]:
        for molecule in MEDICINES:
            suggested = "[suggested from your earlier notes" in line
            if molecule in line.lower() and molecule not in transcript and not suggested:
                METRICS.count("invented_molecules_blocked")
                line = re.sub(molecule, "[molecule not stated: doctor to specify]", line, flags=re.IGNORECASE)
        plan.append(line)
    return {"soap": {**state["soap"], "plan": plan}, "medicines": medicines}


def _clinic_of(doctor_id: str) -> str:
    return (db.one("SELECT clinic FROM doctor WHERE id = ?", (doctor_id,)) or {}).get("clinic", "Baner")


def _stock_for_plan(plan: list[str], clinic: str) -> list[dict]:
    """PARALLEL tool calls: one stock lookup per medicine named in the plan, all at the same time."""
    molecules = sorted({m for line in plan for m in MEDICINES if m in line.lower()})
    results = run_tools_parallel([{"name": "check_stock", "args": {"medicine": m, "clinic": clinic}} for m in molecules])
    flags = [r["result"] for r in results if r.get("result")]
    for line in plan:  # a class without a molecule cannot be stock-checked yet
        if "doctor to specify" in line:
            flags.append({"medicine": line.split("[")[0].strip(), "status": "needs_molecule", "qty": None})
    return flags


def stock_check(state: ScribeState) -> dict:
    """Check pharmacy stock for the draft, so the doctor sees availability while reviewing."""
    return {"stock": _stock_for_plan(state["soap"]["plan"], _clinic_of(state["doctor_id"]))}


def review(state: ScribeState) -> Command[Literal["draft", "save_signed", "__end__"]]:
    """INTERRUPT: the run pauses until the doctor signs or rejects. Nothing is written before this returns."""
    decision = interrupt({"draft": state["soap"], "stock": state["stock"], "suggestions": state.get("suggestions", [])})
    if decision["action"] == "reject":
        if state.get("loops", 0) >= 2:  # stop after two rejections: a human writes this note
            return Command(goto=END, update={"decision": decision, "note": {"status": "rejected"}})
        return Command(goto="draft", update={"decision": decision, "comments": decision.get("comments", ""), "loops": state.get("loops", 0) + 1})
    return Command(goto="save_signed", update={"decision": decision})


def _plan_text(lines: list[str]) -> str:
    return "\n".join(BRACKET.sub("", line).strip() for line in lines)


def save_signed(state: ScribeState, *, store: BaseStore) -> dict:
    """Save the SIGNED note, write the audit event, and learn from the doctor's edits."""
    draft_soap, final_plan = state["soap"], state["decision"].get("plan") or state["soap"]["plan"]
    final_soap = {**draft_soap, "plan": final_plan}
    # edit distance: 0 = the doctor changed nothing, 1 = rewrote everything (brackets are ignored)
    distance = round(1 - difflib.SequenceMatcher(None, _plan_text(draft_soap["plan"]), _plan_text(final_plan)).ratio(), 3)
    note = db.save_note(state["patient_id"], state["doctor_id"], final_soap, draft_soap, distance)
    db.add_audit(state["doctor_id"], "doctor", "signed note", f"note:{note['id']}")
    METRICS.count("notes_signed")
    # the stock tool runs again on the SIGNED plan: this is the "in stock at Baner, 240 units" moment
    note["stock"] = _stock_for_plan(final_plan, _clinic_of(state["doctor_id"]))
    return {"note": note, "lessons": learn(store, state["doctor_id"], draft_soap["plan"], final_plan)}


def learn(memory_store, doctor_id: str, draft_plan: list[str], final_plan: list[str]) -> list[dict]:
    """SELF-LEARNING: when the draft only named a drug CLASS and the doctor picked a molecule, remember it.

    The lesson goes: candidate -> (repeats) -> ready_for_approval -> (doctor approves) -> active.
    """
    learned = []
    for line in draft_plan:
        lowered = line.lower()
        drug_class = next((c for word, c in CLASS_WORDS.items() if word in lowered and ("to specify" in lowered or "earlier notes" in lowered)), None)
        if not drug_class:
            continue
        chosen = next((f for f in final_plan if any(MEDICINES[m] == drug_class and m in f.lower() for m in MEDICINES)), None)
        if chosen:
            learned.append(record_lesson(memory_store, doctor_id, f"drug:{drug_class}", BRACKET.sub("", chosen).strip()))
    return learned


def build_scribe_graph() -> StateGraph:
    graph = StateGraph(ScribeState)
    for name, fn in [("segment", segment), ("draft", draft), ("ground", ground), ("stock_check", stock_check),
                     ("review", review), ("save_signed", save_signed)]:
        graph.add_node(name, fn)
    graph.add_edge(START, "segment")
    graph.add_edge("segment", "draft")
    graph.add_edge("draft", "ground")
    graph.add_edge("ground", "stock_check")
    graph.add_edge("stock_check", "review")
    graph.add_edge("save_signed", END)
    return graph
