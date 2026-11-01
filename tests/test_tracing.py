"""Guards for the LangSmith trace problems found in the shared traces:
  * steps shown "in progress" forever, because the last trace messages were lost when the server stopped
  * tool calls shown as separate traces, because worker threads lost the link to their parent run"""
import contextvars

from app import tracing
from app.config import settings
from app.tools import TOOL_MAP, run_tools_parallel


def test_flush_does_nothing_and_never_fails_when_tracing_is_off(monkeypatch):
    monkeypatch.setattr(settings, "langsmith_tracing", False)
    assert tracing.flush_traces() is None


def test_flush_runs_when_tracing_is_on_and_a_failure_cannot_break_the_app(monkeypatch):
    called = []
    monkeypatch.setattr(settings, "langsmith_tracing", True)
    import langchain_core.tracers.langchain as lc
    monkeypatch.setattr(lc, "wait_for_all_tracers", lambda: called.append(1))
    tracing.flush_traces()
    assert called == [1]
    monkeypatch.setattr(lc, "wait_for_all_tracers", lambda: (_ for _ in ()).throw(RuntimeError("network down")))
    tracing.flush_traces()                                   # must not raise


def test_graph_requests_flush_traces_after_the_response(client, monkeypatch):
    calls = []
    monkeypatch.setattr(tracing, "flush_traces", lambda: calls.append(1))
    import app.main as main
    monkeypatch.setattr(main, "flush_traces", lambda: calls.append(1))
    client.post("/api/chat", json={"message": "hello"}, headers={"X-User-Id": "p1"})
    client.post("/api/assistant", json={"message": "help"}, headers={"X-User-Id": "d1"})
    assert len(calls) >= 2                                   # one flush per request (the dependency adds it as a background task)


def test_parallel_tools_stay_inside_the_callers_context(monkeypatch):
    """The trace parent travels in a context variable. A worker thread must see the same value as the caller."""
    marker = contextvars.ContextVar("trace_parent", default="none")
    seen = []

    class Probe:
        def invoke(self, args):
            seen.append(marker.get())
            return args["n"]

    monkeypatch.setitem(TOOL_MAP, "probe", Probe())
    marker.set("the-conversation")
    out = run_tools_parallel([{"name": "probe", "args": {"n": i}} for i in range(6)])
    assert [o["result"] for o in out] == list(range(6))
    assert seen == ["the-conversation"] * 6
