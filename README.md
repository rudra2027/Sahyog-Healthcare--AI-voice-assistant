# Sahyog Care Companion

A voice-first hospital assistant built with **LangGraph**, with three separate portals for **patients**, **doctors** and the **front desk**. Every answer comes from checked data, every safety rule is plain code, and the model never has the last word on a booking.

> This is a learning project with synthetic data. It is **not medical advice** and not a real hospital system.

## What it does

| Portal | Who | What they can do |
|---|---|---|
| `/` | Patients | Describe symptoms and get matched to a doctor, book (with a pause to pick the doctor), see and cancel their visits, upload reports and ask what they say (with sources), tell their doctor something, ask general questions ("what does a general physician do?") |
| `/doctor` | Doctors | Today's schedule with each patient's reason for visiting, patient cards and reports, ask questions about a patient's reports, schedule follow-ups, new uploads and patient messages, dictate a consultation and **sign** the note |
| `/desk` | Front desk (compliance can look, not change) | A **console**: day board per doctor (booked, free, blocked times), appointments, patient records as tables, shift a time, change a doctor, cancel, mark a doctor away, tickets, audit trail |

Each portal has a round, animated **voice assistant** (bottom right) that adapts to the role. It listens hands-free, with mute, and speaks back. Its look is generated from a seed, so every person gets a different one.

### The front desk assistant

Tell it what to change: *"move Priya to 4 pm tomorrow"*, *"switch Rahul to another doctor"*, *"Dr. Rao is on leave this afternoon"*, *"which cardiologists are free tomorrow"*, *"show Asha's history"*.

* Plain rules read names, days and times. When a sentence is vague ("push her to after lunch", or Hindi) the model reads it, but it only ever sees `PATIENT` and `DOCTOR`, never real names.
* **Nothing changes until the person says yes.** Then the same safe database call as the buttons runs (one transaction, never a double booking) and an audit line is written.
* The model can misread a sentence. It can never book, move or cancel anything by itself.

## How it is built

```
Browser (static HTML/JS, no build step)
   │   Supabase sign-in token on every call
   ▼
FastAPI  ── role check on every endpoint (patient / doctor / frontdesk / compliance)
   │
   ├─ LangGraph chat graph ── router ─┬─ emergency (rules, no model)
   │                                  ├─ triage   (gate, loop, parallel tools, PAUSE to pick a doctor, book)
   │                                  ├─ records  (consent, SQL filter, cited answers, bounded checks)
   │                                  ├─ visits / share / info / welcome
   │                                  └─ scribe   (PAUSE for the doctor's signature, self-learning lessons)
   ├─ Staff assistants (plain code over the real tables; the model only reads vague wording)
   ├─ SQLite (doctors, slots, bookings, records, audit)   ·   Supabase (sign-in, profile, chat history)
   └─ OpenRouter model (or a built-in offline fake)       ·   LangSmith traces
```

**LangGraph ideas used:** shared state with reducers, `Command` routing, subgraphs as nodes, `interrupt()` and `Command(resume=...)`, a checkpointer (thread memory) and a store (long-term memory), structured output, tool binding with parallel tool runs, bounded loops, and human-in-the-loop pauses.

**Five memory layers:** thread memory (checkpointer), patient preferences (store), agent lessons that a doctor approves (store), a 30-second slot cache (never the source of truth), and SQL records always filtered by `WHERE patient_id = ?`.

### Safety, in plain code

* Red flags (chest pain, stroke signs, and so on) are decided by rules on normalised text, never by the model, and open an emergency ticket.
* Clinical requests ("what dose should I take?") are refused before any model runs; general questions are answered only from a facts file.
* A doctor can only open patients who have an active booking with them. The front desk sees **which** files were uploaded, never what a report says.
* Anything written inside an uploaded file that looks like an instruction is quarantined, not obeyed.
* Prompts are scrubbed of phone numbers, emails, ABHA ids and names before they leave.
* Staff roles live on the server. Staff register with a hospital access code that the server checks.

## Quick start

Needs Python 3.11.

```bash
git clone https://github.com/rudra2027/Sahyog-Healthcare--AI-voice-assistant.git
cd Sahyog-Healthcare--AI-voice-assistant

python -m venv .venv
# Windows:  .venv\Scripts\activate        macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # Windows: copy .env.example .env
uvicorn app.main:app --reload
```

Open <http://localhost:8000>. API docs are at <http://localhost:8000/docs>.

