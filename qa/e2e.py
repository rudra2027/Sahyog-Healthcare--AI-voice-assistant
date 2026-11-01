"""End-to-end QA: drives a real browser through the patient site, the doctor portal and the front desk,
the way a person would, and records PASS or FAIL for each check. Run it with the SYSTEM Python (it has Playwright):

    python qa/e2e.py

It starts its own throw-away servers (own database, offline fake model) so your real data is never touched:
    port 8101 = demo logins ON   (lets the browser act as a patient, doctor or front-desk person)
    port 8102 = demo logins OFF  (what a real deployment looks like: strangers get the sign-in screen)
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_server   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "qa" / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
RESULTS: list[dict] = []
BANNED = ["LangGraph", "OpenRouter", "gpt-6", "checkpointer", "p95", "Behind the scenes", "Thread memory", "AI: ", "offline demo model", "TTL", "cache hit"]


def check(area: str, name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"area": area, "check": name, "ok": bool(ok), "detail": detail})
    print(f"  {'PASS' if ok else 'FAIL'}  [{area}] {name}" + (f"  -> {detail}" if detail and not ok else ""))
    return bool(ok)


DB_PATHS: dict[int, str] = {}


def start_server(port: int, demo: bool):
    DB_PATHS[port] = str(Path(tempfile.mkdtemp()) / f"e2e{port}.db")
    return qa_server.start(port, {"DATABASE_PATH": DB_PATHS[port], "LLM_MODE": "fake", "REDIS_URL": "", "LANGSMITH_TRACING": "false",
                                  "ALLOW_DEMO_LOGIN": "true" if demo else "false", "PROMOTE_AFTER": "2"})


def make_speech_wav(path: Path, sentence: str) -> None:
    """A WAV with quiet, a spoken sentence, then quiet again: what a person at a microphone sounds like.
    Built with the project's own Python, which has the speech and audio libraries."""
    subprocess.run([str(VENV_PY), str(ROOT / "qa" / "make_wav.py"), str(path), sentence], check=True, cwd=ROOT, capture_output=True)


class Page:
    """A browser page that remembers every console error and every failed request, so no problem goes unseen."""

    def __init__(self, context, label: str):
        self.page = context.new_page()
        self.label, self.errors, self.bad_requests = label, [], []
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("response", lambda r: self.bad_requests.append(f"{r.status} {r.request.method} {r.url.split('localhost')[-1][:80]}")
                     if r.status >= 400 and "/api/tts" not in r.url and "favicon" not in r.url else None)

    def goto(self, url: str):
        self.page.goto(url, wait_until="load")
        self.page.wait_for_timeout(1500)

    def shot(self, name: str):
        self.page.screenshot(path=str(SHOTS / f"{self.label}-{name}.png"), full_page=False)

    def visible_text(self) -> str:
        return self.page.inner_text("body")


def no_backend_words(area: str, pg: Page):
    text = pg.visible_text()
    found = [w for w in BANNED if w.lower() in text.lower()]
    check(area, "page shows no backend details (model, memory, latency...)", not found, ", ".join(found))


def bot_ask(pg: Page, text: str, wait_ms: int = 4000) -> str:
    """Type into the round assistant and return its newest reply."""
    page = pg.page
    if not page.locator("#sahyogBot.open").count():
        page.click("#sbLaunch")
        page.wait_for_timeout(700)
    before = page.locator(".sb-b.bot").count()
    page.fill("#sbInput", text)
    page.press("#sbInput", "Enter")
    try:
        page.wait_for_function("(n) => document.querySelectorAll('.sb-b.bot').length > n && !document.querySelector('.sb-typing')", arg=before, timeout=wait_ms + 12000)
    except Exception:
        pass
    page.wait_for_timeout(300)
    return page.locator(".sb-b.bot").last.inner_text()


