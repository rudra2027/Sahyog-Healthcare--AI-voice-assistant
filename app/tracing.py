"""Sending traces to LangSmith reliably.

LangSmith traces are uploaded by a background thread in small batches. If the server stops a moment after the last request
(a restart, Ctrl+C, a test run that kills it), the final "this step finished" messages may never be sent, and LangSmith then
shows those steps as "in progress" forever. Nothing was wrong with the model call: the report of it was lost.

So we do two things:
  * after every request that ran a graph, flush the queue in the background (the person never waits for it)
  * flush once more when the server shuts down
"""
import logging

from fastapi import BackgroundTasks

from app.config import settings

log = logging.getLogger("sahyog")


def flush_traces() -> None:
    """Send every queued trace to LangSmith now. Safe to call when tracing is off."""
    if not settings.langsmith_tracing:
        return
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers

        wait_for_all_tracers()
    except Exception as err:   # tracing must never break the app
        log.warning("Could not flush LangSmith traces: %s", err)


def flush_after(background: BackgroundTasks) -> None:
    """A FastAPI dependency: add it to an endpoint and traces are flushed right AFTER the response is sent."""
    background.add_task(flush_traces)
