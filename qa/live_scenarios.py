"""Runs the scenario catalogue against a server that uses the REAL model (OpenRouter), not the offline fake.
The unit tests use the fake model so they are free and repeatable; this checks the same questions with the real thing.

    python qa/live_scenarios.py          (system Python; starts its own server on port 8103 with a throw-away database)
"""
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_server   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tests import scenarios   # noqa: E402
from tests.test_general_questions import GENERAL   # noqa: E402

# Questions worded the way people really say them. Hospital rules we never wrote down must NOT be invented.
EXTRA_GENERAL = [
    ('can you tell me what a general physician is', r'(?i)family doctor|first doctor|wide range'),
    ('is a GP the same as a family doctor?', r'(?i)yes|same|family doctor|general physician'),
    ('do I need a referral to see a specialist?', r'(?i)front desk|do not have|general physician|refer'),
    ('what happens if I miss my appointment?', r'(?i)front desk|do not have'),
    ('do you treat children?', r'(?i)paediatric|children|front desk'),
]

BASE = "http://127.0.0.1:8103"
server = qa_server.start(8103, {"DATABASE_PATH": str(Path(tempfile.mkdtemp()) / "live.db"), "ALLOW_DEMO_LOGIN": "true", "REDIS_URL": "",
                                    "LLM_MODE": "openrouter", "STT_BACKEND": "browser"})
try:
    print("model in use:", requests.get(BASE + "/api/health/details", headers={"X-User-Id": "fd1"}).json()["llm"])
    failures, total = [], 0

    def run(who, path, question, pattern, extra=None):
        global total
        total += 1
        started = time.time()
        res = requests.post(BASE + path, json={"message": question, **(extra or {})}, headers={"X-User-Id": who}, timeout=90)
        reply = res.json().get("reply", str(res.json())) if res.ok else f"HTTP {res.status_code}"
        ok = re.search(pattern, reply, flags=re.S) is not None
        print(f"  {'PASS' if ok else 'FAIL'}  [{who}] {question[:58]:58} {time.time() - started:4.1f}s")
        if not ok:
            failures.append((who, question, reply))

    for q, pat in scenarios.PATIENT:
        run("p1", "/api/chat", q, pat)
    for q, about, pat in scenarios.PATIENT_RECORD_QUESTIONS:
        run("p1", "/api/chat", q, pat + "|HTTP 403", {"about": about})
    for q, pat in GENERAL + EXTRA_GENERAL:
        run("p1", "/api/chat", q, pat)
    for q, pat in scenarios.DOCTOR:
        run("d1", "/api/assistant", q, pat)
    for q, pat in scenarios.FRONT_DESK:
        run("fd1", "/api/assistant", q, pat)
    print(f"\n{total - len(failures)} of {total} scenarios passed with the real model")
    for who, q, reply in failures:
        print(f"  FAIL [{who}] {q!r}\n        -> {reply[:300]!r}")
finally:
    qa_server.stop(server)
sys.exit(1 if failures else 0)
