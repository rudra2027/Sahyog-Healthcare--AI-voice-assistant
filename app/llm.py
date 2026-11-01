"""The ONE place where we talk to a language model.

Every workflow calls `get_llm()` and uses three methods:

    llm.structured(Schema, system, user, fallback)  -> a filled-in Pydantic object
    llm.tool_calls(system, user, tools, fallback)   -> which tools the model wants to call
    (plain chat is not needed in this app: replies are built from checked facts)

Why a wrapper class?
1. SAFETY GATE: every prompt is scrubbed of phone numbers, names, ABHA ids first.
   This is the "egress adapter": nothing identifiable leaves in a prompt.
2. SWAPPABLE VENDOR: change OPENROUTER_MODEL in .env and nothing else changes.
3. FAILS SOFT: if the model errors or times out we use the rule-based `fallback`.
4. OFFLINE MODE: with no API key a built-in FakeLLM answers using simple rules, so you
   can develop, test and load-test without spending money.
"""
import logging
import re
import time
from functools import lru_cache

from pydantic import BaseModel

from app import safety
from app.config import settings
from app.formulary import CLASS_WORDS, MEDICINES, rule_match
from app.metrics import METRICS
from app.schemas import DeskCommand, GeneralAnswer, Citation, CitedAnswer, Intent, MedicineList, SoapNote, SpecialtyMatch

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Backend 1: the real model through OpenRouter
# ----------------------------------------------------------------------------
class OpenRouterLLM:
    name = "openrouter"

    def __init__(self) -> None:
        from langchain_openai import ChatOpenAI  # OpenRouter speaks the OpenAI API

        self.chat = ChatOpenAI(
            model=settings.openrouter_model,       # e.g. openai/gpt-6-sol
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            temperature=0,                          # same input -> same output (good for tests)
            timeout=30,                             # never wait forever
            max_retries=2,                          # retry a flaky network call twice
        )

    def structured(self, schema: type[BaseModel], system: str, user: str) -> BaseModel:
        # with_structured_output makes the model return JSON that Pydantic validates for us
        return self.chat.with_structured_output(schema).invoke([("system", system), ("human", user)])

    def tool_calls(self, system: str, user: str, tools: list) -> list[dict]:
        # bind_tools tells the model which tools exist; the model replies with the calls it wants
        message = self.chat.bind_tools(tools).invoke([("system", system), ("human", user)])
        return [{"name": c["name"], "args": c["args"]} for c in message.tool_calls]


