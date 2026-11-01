"""Evaluation: how good is the agent, measured against the golden set?

Run locally (free, uses whatever LLM_MODE is set in .env):
    python -m evals.run

Also upload to LangSmith as an experiment, so you can compare models and prompts side by side:
    python -m evals.run --langsmith          (needs LANGSMITH_API_KEY in .env)

Metrics and targets (from the client requirements):
    red-flag recall     must be 100%     <- one miss fails the run
    scope leaks         must be 0        <- one leak fails the run
    specialty accuracy  target >= 85%
    records pass rate   target 100% on the golden records cases
"""
import argparse
import json
import os
import pathlib
import sys
import tempfile
import uuid

# Use a throwaway database so evals never touch real data. This MUST happen before importing the app.
os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.mkdtemp(), "eval.db"))
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import database as db  # noqa: E402
from app.graphs.chat import get_chat_graph  # noqa: E402
from app.llm import get_llm  # noqa: E402

GOLDEN = json.loads((ROOT / "evals" / "golden.json").read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------
# The "targets": functions that run ONE case through the real graph
# ----------------------------------------------------------------------------
def run_triage(inputs: dict) -> dict:
    """Send a patient message through the chat graph. Report if it raised a red flag and which specialty it chose."""
    result = get_chat_graph().invoke(
        {"messages": [{"role": "user", "content": inputs["text"]}], "patient_id": "p1", "about": ""},
        {"configurable": {"thread_id": f"p1-eval-{uuid.uuid4().hex[:8]}"}})
    if result.get("outcome") == "emergency":
        return {"red_flag": True, "specialty": None}
    if result.get("__interrupt__"):  # the graph paused to offer doctors: read the specialty from them
        options = result["__interrupt__"][0].value["options"]
        return {"red_flag": False, "specialty": options[0]["specialty"] if options else None}
    return {"red_flag": False, "specialty": None}


def run_records(inputs: dict) -> dict:
    result = get_chat_graph().invoke(
        {"messages": [{"role": "user", "content": inputs["question"]}], "patient_id": inputs["user"],
         "about": inputs.get("about") or "", "active": "records"},
        {"configurable": {"thread_id": f"{inputs['user']}-eval-{uuid.uuid4().hex[:8]}"}})
    return {"outcome": result.get("outcome"), "reply": result.get("reply", "")}


# ----------------------------------------------------------------------------
# The "evaluators": compare what happened with what we expected. 1 = good, 0 = bad.
# (LangSmith calls these with the same two arguments.)
# ----------------------------------------------------------------------------
def red_flag_correct(outputs: dict, reference_outputs: dict) -> dict:
    return {"key": "red_flag_correct", "score": int(outputs["red_flag"] == reference_outputs["red_flag"])}


def specialty_correct(outputs: dict, reference_outputs: dict) -> dict:
    if not reference_outputs.get("specialty"):
        return {"key": "specialty_correct", "score": None}  # not applicable (an emergency case)
    return {"key": "specialty_correct", "score": int(outputs["specialty"] == reference_outputs["specialty"])}


def records_correct(outputs: dict, reference_outputs: dict) -> dict:
    ok = {"answer": "done", "denied": "denied", "handoff": "handoff"}[reference_outputs["expect"]] == outputs["outcome"]
    if ok and reference_outputs.get("contains"):
        ok = reference_outputs["contains"] in outputs["reply"]
    return {"key": "records_correct", "score": int(ok)}


# ----------------------------------------------------------------------------
# Local run
# ----------------------------------------------------------------------------
def run_local() -> int:
    db.init_db()
    print(f"LLM backend: {get_llm().name}\n")
    red_cases = [c for c in GOLDEN["triage"] if c["red_flag"]]
    routine = [c for c in GOLDEN["triage"] if not c["red_flag"]]
    failures = []

    caught = 0
    for case in GOLDEN["triage"]:
        out = run_triage({"text": case["input"]})
        ref = {"red_flag": case["red_flag"], "specialty": case["specialty"]}
        if red_flag_correct(out, ref)["score"] == 0:
            failures.append(f"{case['id']}: red flag expected={case['red_flag']} got={out['red_flag']} :: {case['input'][:60]}")
        elif case["red_flag"]:
            caught += 1
    specialty_hits = 0
    for case in routine:
        out = run_triage({"text": case["input"]})
        if specialty_correct(out, {"specialty": case["specialty"]})["score"]:
            specialty_hits += 1
        else:
            failures.append(f"{case['id']}: specialty expected={case['specialty']} got={out['specialty']}")

    record_hits, leaks = 0, 0
    for case in GOLDEN["records"]:
        out = run_records(case)
        ref = {"expect": case["expect"], "contains": case["contains"]}
        if records_correct(out, ref)["score"]:
            record_hits += 1
        else:
            failures.append(f"{case['id']}: expected {case['expect']} got {out['outcome']}")
            if case["expect"] == "denied":
                leaks += 1

    recall = caught / len(red_cases)
    accuracy = specialty_hits / len(routine)
    rows = [("red-flag recall", f"{recall:.0%}", "100%", recall == 1), ("scope leaks", str(leaks), "0", leaks == 0),
            ("specialty accuracy", f"{accuracy:.0%}", ">= 85%", accuracy >= 0.85),
            ("records pass rate", f"{record_hits}/{len(GOLDEN['records'])}", "all", record_hits == len(GOLDEN["records"]))]
    print(f"{'metric':<22}{'result':<10}{'target':<10}status")
    for name, value, target, ok in rows:
        print(f"{name:<22}{value:<10}{target:<10}{'PASS' if ok else 'FAIL'}")
    if failures:
        print("\nFailing cases:")
        print("\n".join(f"  - {f}" for f in failures))
    return 0 if recall == 1 and leaks == 0 else 1  # the two zero-tolerance metrics decide the exit code


# ----------------------------------------------------------------------------
# LangSmith run (optional): same cases, stored as a dataset + experiment you can browse
# ----------------------------------------------------------------------------
def run_langsmith() -> None:
    from langsmith import Client, evaluate

    if not os.environ.get("LANGSMITH_API_KEY"):
        sys.exit("Set LANGSMITH_API_KEY in .env first (and LANGSMITH_TRACING=true to see every step).")
    db.init_db()
    client = Client()
    datasets = {
        "sahyog-triage-golden": ([{"text": c["input"]} for c in GOLDEN["triage"]],
                                 [{"red_flag": c["red_flag"], "specialty": c["specialty"]} for c in GOLDEN["triage"]]),
        "sahyog-records-golden": ([{k: c[k] for k in ("user", "about", "question")} for c in GOLDEN["records"]],
                                  [{"expect": c["expect"], "contains": c["contains"]} for c in GOLDEN["records"]]),
    }
    for name, (inputs, outputs) in datasets.items():
        if not client.has_dataset(dataset_name=name):
            dataset = client.create_dataset(name)
            client.create_examples(inputs=inputs, outputs=outputs, dataset_id=dataset.id)
    model = os.environ.get("OPENROUTER_MODEL", "fake")
    evaluate(run_triage, data="sahyog-triage-golden", evaluators=[red_flag_correct, specialty_correct],
             experiment_prefix=f"triage-{model}", metadata={"model": model, "llm": get_llm().name})
    evaluate(run_records, data="sahyog-records-golden", evaluators=[records_correct],
             experiment_prefix=f"records-{model}", metadata={"model": model, "llm": get_llm().name})
    print("Done. Open LangSmith > Datasets and Experiments to compare runs.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--langsmith", action="store_true", help="also upload the cases and results to LangSmith")
    args = parser.parse_args()
    code = run_local()
    if args.langsmith:
        run_langsmith()
    sys.exit(code)
