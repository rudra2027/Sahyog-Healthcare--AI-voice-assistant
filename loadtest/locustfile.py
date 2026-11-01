"""Load test with Locust: many pretend users hit the API at the same time.

1. Start the server in one terminal:
       uvicorn app.main:app --port 8000
   (add FAKE_LLM_DELAY_MS=300 to pretend the AI takes 300 ms per call)
2. Start Locust in another terminal and open http://localhost:8089 :
       locust -f loadtest/locustfile.py --host http://localhost:8000
   Or run without a browser for 30 seconds with 25 users:
       locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 25 -r 5 -t 30s

When the run ends, the script prints PASS or FAIL against the client's targets:
    triage turn p95 under 4 seconds, record answer p95 under 8 seconds, note draft p95 under 20 seconds.
The client expects 1,400 requests an HOUR at the morning peak, which is under 0.4 per second,
so this test pushes far harder than real life on purpose.
"""
import random

from locust import HttpUser, between, events, task

CASE_B = "Dull lower back pain for three weeks, worse after long sitting. No fever, no weakness in the legs."
CASE_A = "My mother (62) has an ache in her left arm and breathlessness since this morning. She has diabetes."
TRANSCRIPT = ("Doctor: Does it happen when you climb stairs?\nPatient: Yes, two or three times this week.\n"
              "Doctor: Blood pressure is 138 over 86. I will start a low-dose statin and order an ECG in two weeks.")
TARGETS_MS = {"POST triage turn": 4000, "POST records answer": 8000, "POST note draft": 20000}


class PatientUser(HttpUser):
    """A patient browsing doctors, booking by chat, and asking about a report."""

    weight = 8
    wait_time = between(0.5, 2)
    headers = {"X-User-Id": "p1"}

    @task(5)
    def browse_doctors(self):
        self.client.get("/api/doctors", params={"specialty": "Orthopaedics"}, headers=self.headers, name="GET /api/doctors")

    @task(4)
    def see_slots(self):
        self.client.get(f"/api/doctors/d{random.randint(1, 30)}/slots", headers=self.headers, name="GET /api/doctors/[id]/slots")

    @task(6)
    def book_by_chat(self):
        """The full booking journey: describe symptoms, then pick an option."""
        with self.client.post("/api/chat", json={"message": CASE_B}, headers=self.headers, name="POST triage turn", catch_response=True) as r:
            if r.status_code != 200 or not r.json().get("options"):
                return r.failure(f"no options: {r.status_code}")
            first = r.json()
        slot = random.choice(first["options"])["slot_id"]
        # 409 means "someone else got there first", which is a normal outcome under load
        with self.client.post("/api/chat/resume", json={"thread_id": first["thread_id"], "slot_id": slot}, headers=self.headers,
                              name="POST resume booking", catch_response=True) as r:
            if r.status_code in (200, 409):
                r.success()

    @task(2)
    def red_flag(self):
        self.client.post("/api/chat", json={"message": CASE_A}, headers=self.headers, name="POST red flag")

    @task(3)
    def ask_records(self):
        self.client.post("/api/records/ask", json={"question": "What does the report say about her cholesterol?", "about": "p2"},
                         headers=self.headers, name="POST records answer")

    @task(1)
    def health(self):
        self.client.get("/api/health", name="GET /api/health")


class DoctorUser(HttpUser):
    """A doctor drafting and signing a note."""

    weight = 2
    wait_time = between(1, 3)
    headers = {"X-User-Id": "d1"}

    @task
    def draft_and_sign(self):
        with self.client.post("/api/scribe/start", json={"transcript": TRANSCRIPT, "patient_id": "p1"}, headers=self.headers,
                              name="POST note draft", catch_response=True) as r:
            if r.status_code != 200:
                return r.failure(str(r.status_code))
            thread = r.json()["thread_id"]
        plan = ["Atorvastatin 10 mg nightly", "Tests ordered: ECG", "Review in two weeks"]
        self.client.post("/api/scribe/review", json={"thread_id": thread, "action": "sign", "plan": plan},
                         headers=self.headers, name="POST sign note")


@events.quitting.add_listener
def check_targets(environment, **kwargs):
    """Print PASS/FAIL against the client's p95 targets and set the exit code."""
    failed = False
    print("\n--- p95 against the client's targets ---")
    for name, limit in TARGETS_MS.items():
        entry = environment.stats.entries.get((name, name.split()[0]))
        if not entry or not entry.num_requests:
            continue
        p95 = entry.get_response_time_percentile(0.95)
        ok = p95 <= limit
        failed |= not ok
        print(f"{name:<22} p95 {p95:>7.0f} ms   target {limit} ms   {'PASS' if ok else 'FAIL'}")
    if environment.stats.total.num_failures:
        failed = True
        print(f"{environment.stats.total.num_failures} requests failed")
    environment.process_exit_code = 1 if failed else 0
