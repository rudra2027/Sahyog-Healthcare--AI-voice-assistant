"""Tools: plain Python functions the LLM is allowed to call.

How tool binding works, in four steps:
1. We write a normal function and decorate it with @tool.
2. The decorator reads the function's name, docstring and Pydantic arguments, and
   turns them into a "menu item" the model can read.
3. `llm.bind_tools([...])` shows ONLY the tools a node needs (fewer tools = cheaper,
   faster and safer than showing everything).
4. The model replies with the calls it wants; WE run them (in parallel, below) and
   build the final reply from real data. The model never makes up doctors or slots.
"""
import contextvars
from concurrent.futures import ThreadPoolExecutor

from langchain_core.tools import tool

from app import database as db
from app.config import settings
from app.memory import cache
from app.schemas import SearchDoctorsArgs, StockArgs


@tool(args_schema=SearchDoctorsArgs)
def search_doctors(specialty: str, clinic: str = "") -> list[dict]:
    """Find doctors for a medical specialty. Doctors at the preferred clinic are listed first."""
    doctors = db.list_doctors(specialty=specialty)
    return sorted(doctors, key=lambda d: d["clinic"] != clinic)  # False sorts before True


@tool
def get_next_slot(doctor_id: str) -> dict:
    """Get the next free appointment slot for one doctor. Cached for a few seconds."""
    key = f"slots:{doctor_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    slots = db.next_slots(doctor_id, limit=1)
    result = slots[0] if slots else {}
    cache.set(key, result, ttl=settings.slot_cache_seconds)
    return result


@tool
def assess_no_show_risk(starts_at: str) -> float:
    """Estimate how likely a patient is to miss this slot (0 to 1). Late and early slots are riskier."""
    hour = int(starts_at[11:13]) if len(starts_at) >= 13 else 12
    return round(0.12 + (0.1 if hour >= 16 or hour <= 9 else 0.0), 2)


@tool(args_schema=StockArgs)
def check_stock(medicine: str, clinic: str = "Baner") -> dict:
    """Check pharmacy stock for one medicine. Returns in_stock, out_of_stock or unknown."""
    return db.stock_for(medicine, clinic)


TOOL_MAP = {t.name: t for t in (search_doctors, get_next_slot, assess_no_show_risk, check_stock)}


def invalidate_slots(doctor_id: str) -> None:
    """After a booking the cached slot is stale, so delete it."""
    cache.delete(f"slots:{doctor_id}")


def run_tools_parallel(calls: list[dict]) -> list[dict]:
    """Run many tool calls at the same time. Total time is the slowest call, not the sum.

    Unknown tool names (a model can hallucinate one) are skipped safely.
    """
    def run_one(call: dict) -> dict:
        tool_fn = TOOL_MAP.get(call["name"])
        if tool_fn is None:
            return {**call, "result": None, "error": "unknown tool"}
        return {**call, "result": tool_fn.invoke(call["args"])}

    # A thread starts with an EMPTY context, so LangSmith would show every tool call as its own separate trace. Copying the
    # current context into each worker keeps the tool calls inside the conversation's trace.
    with ThreadPoolExecutor(max_workers=8) as pool:
        contexts = [contextvars.copy_context() for _ in calls]      # copied HERE, in the caller's thread, one per call
        return list(pool.map(lambda pair: pair[0].run(run_one, pair[1]), zip(contexts, calls)))
