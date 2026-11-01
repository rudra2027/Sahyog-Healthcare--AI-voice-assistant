"""Starting and stopping a throw-away server for QA runs.

Two things matter here:
  * QA runs write their LangSmith traces to the project "Sahyog-QA", never to your real project.
  * Stopping is GENTLE (Ctrl+Break, which uvicorn treats like Ctrl+C), so the server can send its last traces. Killing it
    at once left the final steps of the last request "in progress" in LangSmith.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"


def start(port: int, env_extra: dict, wait_seconds: int = 60) -> subprocess.Popen:
    env = {**os.environ, "LANGSMITH_PROJECT": "Sahyog-QA", "HF_HUB_DISABLE_SYMLINKS_WARNING": "1", **env_extra}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0     # so Ctrl+Break reaches only this server
    proc = subprocess.Popen([str(VENV_PY), "-m", "uvicorn", "app.main:app", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    for _ in range(wait_seconds):
        try:
            if requests.get(f"http://127.0.0.1:{port}/api/health", timeout=1).ok:
                return proc
        except requests.RequestException:
            time.sleep(1)
    proc.kill()
    raise RuntimeError(f"server on port {port} did not start")


def stop(proc: subprocess.Popen, patience: int = 30) -> None:
    """Ask the server to shut down (it flushes its traces on the way out), and only kill it if it will not."""
    try:
        proc.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
        proc.wait(timeout=patience)
    except Exception:
        proc.kill()
