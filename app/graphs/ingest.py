"""Workflow 2 (upload side): INGEST A REPORT.

    parse -> tag -> chunk -> index

This runs in the BACKGROUND (FastAPI BackgroundTasks) so the app never waits on a PDF.
The patient sees a status: queued -> processing -> ready (or failed).

Note for learners: real systems turn each chunk into an "embedding" (a list of numbers)
for meaning-based search. To keep this project simple we use keyword search instead.
The graph shape would be identical; you would just add an `embed` node before `index`.
"""
import hashlib
import io
import re

from langgraph.graph import END, START, StateGraph

from app import database as db
from app import safety
from app.memory import cache
from app.metrics import METRICS
from app.state import IngestState

CHUNK_CHARS = 600


def parse(state: IngestState) -> dict:
    """Get text out of the file. Text PDFs and .txt files work; scanned images would need OCR."""
    data, name = state["data"], state["filename"].lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        pages = [(page.extract_text() or "").strip() for page in PdfReader(io.BytesIO(data)).pages]
    else:
        pages = [p.strip() for p in data.decode("utf-8", errors="ignore").split("\f")]
    pages = [p for p in pages if p]
    if not pages:
        raise ValueError("No readable text found (a scanned PDF needs OCR, which is not set up)")
    return {"pages": pages}


def tag(state: IngestState) -> dict:
    """Mark text that tries to give the AI orders (prompt injection) as suspicious.

    Suspicious chunks are stored but NEVER retrieved. This is layer 1; the SQL patient
    filter is layer 2, so even a missed injection cannot reach another patient.
    """
    chunks = []
    for number, text in enumerate(state["pages"], start=1):
        for piece in _split(text):
            suspicious = safety.looks_like_injection(piece)
            if suspicious:
                METRICS.count("injections_quarantined")
            chunks.append({"page": number, "text": piece, "suspicious": suspicious})
    return {"chunks": chunks}


def _split(text: str) -> list[str]:
    """Cut a page into pieces of about CHUNK_CHARS, breaking at sentence ends."""
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if current and len(current) + len(sentence) > CHUNK_CHARS:
            pieces.append(current.strip())
            current = ""
        current += sentence + " "
    if current.strip():
        pieces.append(current.strip())
    return pieces


def index(state: IngestState) -> dict:
    """Save the chunks. The same file uploaded twice is only indexed once (content hash)."""
    fingerprint = hashlib.sha256("".join(c["text"] for c in state["chunks"]).encode()).hexdigest()[:16]
    key = f"ingested:{state['patient_id']}:{fingerprint}"
    if cache.get(key):
        METRICS.count("duplicate_uploads")
    else:
        for chunk in state["chunks"]:
            db.add_chunk(state["doc_id"], state["patient_id"], chunk["page"], chunk["text"], chunk["suspicious"])
        cache.set(key, True, ttl=3600)
    db.set_document_status(state["doc_id"], "ready", len(state["pages"]))
    db.add_audit(state["patient_id"], "patient", "upload", f"doc:{state['doc_id']}")
    return {}


def build_ingest_graph():
    graph = StateGraph(IngestState)
    for name, fn in [("parse", parse), ("tag", tag), ("index", index)]:
        graph.add_node(name, fn)
    graph.add_edge(START, "parse")
    graph.add_edge("parse", "tag")
    graph.add_edge("tag", "index")
    graph.add_edge("index", END)
    return graph.compile()  # no checkpointer needed: it is a short background job


def run_ingest(doc_id: int, patient_id: str, filename: str, data: bytes) -> None:
    """Entry point for the background task. Any failure becomes a visible 'failed' status."""
    db.set_document_status(doc_id, "processing")
    try:
        build_ingest_graph().invoke({"doc_id": doc_id, "patient_id": patient_id, "filename": filename, "data": data})
    except Exception as err:
        METRICS.count("ingest_failures")
        db.set_document_status(doc_id, f"failed: {err}")