With **no keys at all** it runs on a built-in offline model, so you can run, test and load-test everything for free. For a quick tour without signing in, set `ALLOW_DEMO_LOGIN=true` in `.env` and open:

* `/?as=p1` patient Priya, `/doctor?as=d1` Dr. Rao, `/desk?as=fd1` front desk Meena, `/desk?as=c1` compliance Kavita.

Never leave `ALLOW_DEMO_LOGIN=true` on a real deployment.

## Configuration (`.env`)

Copy `.env.example` to `.env`. **`.env` is git-ignored: never commit it.** Everything is optional.

| Variable | What it is for |
|---|---|
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `LLM_MODE` | The real model through OpenRouter. `LLM_MODE=auto` uses it when a key exists, else the offline fake |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | Traces and evaluations |
| `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` | Real sign-in. Use only the **publishable** key, never the secret/service key |
| `DOCTOR_SIGNUP_CODE`, `FRONTDESK_SIGNUP_CODE` | Codes staff type in the "I am a" dropdown when they register. Empty = that choice is closed |
| `ALLOW_DEMO_LOGIN` | `true` lets the demo personas work (local demos and tests only) |
| `STT_BACKEND`, `GOOGLE_API_KEY`, `GEMINI_MODEL`, `WHISPER_MODEL` | Speech to text: Gemini Flash, local `faster-whisper`, or the browser's own service |
| `TTS_BACKEND`, `OLLAMA_HOST` | Speech output: Edge voices (default) or Ollama Phonon |
| `REDIS_URL` | Optional memory that survives restarts. Empty keeps everything in RAM |

### Sign-in with Supabase

1. Create a project, put `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` in `.env`.
2. Paste `supabase/schema.sql` into the Supabase SQL editor and run it (profiles and chat history with row-level security).
3. Everyone who signs up is a **patient**. Staff pick Doctor or Front desk in the form and enter their access code, or an administrator promotes them:

```bash
python -m app.staff users                                # who has signed in, and their role
python -m app.staff doctors                              # doctor ids a login can be attached to
python -m app.staff add-doctor "Dr. Adarsh" Cardiology Baner
python -m app.staff promote sb-<their id> doctor d31
python -m app.staff promote sb-<their id> frontdesk
```

They sign out and in again and land on their own portal.

## Project layout

```
app/
  main.py            every API endpoint, role checks
  config.py          settings from .env
  database.py        SQLite: tables, demo data, idempotent booking, moves, patient records
  llm.py             the one place that talks to a model (OpenRouter) or the offline fake
  safety.py          red flags, scrubbing, injection checks, "no diagnosis" check
  memory.py          checkpointer, store, lessons, cache
  staff_assistant.py doctor and front desk questions (plain code over real tables)
  desk_assistant.py  the desk's manage-by-talking assistant (plan, ask, then do)
  stt.py, voice.py   speech to text and text to speech
  tracing.py         makes sure LangSmith traces finish uploading
  graphs/            chat router, triage, records, ingest, scribe, visits, share, info
static/              the three portals (HTML, CSS, JS, no build step)
supabase/schema.sql  profile and chat-history tables with row-level security
tests/               pytest suite (450+ tests)
evals/               golden triage cases and the evaluation runner
loadtest/            Locust load test
qa/                  browser end-to-end checks, real-model scenarios, trace replay
docs/                walkthrough and LangGraph flow diagrams
```

## Test, evaluate, load-test

```bash
pytest -q                                  # 450+ tests, no API key needed
python -m evals.run                        # red-flag recall, scope leaks, specialty accuracy
python -m evals.run --langsmith            # also uploads results to LangSmith
python qa/e2e.py                           # real-browser checks (pip install playwright first)

# load test: pretend the model takes 300 ms, then run 25 users for 40 s
FAKE_LLM_DELAY_MS=300 uvicorn app.main:app --port 8000
locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 25 -r 5 -t 40s
```

`QA_REPORT.md` lists every defect found while testing and how it was fixed.

## Honest notes

* SQLite and keyword search stand in for Postgres and embeddings to keep the code readable.
* Scanned (image-only) PDFs need OCR, which is not set up. They fail visibly with a clear status.
* The front desk assistant keeps its waiting plan in the server's memory for two minutes. Several server copies would need Redis for that.
* Not verified here: the Redis backend and Ollama Phonon speech.
* All people, doctors, hospitals and reports are fictional.