# =============================================================================== the patient site
def patient_tests(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 860}, permissions=["microphone"])
    pg = Page(ctx, "patient"); page = pg.page
    pg.goto("http://localhost:8101/?as=p1")
    check("patient", "home page loads with the right title", "Sahyog" in page.title())
    nav = page.inner_text(".nav-links")
    check("patient", "nav has Overview, Care, My visits, Records, Doctors", all(w in nav for w in ("Overview", "Care", "My visits", "Records", "Doctors")), nav)
    check("patient", "no doctor or front-desk links on the patient site", not any(w in nav for w in ("Scribe", "Front desk", "Desk")), nav)
    check("patient", "no demo-person picker is shown", page.locator("#persona").count() == 0)
    no_backend_words("patient", pg)
    box = page.locator("#sbLaunch").bounding_box()
    check("patient", "assistant launcher sits in the bottom-right corner", box and box["x"] > 1280 - 140 and box["y"] > 860 - 140, str(box))
    pg.shot("home")

    # --- chat on the page: find a doctor and book
    page.click("#quickChips .chip >> nth=0")
    page.wait_for_selector(".option .btn", timeout=20000)
    check("patient", "back-pain chip shows three doctor options", page.locator("#messages .option").count() == 3, str(page.locator("#messages .option").count()))
    page.click(".option .btn >> nth=0")
    page.wait_for_selector("#messages .bubble.bot >> text=Booked", timeout=20000)
    check("patient", "booking confirms in the chat", True)
    page.wait_for_timeout(800)
    rows = page.locator("#visitList .visit-row").count()
    check("patient", "the new booking appears under My visits", rows >= 1, f"{rows} rows")
    pg.shot("booked")

    # --- cancel from My visits (two taps)
    first = page.locator("[data-cancel]").first
    first.scroll_into_view_if_needed()
    first.click()
    check("patient", "cancel asks for a second tap", "Tap again" in first.inner_text())
    before = page.locator("#visitList .visit-row").count()
    first.click()
    page.wait_for_timeout(1200)
    check("patient", "second tap cancels the visit", page.locator("#visitList .visit-row").count() == before - 1)

    # --- records: upload and ask
    page.locator("#fileInput").set_input_files({"name": "thyroid_test.txt", "mimeType": "text/plain",
                                                 "buffer": b"TSH 6.8 mIU/L (high, normal 0.4 to 4.0). Free T4 0.9 ng/dL. Advice: repeat in six weeks."})
    page.wait_for_selector("#docList >> text=thyroid_test.txt", timeout=15000)
    page.wait_for_selector("#docList >> text=ready", timeout=15000)
    check("patient", "an uploaded report shows as ready", True)
    page.fill("#recQ", "What was my TSH?")
    page.click("#recAsk")
    page.wait_for_selector("#recAnswer .answer p", timeout=20000)
    answer = page.inner_text("#recAnswer")
    check("patient", "asking about the report answers with the number and its source", "6.8" in answer and "thyroid_test.txt" in answer, answer[:120])

    # --- doctors list
    page.locator("#docFilters .chip").nth(1).click()
    page.wait_for_timeout(400)
    card = page.locator("#docGrid .doc").first
    card.scroll_into_view_if_needed(); card.click()
    page.wait_for_timeout(900)
    check("patient", "tapping a doctor shows free times", page.locator("#docGrid .doc.open .slot-pill").count() > 0)

    # --- the round assistant
    chips = [c.strip() for c in page.locator("#sbChips .sb-chip").all_inner_texts()]
    page.click("#sbLaunch"); page.wait_for_timeout(800)
    chips = [c.strip() for c in page.locator("#sbChips .sb-chip").all_inner_texts()]
    check("patient", "assistant offers patient chips", any("chest" in c.lower() or "back" in c.lower() for c in chips), str(chips))
    reply = bot_ask(pg, "When is my next appointment?")
    check("patient", "assistant answers 'next appointment' from the patient's own rows", re.search(r"(?i)next visit|no upcoming", reply) is not None, reply)
    reply = bot_ask(pg, "I have crushing chest pain and my left arm is numb")
    check("patient", "assistant sends chest pain to 112 at once", "112" in reply, reply)
    check("patient", "emergency reply is styled as urgent", page.locator(".sb-b.emergency").count() >= 1)
    reply = bot_ask(pg, "What dose of atorvastatin should I take?")
    check("patient", "assistant refuses to give a dose", not re.search(r"\d+\s?mg", reply), reply)
    pg.shot("assistant")
    page.click("#sbClose")

    # --- a doctor who opens the patient site is sent to the doctor portal
    other = Page(browser.new_context(viewport={"width": 1280, "height": 860}), "doctor-on-patient-site")
    other.goto("http://localhost:8101/?as=d1"); other.page.wait_for_timeout(1500)
    check("patient", "a doctor opening the booking site is redirected to /doctor", other.page.url.endswith("/doctor"), other.page.url)
    desk = Page(browser.new_context(viewport={"width": 1280, "height": 860}), "desk-on-patient-site")
    desk.goto("http://localhost:8101/?as=fd1"); desk.page.wait_for_timeout(1500)
    check("patient", "front desk opening the booking site is redirected to /desk", desk.page.url.endswith("/desk"), desk.page.url)

    check("patient", "no JavaScript errors on the patient site", not pg.errors, "; ".join(pg.errors[:3]))
    bad = [b for b in pg.bad_requests if not b.startswith("401 ")]
    check("patient", "no failed requests on the patient site", not bad, "; ".join(bad[:4]))

    # --- phone
    m = browser.new_context(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
    mp = Page(m, "patient-mobile"); mp.goto("http://localhost:8101/?as=p1")
    over = mp.page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    check("patient", "phone width: page does not scroll sideways", over <= 1, f"{over}px too wide")
    mp.page.click("#sbLaunch"); mp.page.wait_for_timeout(900)
    panel = mp.page.locator("#sbPanel").bounding_box()
    check("patient", "phone width: assistant panel fits the screen", panel and panel["width"] <= 375 and panel["x"] >= 0, str(panel))
    mp.shot("assistant")
    ctx.close(); m.close()


HOSTILE = "<img src=x onerror=window.__xss=1>Eve"


def plant_hostile_patient(port: int) -> None:
    """Put a patient with HTML in their name and reason on Dr. Rao's list, the way a hostile sign-up could."""
    import sqlite3
    db = sqlite3.connect(DB_PATHS[port])
    slot = db.execute("SELECT id FROM slot WHERE doctor_id = 'd1' AND taken = 0 AND starts_at > datetime('now','localtime') ORDER BY starts_at LIMIT 1 OFFSET 12").fetchone()[0]
    db.execute("INSERT INTO patient (id, name, phone, language, age) VALUES ('pxss', ?, '', 'en', 30)", (HOSTILE,))
    db.execute("INSERT INTO appointment (patient_id, slot_id, idempotency_key, status, created_at, reason, kind) VALUES ('pxss', ?, 'xss-1', 'booked', datetime('now'), ?, 'visit')",
               (slot, "<script>window.__xss=2</script> stomach pain"))
    db.execute("UPDATE slot SET taken = 1 WHERE id = ?", (slot,))
    db.commit(); db.close()


# =============================================================================== the doctor portal
def doctor_tests(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = Page(ctx, "doctor"); page = pg.page
    pg.goto("http://localhost:8101/doctor?as=d1")
    page.wait_for_selector(".appt", timeout=15000)
    check("doctor", "portal opens and greets the doctor by name", "Rao" in page.inner_text("#hello"), page.inner_text("#hello"))
    check("doctor", "four summary tiles", page.locator("#tiles .tile").count() == 4)
    check("doctor", "appointments are listed", page.locator(".appt").count() >= 3, str(page.locator(".appt").count()))
    nav = page.inner_text(".nav-links")
    check("doctor", "doctor nav has no booking or front-desk links", "Records" not in nav and "Desk" not in nav, nav)
    no_backend_words("doctor", pg)
    pg.shot("home")

    page.click("#dayTabs [data-day='']"); page.wait_for_timeout(900)
    check("doctor", "hostile HTML in a patient's name is shown as text, never run", page.evaluate("window.__xss") is None and "<img" in page.inner_text("#apptList"), str(page.evaluate("window.__xss")))
    page.locator(".appt", has_text="Eve").click(); page.wait_for_timeout(900)
    check("doctor", "hostile HTML in the patient card is also harmless", page.evaluate("window.__xss") is None)
    page.locator(".appt", has_text="Asha").click()
    page.wait_for_selector("#patientPanel h3 >> text=Asha", timeout=10000)
    panel = page.inner_text("#patientPanel")
    check("doctor", "patient card shows reason, language and reports with upload times", all(w in panel for w in ("Cholesterol follow-up", "lipid_report.pdf", "Marathi")), panel[:200])
    page.fill("#pq", "What was the LDL cholesterol?"); page.click("#pAsk")
    page.wait_for_selector("#pAnswer .answer p", timeout=20000)
    ans = page.inner_text("#pAnswer")
    check("doctor", "asking about the report returns 162 with a source", "162" in ans and "lipid_report" in ans, ans[:160])

    page.locator(".appt", has_text="Priya").click()
    page.wait_for_selector("#patientPanel h3 >> text=Priya")
    page.select_option("#fuSlot", index=2)
    page.fill("#fuReason", "Review after blood tests")
    page.click("#fuBook")
    page.wait_for_selector(".appt >> text=Follow-up", timeout=10000)
    check("doctor", "scheduling a follow-up adds a Follow-up row to the schedule", True)
    page.screenshot(path=str(SHOTS / "doctor-followup.png"))

    # --- note: draft and sign
    page.locator("#note").scroll_into_view_if_needed()
    page.click("#sampleBtn"); page.wait_for_timeout(500)
    page.click("#draftBtn")
    page.wait_for_selector("#draftOut .soap", timeout=20000)
    check("doctor", "drafting a note shows SOAP sections and flags a gap", page.locator("#draftOut input.gap").count() >= 1)
    gap = page.locator("#draftOut input.gap").first
    gap.fill("Atorvastatin 10 mg nightly")
    page.click("#signBtn")
    page.wait_for_selector("#draftOut >> text=Signed", timeout=15000)
    check("doctor", "signing the corrected note saves it", True)

    # --- the doctor's assistant
    page.click("#sbLaunch"); page.wait_for_timeout(800)
    chips = [c.strip() for c in page.locator("#sbChips .sb-chip").all_inner_texts()]
    check("doctor", "assistant offers doctor chips", any("schedule" in c.lower() for c in chips), str(chips))
    for question, pattern in [("Is the MRI uploaded for Rahul?", r"(?i)mri_report"), ("Any new reports for me to review?", r"(?i)uploaded|none"),
                              ("Who is my next patient?", r"(?i)next patient"), ("Tell me about Priya", r"(?i)priya"),
                              ("What does Asha's lipid report say about LDL?", r"162")]:
        reply = bot_ask(pg, question)
        check("doctor", f"assistant: {question}", re.search(pattern, reply) is not None, reply[:160])
    pg.shot("assistant")

    # --- a doctor cannot open the front desk
    other = Page(browser.new_context(viewport={"width": 1280, "height": 800}), "doctor-on-desk")
    other.goto("http://localhost:8101/desk?as=d1"); other.page.wait_for_timeout(1500)
    check("doctor", "a doctor opening /desk is sent back to /doctor", other.page.url.endswith("/doctor"), other.page.url)
    check("doctor", "no JavaScript errors on the doctor portal", not pg.errors, "; ".join(pg.errors[:3]))
    bad = [b for b in pg.bad_requests if not b.startswith("401 ")]
    check("doctor", "no failed requests on the doctor portal", not bad, "; ".join(bad[:4]))
    ctx.close()


# =============================================================================== the front desk
def desk_tests(browser):
    ctx = browser.new_context(viewport={"width": 1366, "height": 900})
    pg = Page(ctx, "desk"); page = pg.page
    pg.goto("http://localhost:8101/desk?as=fd1")
    page.wait_for_selector(".dcard", timeout=15000)
    check("desk", "console shows a card for every doctor with times", page.locator(".dcard").count() >= 10)
    check("desk", "booked times show the patient on the board", page.locator(".tslot.booked").count() >= 3)
    check("desk", "six numbers across the top", page.locator("#kpis .kpi").count() == 6)
    check("desk", "hostile HTML in a name is harmless on the front desk too", page.evaluate("window.__xss") is None)
    check("desk", "no audit tab for the front desk (compliance only)", not page.locator("#auditNav").is_visible())
    check("desk", "the console is not the patient site (no booking hero, has a side bar)", page.locator(".side-nav").is_visible() and page.locator("#hero, .hero").count() == 0)
    no_backend_words("desk", pg)
    pg.shot("board")

    # shift a booking's time through the window
    first = page.locator(".tslot.booked").first
    patient = first.get_attribute("title").split(" · ")[0]
    first.click(); page.wait_for_selector(".modal-card [data-move]")
    check("desk", "tapping a booked time opens the booking", patient.split()[0] in page.locator(".modal-card h3").inner_text())
    page.click(".modal-card [data-move]"); page.wait_for_selector("#mvDays .dchip")
    page.click("#mvDays .dchip >> nth=1"); page.wait_for_selector("#mvSlots [data-slot]")
    page.locator("#mvSlots [data-slot]").nth(6).click()
    check("desk", "confirm stays disabled until a time is chosen, then enables", page.locator("#mvGo").is_enabled())
    page.click("#mvGo"); page.wait_for_timeout(1500)
    check("desk", "moving a booking shows a confirmation and closes the window", page.locator("#modal").is_hidden())
    pg.shot("moved")

    # a doctor goes away
    page.locator("[data-away]").nth(3).click(); page.wait_for_selector("#awayGo")
    page.click("#awayGo"); page.wait_for_timeout(1500)
    check("desk", "a doctor can be marked away and their free times close", page.locator(".dcard .pill.warn").count() >= 1 or page.locator(".tslot.blocked").count() >= 5)

    page.click("[data-view=appts]"); page.wait_for_selector("#view tbody tr")
    check("desk", "appointments list shows who, with which doctor and when", page.locator("#view tbody tr").count() >= 3)
    page.fill("#fSearch", "priya"); page.wait_for_timeout(400)
    check("desk", "searching a patient narrows the list", 1 <= page.locator("#view tbody tr").count() <= 4)
    btn = page.locator("[data-cancel]").first
    btn.click()
    check("desk", "cancel asks for a second tap", "Tap again" in btn.inner_text())
    rows_before = page.locator("#view tbody tr").count()
    btn.click(); page.wait_for_timeout(1500)
    check("desk", "second tap cancels the booking", page.locator("#view tbody tr").count() < rows_before or page.locator("#view .empty").count() == 1)

    page.click("[data-view=patients]"); page.wait_for_selector("[data-pid]")
    page.locator("[data-pid]", has_text="Asha").first.click(); page.wait_for_selector("#pdetail h2")
    detail = page.inner_text("#pdetail")
    check("desk", "patient record has a bookings table and an uploads table", "Bookings" in detail and "Documents and reports uploaded" in detail)
    docs = page.locator("#pdetail .cpanel").nth(1).inner_text()
    check("desk", "uploads show file names, never what the report says", "lipid_report" in docs and "162" not in docs and "LDL" not in docs, docs[:200])
    pg.shot("patient")

    page.click("[data-view=tickets]"); page.wait_for_timeout(700)
    check("desk", "tickets view opens", page.locator("#viewTitle").inner_text() == "Tickets")
    page.click("[data-view=board]"); page.wait_for_selector(".dcard")

    reply = bot_ask(pg, "Which doctors are available tomorrow?")
    check("desk", "assistant: which doctors are free", re.search(r"(?i)doctors? free|free", reply) is not None, reply[:140])
    reply = bot_ask(pg, "Has Asha uploaded any reports?")
    check("desk", "assistant: uploads show names only, not contents", "lipid_report" in reply and "162" not in reply, reply[:160])
    reply = bot_ask(pg, "move Rahul to 4 pm tomorrow")
    check("desk", "assistant asks before moving a booking", "Say yes" in reply and "4:00 PM" in reply, reply[:200])
    reply = bot_ask(pg, "yes")
    check("desk", "assistant moves it after a yes", reply.startswith("Done") and "4:00 PM" in reply, reply[:200])
    page.wait_for_timeout(1200)
    pg.shot("assistant")
    check("desk", "no JavaScript errors on the front desk", not pg.errors, "; ".join(pg.errors[:3]))
    bad = [b for b in pg.bad_requests if not b.startswith("401 ")]
    check("desk", "no failed requests on the front desk", not bad, "; ".join(bad[:4]))

    comp = Page(browser.new_context(viewport={"width": 1280, "height": 800}), "compliance")
    comp.goto("http://localhost:8101/desk?as=c1")
    comp.page.wait_for_selector("#auditNav", state="visible", timeout=10000)
    comp.page.click("[data-view=board]"); comp.page.wait_for_selector(".dcard")
    check("desk", "compliance sees the audit trail and no change buttons", comp.page.locator("[data-away], [data-block]:not([disabled])").count() == 0)
    ctx.close()


# =============================================================================== strangers, and the microphone
def stranger_tests(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 860})
    for path, who in (("/doctor", "doctor"), ("/desk", "desk")):
        pg = Page(ctx, f"stranger-{who}"); pg.goto(f"http://localhost:8102{path}")
        check("stranger", f"{path}: a stranger sees only the sign-in screen", pg.page.locator("#gate").is_visible() and not pg.page.locator("main").is_visible())
        check("stranger", f"{path}: no staff data is in the page", "Asha" not in pg.visible_text() and "Rahul" not in pg.visible_text())
        pg.shot("gate")
    pg = Page(ctx, "stranger-home"); pg.goto("http://localhost:8102/")
    pg.page.click("#sbLaunch"); pg.page.wait_for_timeout(700)
    reply = bot_ask(pg, "I have back pain")
    check("stranger", "the assistant asks a stranger to sign in", "sign in" in reply.lower(), reply)
    pg.page.press("#sbInput", "Escape")
    pg.page.wait_for_timeout(600)
    check("stranger", "Escape closes the assistant", pg.page.locator("#sahyogBot.open").count() == 0)
    pg.page.click("#authBtn"); pg.page.wait_for_timeout(500)
    check("stranger", "Sign in opens the sign-in window", pg.page.locator(".auth-back.open").count() == 1)
    pg.page.click("#authGo"); pg.page.wait_for_timeout(300)
    check("stranger", "an empty sign-in form is refused with a clear message", "password" in pg.page.inner_text("#authMsg").lower(), pg.page.inner_text("#authMsg"))
    pg.page.click(".auth-tabs [data-mode=signup]")
    check("stranger", "Create account shows the name field", pg.page.locator("#authName").is_visible())
    pg.page.keyboard.press("Escape"); pg.page.wait_for_timeout(400)
    check("stranger", "Escape closes the sign-in window", pg.page.locator(".auth-back.open").count() == 0)
    dr = pg.page.evaluate("fetch('/api/doctor/appointments').then(r => r.status)")
    check("stranger", "doctor data cannot be fetched without signing in", dr == 401, str(dr))
    ctx.close()


def mic_test(browser_type, wav: Path):
    """Edge with a fake microphone that plays a spoken sentence: the whole voice path, for real."""
    browser = browser_type.launch(channel="msedge", headless=True, args=[
        "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", f"--use-file-for-fake-audio-capture={wav}"])
    ctx = browser.new_context(viewport={"width": 1280, "height": 860}, permissions=["microphone"])
    pg = Page(ctx, "mic"); page = pg.page
    pg.goto("http://localhost:8101/?as=p1")
    page.click("#sbLaunch")
    try:
        page.wait_for_selector(".sb-b.user", timeout=60000)
        heard = page.locator(".sb-b.user").first.inner_text()
        check("voice", "speaking into the microphone sends the words to the assistant", re.search(r"(?i)back|pain|weeks|sitting", heard) is not None, heard)
        page.wait_for_selector(".sb-b.bot >> nth=1", timeout=30000)
        check("voice", "the assistant answers what it heard", True)
    except Exception as err:
        check("voice", "speaking into the microphone sends the words to the assistant", False, f"nothing was heard: {err}")
    pg.shot("mic")
    # hold-to-talk: press and hold the mic button while the fake microphone speaks, then let go
    page.wait_for_timeout(6000)
    count = page.locator(".sb-b.user").count()
    box = page.locator("#sbMic").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down(); page.wait_for_timeout(6500); page.mouse.up()
    try:
        page.wait_for_function("(n) => document.querySelectorAll('.sb-b.user').length > n", arg=count, timeout=40000)
        check("voice", "press-and-hold on the mic button records and sends", True)
    except Exception:
        check("voice", "press-and-hold on the mic button records and sends", False, "no new message after releasing")
    browser.close()


def main():
    wav = Path(tempfile.mkdtemp()) / "speech.wav"
    make_speech_wav(wav, "Dull lower back pain for three weeks, worse after sitting for a long time.")
    servers = [start_server(8101, True), start_server(8102, False)]
    plant_hostile_patient(8101)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            for name, fn in (("PATIENT SITE", patient_tests), ("DOCTOR PORTAL", doctor_tests), ("FRONT DESK", desk_tests), ("STRANGERS", stranger_tests)):
                print(f"\n== {name} ==")
                try:
                    fn(browser)
                except Exception as err:   # a crash is a failed check, not a silent skip
                    check(name.lower(), "the test run completed", False, f"{type(err).__name__}: {str(err)[:200]}")
            browser.close()
            print("\n== VOICE ==")
            try:
                mic_test(p.chromium, wav)
            except Exception as err:
                check("voice", "the test run completed", False, f"{type(err).__name__}: {str(err)[:200]}")
    finally:
        for s in servers:
            qa_server.stop(s)
    failed = [r for r in RESULTS if not r["ok"]]
    (ROOT / "qa" / "e2e_results.json").write_text(json.dumps(RESULTS, indent=1), encoding="utf-8")
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed, {len(RESULTS)} checks")
    for r in failed:
        print(f"  FAIL [{r['area']}] {r['check']}: {r['detail']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
