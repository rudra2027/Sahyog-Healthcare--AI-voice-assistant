"""Replays the conversation from LangSmith trace 01a19898-1438-7574-9749-a6b1fb82c00c against the REAL model.

    python qa/replay_trace.py        (system Python; starts its own server on port 8105 with a throw-away database)

Conversation A is the trace as it happened (the chest pain message has two spaces, like the patient typed it).
Conversation B is the path the patient wanted: a general physician, a booking, then sharing details with the doctor.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_server   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8105"
server = qa_server.start(8105, {"DATABASE_PATH": str(Path(tempfile.mkdtemp()) / "trace.db"), "ALLOW_DEMO_LOGIN": "true", "REDIS_URL": "",
                                    "LLM_MODE": "openrouter", "STT_BACKEND": "browser"})
problems = []


def say(user, text, thread=None):
    res = requests.post(BASE + "/api/chat", json={"message": text, "thread_id": thread}, headers={"X-User-Id": user}, timeout=90).json()
    print(f"  patient: {text}\n  bot    : [{res['outcome']}] {res['reply'][:170]}")
    return res


def expect(ok, what):
    print(f"  {'PASS' if ok else 'FAIL'}  {what}\n")
    if not ok:
        problems.append(what)


try:

    print("A. the trace as it happened (patient p3)")
    r = say("p3", "i have been feeling dizzy the whole day dont feel like eating anything due to pukish senseory")
    t = r["thread_id"]
    r = say("p3", "gut , its been for a day", t)
    expect(r["outcome"] != "handoff", "two short answers never end at the front desk")
    r = say("p3", "but i can cosult with general physician ?", t) if r["outcome"] == "asked" else r
    r = say("p3", "chest  pain for a day", t)
    expect(r["outcome"] == "emergency" and "112" in r["reply"], "chest pain typed with two spaces is an emergency")

    print("B. the path the patient wanted (patient p1)")
    r = say("p1", "i have been feeling dizzy the whole day and dont feel like eating, I feel nauseous")
    t = r["thread_id"]
    if r["outcome"] == "asked":
        r = say("p1", "mostly my gut, for a day", t)
    expect(r["outcome"] == "waiting_for_choice", "the patient is offered doctors (not a dead end)")
    r = say("p1", "can I just consult a general physician?", t)
    expect(r["outcome"] == "waiting_for_choice" and all(o["specialty"] == "General Medicine" for o in r["options"]), "naming a general physician shows only General Medicine doctors")
    r = say("p1", "the first one", t)
    expect(r["reply"].startswith("Booked"), "booking works")
    doctor_line = r["reply"]
    r = say("p1", "can you share few details with my doctor :", t)
    expect(r["outcome"] == "asked", "the assistant asks what to share instead of saying hello")
    r = say("p1", "dizzy and nauseous since yesterday morning, I have not eaten, no fever", t)
    expect(r["outcome"] == "done" and "sent this to" in r["reply"], "the details are sent in the patient's own words")
    doctor = requests.get(BASE + "/api/appointments", headers={"X-User-Id": "fd1"}).json()
    mine = [a for a in doctor if a["patient_id"] == "p1" and a["reason"] and "dizzy" in a["reason"].lower()]
    expect(bool(mine) and "general physician" not in mine[0]["reason"].lower(), "the booking reason is the symptoms, without the 'can I see a physician' noise")
    rao = requests.get(BASE + "/api/doctors", timeout=10).json()
    print("  ", doctor_line)
finally:
    qa_server.stop(server)
print("ALL GOOD" if not problems else f"{len(problems)} PROBLEM(S): {problems}")
sys.exit(1 if problems else 0)
