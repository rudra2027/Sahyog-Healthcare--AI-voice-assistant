"""Shared test setup.

IMPORTANT: the environment variables below are set BEFORE the app is imported, so the
tests use a throwaway database file and the offline fake LLM. No API key, no cost, and
your real data is never touched.
"""
import os
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["LLM_MODE"] = "fake"
os.environ["ALLOW_DEMO_LOGIN"] = "true"
os.environ["STT_BACKEND"] = "browser"   # tests never load the speech model
os.environ["REDIS_URL"] = ""
os.environ["PROMOTE_AFTER"] = "2"
os.environ["LANGSMITH_TRACING"] = "false"

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    """One running app for the whole test session (startup seeds the demo data)."""
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def as_user(user_id: str) -> dict:
    """Headers that 'log in' as a demo persona: p1 patient, d1 doctor, fd1 front desk, c1 compliance."""
    return {"X-User-Id": user_id}


CASE_A = ("Hi, my mother (62) has had a dull ache in her left arm and some breathlessness since this morning. "
          "She has diabetes. Which doctor should we see?")
CASE_B = "Dull lower back pain for three weeks, worse after long sitting. No fever, no weakness in the legs."