# ----------------------------------------------------------------------------
# Backend 2: an offline stand-in that follows the same rules with plain code
# ----------------------------------------------------------------------------
class FakeLLM:
    name = "fake"

    def _pause(self) -> None:
        if settings.fake_llm_delay_ms:  # lets load tests pretend the model takes time
            time.sleep(settings.fake_llm_delay_ms / 1000)

    def structured(self, schema: type[BaseModel], system: str, user: str) -> BaseModel:
        self._pause()
        if schema is SpecialtyMatch:
            specialty, alternative, confidence = rule_match(user)
            return SpecialtyMatch(specialty=specialty, alternative=alternative, confidence=confidence)
        if schema is Intent:
            low = user.lower()
            if any(w in low for w in ("report", "result", "cholesterol", "lab", "scan", "mri", "hba1c", "record")):
                return Intent(workflow="records")
            if any(w in low for w in ("what is", "what does", "who is", "how do", "can i", "should i bring", "explain")):
                return Intent(workflow="info")
            if any(w in low for w in ("pain", "fever", "doctor", "book", "appointment", "ache", "cough", "sick")):
                return Intent(workflow="triage")
            return Intent(workflow="smalltalk")
        if schema is DeskCommand:     # offline: the rules already tried; a stand-in cannot read vague phrasing
            return DeskCommand(action="none")
        if schema is GeneralAnswer:   # offline: read the first facts back (a real model would phrase them)
            facts = [line[2:] for line in user.split("FACTS:", 1)[-1].splitlines() if line.startswith("- ")]
            return GeneralAnswer(answer=" ".join(facts[:2]) or "I do not have that information. The front desk can help.", specialty=None)
        if schema is MedicineList:
            low = user.lower()
            return MedicineList(items=[m for m in MEDICINES if m in low])
        if schema is CitedAnswer:
            return self._answer(user)
        if schema is SoapNote:
            return self._soap(user)
        raise ValueError(f"FakeLLM does not know {schema.__name__}")

    def tool_calls(self, system: str, user: str, tools: list) -> list[dict]:
        self._pause()
        specialty = re.search(r"SPECIALTY:\s*(.+)", user)
        clinic = re.search(r"CLINIC:\s*(.*)", user)
        if specialty:
            return [{"name": "search_doctors", "args": {"specialty": specialty.group(1).strip(), "clinic": (clinic.group(1).strip() if clinic else "")}}]
        return []

    # --- helpers that imitate what a good model would write ---
    @staticmethod
    def _answer(user: str) -> CitedAnswer:
        question = re.search(r"QUESTION:\s*(.*)", user).group(1)
        terms = safety.question_terms(question)
        chunks = re.findall(r"\[doc (\d+) page (\d+)\]\s*(.*)", user)
        scored = sorted(chunks, key=lambda c: -sum(t in c[2].lower() for t in terms))
        best = [c for c in scored if any(t in c[2].lower() for t in terms)][:2] or scored[:1]
        if not best:
            return CitedAnswer(answer="I could not find that in your records.", citations=[])
        sentences = []
        for _, _, text in best:
            sentences += [s.strip() for s in re.split(r"(?<=[.])\s+", text) if any(t in s.lower() for t in terms)]
        answer = " ".join(sentences[:3]) or best[0][2]
        return CitedAnswer(answer=answer, citations=[Citation(doc_id=int(d), page=int(p)) for d, p, _ in best])

    @staticmethod
    def _soap(user: str) -> SoapNote:
        body = re.search(r"TRANSCRIPT:\n(.*?)(?:\nSUGGESTION|\nDOCTOR COMMENTS:|\Z)", user, re.S).group(1)
        suggestions = dict(re.findall(r"SUGGESTION (\w+) => (.*)", user))
        doctor = " ".join(l.split(":", 1)[1].strip() for l in body.splitlines() if l.lower().startswith("doctor:"))
        patient = " ".join(l.split(":", 1)[1].strip() for l in body.splitlines() if l.lower().startswith("patient:"))
        low = doctor.lower()
        bp = re.search(r"(\d{2,3}) over (\d{2,3})", low)
        plan = []
        for molecule in MEDICINES:                          # medicines the doctor named
            if molecule in low:
                plan.append(f"{molecule.capitalize()} (as stated by the doctor)")
        for word, drug_class in CLASS_WORDS.items():        # only a CLASS was named
            if word in low and not any(MEDICINES[m] == drug_class and m in low for m in MEDICINES):
                dose = ", low dose" if "low-dose" in low or "low dose" in low else ""
                if drug_class in suggestions:
                    plan.append(f"{suggestions[drug_class]} [suggested from your earlier notes; doctor to confirm]")
                else:
                    plan.append(f"{drug_class.capitalize()}{dose} [molecule and dose not stated: doctor to specify]")
        tests = [t for t in ("ECG", "lipid panel", "x-ray", "blood test", "MRI", "ultrasound") if t.lower() in low]
        if tests:
            plan.append("Tests ordered: " + ", ".join(tests))
        follow = re.search(r"(?:in|after)\s+(\w+)\s+(weeks?|days?|months?)", low)
        if follow:
            plan.append(f"Review in {follow.group(1)} {follow.group(2)}")
        assessment = ("As stated by the doctor: cardiac work-up initiated for exertional chest symptoms."
                      if tests and "chest" in patient.lower() else "Assessment not stated: doctor to complete.")
        return SoapNote(
            subjective=patient or "Not stated.",
            objective=f"Blood pressure {bp.group(1)}/{bp.group(2)}." if bp else "No measurements stated.",
            assessment=assessment, plan=plan or ["No plan stated: doctor to complete."])


# ----------------------------------------------------------------------------
# The wrapper every workflow uses
# ----------------------------------------------------------------------------
class LLMClient:
    def __init__(self, backend) -> None:
        self.backend = backend
        self.name = backend.name

    def structured(self, schema: type[BaseModel], system: str, user: str, fallback, names: list[str] | None = None) -> BaseModel:
        """Ask for a Pydantic object. `fallback` is a function used if the model fails."""
        clean = safety.scrub(user, names)                  # 1. scrub before anything leaves
        METRICS.count("llm_calls")
        METRICS.count("scrubbed_items", clean.count)
        start = time.perf_counter()
        try:
            return self.backend.structured(schema, system, clean.text)
        except Exception as err:                           # 2. fail soft: rules take over
            log.warning("LLM failed (%s); using the rule-based fallback", err)
            METRICS.count("llm_fallbacks")
            return fallback()
        finally:
            METRICS.record_latency("llm_call", (time.perf_counter() - start) * 1000)

    def tool_calls(self, system: str, user: str, tools: list, fallback, names: list[str] | None = None) -> list[dict]:
        """Ask which tools to call. Returns [{"name": ..., "args": {...}}]."""
        clean = safety.scrub(user, names)
        METRICS.count("llm_calls")
        METRICS.count("scrubbed_items", clean.count)
        try:
            calls = self.backend.tool_calls(system, clean.text, tools)
            return calls or fallback()
        except Exception as err:
            log.warning("LLM tool call failed (%s); using the fallback", err)
            METRICS.count("llm_fallbacks")
            return fallback()


@lru_cache
def get_llm() -> LLMClient:
    """Pick the backend once. `auto` = OpenRouter if a key exists, else the offline fake."""
    mode = settings.llm_mode
    use_real = mode == "openrouter" or (mode == "auto" and settings.openrouter_api_key)
    backend = OpenRouterLLM() if use_real else FakeLLM()
    log.info("LLM backend: %s", backend.name)
    return LLMClient(backend)
