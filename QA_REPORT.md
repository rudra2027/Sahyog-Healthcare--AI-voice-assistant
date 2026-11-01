# QA report: Sahyog Care Companion

How it was tested, what was found, and what is still not proven.

## Results at a glance

| Layer | What it covers | Result |
|---|---|---|
| Automated tests (`pytest`) | units, API, access control for every endpoint and role, 68 spoken scenarios and the LangSmith trace cases, robustness | **403 passed** |
| Browser end-to-end (`python qa/e2e.py`) | real Edge browser through the patient site, doctor portal, front desk, strangers, phone width, microphone | **76 of 76 checks passed** |
| Real-model scenarios (`python qa/live_scenarios.py`) | the same 97 questions, answered by the real OpenRouter model | **97 of 97 passed** (after one fix, see D5) |
| Evaluation (`python -m evals.run`) | red-flag recall, scope leaks, specialty accuracy, records | 26 triage cases. Fake model: all pass. Real model: red-flag recall 100%, scope leaks 0, specialty accuracy 93% (target 85%), records 7/7 |
| Load test (`locust`, 25 users, 30 s) | booking, triage, records, notes | 454 requests, **0 failures**, triage p95 720 ms (target 4000 ms) |

## Who was tested, doing what

**Patient:** find a doctor by symptoms, book, see it under My visits, cancel (two taps), upload a report and ask about it,
ask "when is my next appointment", "do I have a follow-up", "have my reports been uploaded", emergency words go to 112,
a dose request is refused, someone else's records are refused, hostile text does not break anything.

**Doctor:** today's and tomorrow's schedule, next patient, patient card with reason and reports, "is the MRI uploaded for
Rahul", "any new reports for me", what a report says (with source), patient history, schedule a follow-up after tests,
consultation note (draft, fix the gap, sign), cannot open patients who never booked with them.

**Front desk:** bookings by day, doctor or patient, free slots by specialty or doctor, tickets (close one), who uploaded
what (names only, never contents), cancel a booking (two taps), compliance sees the audit trail and cannot cancel.

**Stranger:** sees only a sign-in screen on `/doctor` and `/desk`, no staff data, API calls return 401.

The full question list is in `tests/scenarios.py`. Add a row whenever someone finds a question handled badly.

## Defects found and fixed

| # | Found by | Problem | Fix |
|---|---|---|---|
| D1 | scenario tests | "Is the MRI uploaded for Rahul?" tried to read the report text and said "could not find" | status questions (uploaded, received, is there...) are answered from the upload list; only "what does it say" reads the report |
| D2 | scenario tests | "What does Priya's blood test say about ferritin?" found nothing, because the page text never says "blood test" | the file name now counts when searching a report |
| D3 | scenario tests | "Is there an MRI for Priya?" returned her profile card | a report type in the question is treated as a status question |
| D4 | scenario tests | spoken "a MRI" instead of "an MRI" | article chosen per report type |
| D5 | real-model run | the model sometimes wrote "Orthopedics", which matched no doctor, so a patient with back pain was sent to the front desk (intermittent) | the model is given the exact specialty list, and any spelling is snapped to it (`normalise_specialty`) |
| D6 | robustness tests | the silent-audio guard read the wrong part of the file, so it never worked | reads the real audio data position |
| D7 | robustness tests | a message of only spaces was accepted | trimmed and refused |
| D8 | access review | `/api/health` and `/api/metrics` were public and revealed the model, memory and speed numbers | public health says only "ok"; details and metrics are staff only |
| D9 | live testing | Gemini transcribed pure silence as "Hello" | silent clips are never sent |
| D10 | user report | doctor portal reloaded itself endlessly for signed-in users | reload only when the person actually changes; browser always fetches the newest scripts |
| D11 | user report | voice detection missed quiet microphones | lower adaptive threshold, microphone gain request, press-and-hold to talk, "I cannot hear you" hint |
| D13 | **LangSmith trace** | **"chest  pain for a day" (two spaces) was not recognised as an emergency, and the patient was booked into an ordinary visit** | all text is normalised before the red-flag rules (case, punctuation, repeated spaces), plus more natural wordings ("pain in my chest", "my chest hurts", "heart attack") |
| D14 | trace | with doctors on screen, anything typed counted as a failed choice, so a warning sign typed then was swallowed and a question could not be asked | red flags, questions about visits, "share with my doctor", a named specialty and "never mind" now leave the choice and are answered |
| D15 | trace | two vague answers opened a front-desk ticket and forgot everything, so the patient was asked the same question again | after one question the patient is offered a general physician; the front desk is no longer a dead end |
| D16 | trace | "can I consult a general physician?" was ignored | a doctor type named by the patient (general physician, skin doctor, cardiologist...) is used directly |
| D17 | trace | "can you share a few details with Dr. Rao :" got a generic "Hello..." | new workflow: the patient's own words go to their doctor (colon, "tell my doctor that...", or what they already said); the doctor sees them on the patient's card and can ask the assistant |
| D18 | trace + real model | the model's confidence of 0.55 for a clearly right specialty counted as unsure; tie between General Medicine and Paediatrics for "my toddler is not eating" | threshold 0.5; ties go to the more specific specialty |
| D19 | user report + real model | general questions ("what does a general physician do?", "what should I bring?", "how much does it cost?") were not understood, or the model's mood decided whether a dose request was refused | new general-questions workflow: facts written by people (app/knowledge.py), the model only phrases them, unknown hospital facts get "the front desk can help"; diagnosis and dose requests are refused by code before any model sees them |
| D20 | LangSmith traces | many steps showed "in progress" forever, and tool calls appeared as separate traces | **Not a failing model call.** The server was stopped a moment after the last request, before LangSmith's background uploader sent the final "finished" messages (reproduced exactly: root run, triage, shortlist and the model call stay pending). Fixed by flushing traces after every request and on shutdown, stopping QA servers gently, sending QA traces to their own project (Sahyog-QA), and keeping tool calls inside the conversation's trace (worker threads had lost the parent link). A hard kill 3 seconds after a request now leaves nothing pending |
| D12 | user report | assistant could not answer report-status questions or show history and follow-ups | new "My visits" workflow for patients, follow-up scheduling for doctors, report-status answers for doctors and front desk |

Hostile HTML in a patient's name and reason is shown as text and never runs (checked in the doctor and front-desk pages).

## Not proven (be honest about these)

* **A real sign-up and sign-in with Supabase from a browser.** Tests use a fake Supabase and the demo login. The code path
  that calls Supabase is exercised only by you signing in.
* **Your actual microphone** (Conexant SmartAudio). The voice path was proven with a recorded sentence played through a
  fake microphone in Edge: it was detected, transcribed with Gemini and answered. A physical microphone can still differ.
* **Speech-to-text speed:** Gemini 2.5 Flash took about 4 seconds for a sentence here, local whisper 2 to 3 seconds. Gemini is
  more accurate with accents. Set `STT_BACKEND=whisper` if you prefer the faster, offline one.
* **Redis, Ollama Phonon, LangSmith upload** were not exercised.
* **Many real users at once on a real network.** The load test ran on one laptop with a 300 ms pretend model.
* **Email confirmation is off in your Supabase project**, so anyone can sign up with any email. Staff roles are tied to the
  account's unique id and set by an administrator, so this cannot make someone a doctor, but turn confirmation on before
  real use.

## How to repeat all of this

```
.venv\Scripts\python -m pytest -q          # 239 tests, about 2 minutes
python qa/e2e.py                           # browser tests, about 3 minutes (uses system Python with Playwright)
python qa/live_scenarios.py                # the same scenarios with the real model
.venv\Scripts\python -m evals.run          # golden-set evaluation
```


## Front desk console (managerial portal)

New: `/desk` is a separate console. Day board per doctor, appointments, patient records as tables, shift time / change doctor /
cancel / block a doctor's day, and an assistant that does the same behind a yes.

Tested: 29 new tests in `tests/test_desk.py` (board, access by role, record never contains report text, shift, change doctor,
no double booking / blocked / past / missing slot, block and unblock, doctor away, assistant plan then yes, no, other question,
nearest free times, day without time, same-surname doctor and patient, the model sees no real names, the model alone cannot
change a booking, compliance and doctors refused). Browser: 85/85 checks including moving a booking through the window,
marking a doctor away, patient tables and the assistant's plan-then-yes. Real model: vague English, Hindi ("shaam ko rakh do")
and "not coming this afternoon" all became a correct plan waiting for a yes.

Defects found while building it:
* D21  My new CSS reused class names from the patient site (`.doc`, `.slots`), which hid the time chips. Renamed with a console prefix.
* D22  `hidden` did not hide the Audit link or the KPI strip because my CSS set `display` on them. Added `[hidden]` rules (caught by e2e).
* D23  "Dr. Rao" matched every doctor surnamed Rao. An exact name now wins; otherwise the assistant asks which one.
* D24  "cardiologists" (plural) was not read as a specialty.

Not done on purpose: the assistant's waiting plan is kept in the server's memory (two minutes), so with several server copies
it would need Redis. The desk cannot read report contents; only names, dates and status.
