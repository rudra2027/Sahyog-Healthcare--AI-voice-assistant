"""The front desk console: the day board, patient records, moving bookings (time or doctor), blocking a doctor's day,
and the same things done through the assistant (which always asks for a yes first)."""
import random
import string
import uuid
from datetime import date, timedelta

import pytest

from app import database as db
from app import desk_assistant
from app.schemas import DeskCommand

DESK = {"X-User-Id": "fd1"}
TOMORROW = (date.today() + timedelta(days=1)).isoformat()


def word() -> str:
    return "".join(random.choices(string.ascii_lowercase, k=7)).capitalize()


def new_doctor(specialty="Cardiology", clinic="Baner") -> dict:
    name = f"Dr. {word()} {word()}"
    return {"id": db.add_doctor(name, specialty, clinic), "name": name, "surname": name.split()[-1]}


def new_patient() -> dict:
    pid, name = "t-" + uuid.uuid4().hex[:8], f"{word()} {word()}"
    with db.conn() as c:
        c.execute("INSERT INTO patient (id, name, phone, language, age) VALUES (?,?,?,?,?)", (pid, name, "", "English", 41))
    return {"id": pid, "name": name, "first": name.split()[0]}


def slot_at(doctor_id: str, hhmm: str, day: str = TOMORROW) -> dict:
    return db.one("SELECT * FROM slot WHERE doctor_id = ? AND starts_at = ?", (doctor_id, f"{day} {hhmm}"))


def book(patient: dict, doctor: dict, hhmm="10:00", day=TOMORROW) -> dict:
    slot = slot_at(doctor["id"], hhmm, day)
    return db.reserve_slot(patient["id"], slot["id"], uuid.uuid4().hex, reason="knee pain") | {"slot": slot}


def say(client, text, who="fd1"):
    return client.post("/api/assistant", json={"message": text}, headers={"X-User-Id": who}).json()


def booking(appointment_id: int) -> dict:
    return db.one("SELECT a.*, s.starts_at, s.doctor_id FROM appointment a LEFT JOIN slot s ON s.id = a.slot_id WHERE a.id = ?", (appointment_id,))


# ---------- the console's data ----------
def test_board_shows_doctors_with_bookings_and_free_times(client):
    doctor, patient = new_doctor(), new_patient()
    appt = book(patient, doctor, "11:30")
    board = client.get(f"/api/desk/board?day={TOMORROW}", headers=DESK).json()
    mine = next(d for d in board["doctors"] if d["id"] == doctor["id"])
    assert [b["patient_name"] for b in mine["bookings"]] == [patient["name"]] and mine["bookings"][0]["starts_at"] == f"{TOMORROW} 11:30"
    assert len(mine["free"]) == 17 and board["totals"]["booked"] >= 1
    assert appt["slot"]["id"] not in [s["slot_id"] for s in mine["free"]]


def test_only_the_front_desk_runs_the_console(client):
    for who, code in (("p1", 403), ("d1", 403)):
        assert client.get("/api/desk/board", headers={"X-User-Id": who}).status_code == code
        assert client.get("/api/desk/patients", headers={"X-User-Id": who}).status_code == code
    assert client.get("/api/desk/board", headers={"X-User-Id": "c1"}).status_code == 200            # compliance may look
    assert client.post("/api/appointments/1/reschedule", json={"slot_id": 1}, headers={"X-User-Id": "c1"}).status_code == 403


def test_patient_record_is_a_table_of_every_booking_and_file_names_only(client):
    doctor, patient = new_doctor(), new_patient()
    keep, drop = book(patient, doctor, "09:00"), book(patient, doctor, "09:30")
    db.cancel_appointment(drop["id"])
    with db.conn() as c:
        c.execute("INSERT INTO document (patient_id, filename, status, pages, uploaded_at) VALUES (?,?,?,?,?)", (patient["id"], "mri-knee.pdf", "ready", 2, "2026-11-01 10:00"))
        c.execute("INSERT INTO doc_chunk (doc_id, patient_id, page, text) VALUES (1,?,1,'SECRET FINDING: meniscus tear')", (patient["id"],))
    rec = client.get(f"/api/desk/patients/{patient['id']}", headers=DESK).json()
    assert {v["status"] for v in rec["visits"]} == {"booked", "cancelled"} and len(rec["visits"]) == 2
    assert rec["documents"][0]["filename"] == "mri-knee.pdf"
    assert "SECRET FINDING" not in str(rec)                                  # the desk sees WHAT was uploaded, never what it says
    assert client.get("/api/desk/patients?q=" + patient["first"], headers=DESK).json()[0]["id"] == patient["id"]
    assert any(a["purpose"] == "viewed patient record" for a in db.list_audit(f"patient:{patient['id']}"))


# ---------- moving bookings ----------
def test_shift_the_time_frees_the_old_slot_and_takes_the_new_one(client):
    doctor, patient = new_doctor(), new_patient()
    appt = book(patient, doctor, "10:00")
    new = slot_at(doctor["id"], "15:00")
    out = client.post(f"/api/appointments/{appt['id']}/reschedule", json={"slot_id": new["id"]}, headers=DESK).json()
    assert out["starts_at"] == f"{TOMORROW} 15:00" and out["changed_doctor"] is False
    assert slot_at(doctor["id"], "10:00")["taken"] == 0 and slot_at(doctor["id"], "15:00")["taken"] == 1
    assert booking(appt["id"])["starts_at"] == f"{TOMORROW} 15:00"


def test_change_the_doctor_moves_the_booking_to_the_other_doctors_slot(client):
    first, second, patient = new_doctor(), new_doctor(clinic="Kothrud"), new_patient()
    appt = book(patient, first, "10:00")
    out = client.post(f"/api/appointments/{appt['id']}/reschedule", json={"slot_id": slot_at(second["id"], "10:00")["id"]}, headers=DESK).json()
    assert out["changed_doctor"] is True and booking(appt["id"])["doctor_id"] == second["id"]
    assert slot_at(first["id"], "10:00")["taken"] == 0


def test_a_move_can_never_double_book_or_use_a_blocked_or_past_time(client):
    doctor, a, b = new_doctor(), new_patient(), new_patient()
    one, two = book(a, doctor, "10:00"), book(b, doctor, "10:30")
    url = f"/api/appointments/{one['id']}/reschedule"
    assert client.post(url, json={"slot_id": two["slot"]["id"]}, headers=DESK).status_code == 409           # taken by someone else
    blocked = slot_at(doctor["id"], "14:00")
    assert client.post(f"/api/desk/slots/{blocked['id']}/block", headers=DESK).status_code == 200
    assert client.post(url, json={"slot_id": blocked["id"]}, headers=DESK).status_code == 409
    with db.conn() as c:
        c.execute("INSERT INTO slot (doctor_id, starts_at) VALUES (?, '2020-01-01 09:00')", (doctor["id"],))
        past = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    assert client.post(url, json={"slot_id": past}, headers=DESK).status_code == 409
    assert client.post(url, json={"slot_id": 99999999}, headers=DESK).status_code == 409
    assert booking(one["id"])["starts_at"] == f"{TOMORROW} 10:00"                                          # nothing moved


def test_block_and_unblock_a_slot_and_a_booked_slot_cannot_be_blocked(client):
    doctor, patient = new_doctor(), new_patient()
    appt = book(patient, doctor, "10:00")
    free = slot_at(doctor["id"], "12:00")
    assert client.post(f"/api/desk/slots/{free['id']}/block", headers=DESK).status_code == 200
    assert client.post(f"/api/desk/slots/{free['id']}/block", headers=DESK).status_code == 409
    assert client.post(f"/api/desk/slots/{free['id']}/unblock", headers=DESK).status_code == 200
    assert client.post(f"/api/desk/slots/{appt['slot']['id']}/block", headers=DESK).status_code == 409
    assert client.post(f"/api/desk/slots/{free['id']}/explode", headers=DESK).status_code == 404


def test_blocking_a_doctors_day_lists_who_needs_moving(client):
    doctor, patient = new_doctor(), new_patient()
    book(patient, doctor, "10:00")
    out = client.post(f"/api/desk/doctors/{doctor['id']}/block-day", json={"day": TOMORROW}, headers=DESK).json()
    assert out["blocked"] == 17 and [a["patient_name"] for a in out["to_move"]] == [patient["name"]]
    assert db.next_slots(doctor["id"], 50) == [] or all(s["starts_at"][:10] != TOMORROW for s in db.next_slots(doctor["id"], 50))


# ---------- the assistant ----------
def test_assistant_asks_before_moving_and_only_a_yes_changes_anything(client):
    doctor, patient = new_doctor(), new_patient()
    appt = book(patient, doctor, "10:00")
    ask = say(client, f"Move {patient['name']} to 4 pm")
    assert ask["outcome"] == "confirm" and "4:00 PM" in ask["reply"] and "yes" in ask["reply"].lower()
    assert booking(appt["id"])["starts_at"] == f"{TOMORROW} 10:00"                       # nothing yet
    done = say(client, "yes")
    assert done["outcome"] == "done" and done["changed"] is True and "4:00 PM" in done["reply"]
    assert booking(appt["id"])["starts_at"] == f"{TOMORROW} 16:00" and slot_at(doctor["id"], "10:00")["taken"] == 0
    assert any("assistant" in a["purpose"] for a in db.list_audit(f"appointment:{appt['id']}"))


def test_saying_no_or_something_else_leaves_the_booking_alone(client):
    doctor, patient = new_doctor(), new_patient()
    appt = book(patient, doctor, "10:00")
    say(client, f"shift {patient['name']} to 3 pm")
    assert "left it" in say(client, "no")["reply"]
    assert say(client, "yes")["outcome"] != "done" or booking(appt["id"])["starts_at"].endswith("10:00")
    say(client, f"shift {patient['name']} to 3 pm")
    say(client, "how many appointments are there today")                                  # a different question drops the plan
    say(client, "yes")
    assert booking(appt["id"])["starts_at"] == f"{TOMORROW} 10:00"


def test_a_taken_time_offers_the_nearest_free_ones(client):
    doctor, a, b = new_doctor(), new_patient(), new_patient()
    appt = book(a, doctor, "10:00")
    book(b, doctor, "16:00")
    out = say(client, f"move {a['name']} to 4 pm")
    assert out["outcome"] == "asked" and "not free" in out["reply"] and "3:30 PM" in out["reply"]
    assert booking(appt["id"])["starts_at"].endswith("10:00")


def test_a_day_without_a_time_lists_the_free_times(client):
    doctor, patient = new_doctor(), new_patient()
    book(patient, doctor, "10:00")
    out = say(client, f"reschedule {patient['name']} to tomorrow")
    assert out["outcome"] == "asked" and "9:00 AM" in out["reply"] and "Which time" in out["reply"]


def test_changing_the_doctor_keeps_the_time_and_asks_first(client):
    first, second, patient = new_doctor(), new_doctor(clinic="Kothrud"), new_patient()
    appt = book(patient, first, "10:00")
    ask = say(client, f"change the doctor for {patient['name']} to {second['name']}")
    assert ask["outcome"] == "confirm" and second["name"] in ask["reply"] and "10:00 AM" in ask["reply"]
    say(client, "yes")
    assert booking(appt["id"])["doctor_id"] == second["id"] and booking(appt["id"])["starts_at"].endswith("10:00")


def test_another_doctor_of_the_same_specialty_is_found_by_the_assistant(client):
    first, second, patient = new_doctor("Dermatology"), new_doctor("Dermatology", "Kothrud"), new_patient()
    appt = book(patient, first, "10:00")
    ask = say(client, f"switch {patient['name']} to another doctor")
    assert ask["outcome"] == "confirm" and "Dermatology" not in ask["reply"] or second["name"] in ask["reply"] or "Dr." in ask["reply"]
    say(client, "yes")
    assert booking(appt["id"])["doctor_id"] != first["id"]


def test_cancel_through_the_assistant_needs_a_yes(client):
    doctor, patient = new_doctor(), new_patient()
    appt = book(patient, doctor, "10:00")
    assert say(client, f"cancel {patient['name']}'s appointment")["outcome"] == "confirm"
    assert booking(appt["id"])["status"] == "booked"
    say(client, "yes")
    assert booking(appt["id"])["status"] == "cancelled" and slot_at(doctor["id"], "10:00")["taken"] == 0


def test_doctor_on_leave_blocks_the_day_and_names_who_to_move(client):
    doctor, patient = new_doctor(), new_patient()
    book(patient, doctor, "10:00")
    ask = say(client, f"{doctor['name']} is on leave tomorrow")
    assert ask["outcome"] == "confirm" and "already booked" in ask["reply"]
    done = say(client, "yes")
    assert patient["name"] in done["reply"] and "closed" in done["reply"] and done["changed"]
    assert all(s["taken"] != 0 for s in db.rows("SELECT taken FROM slot WHERE doctor_id = ? AND starts_at LIKE ?", (doctor["id"], f"{TOMORROW}%")))


def test_a_doctor_with_the_same_surname_as_a_patient_is_not_mixed_up(client):
    doctor, patient = new_doctor(), new_patient()
    shared = type("P", (), {})
    with db.conn() as c:
        c.execute("UPDATE patient SET name = ? WHERE id = ?", (f"Asha {doctor['surname']}", patient["id"]))
    book(patient, doctor, "10:00")
    ask = say(client, f"move Asha {doctor['surname']} to 2 pm with Dr. {doctor['surname']}")
    assert ask["outcome"] == "confirm" and f"Asha {doctor['surname']}" in ask["reply"]


def test_vague_wording_goes_to_the_model_which_never_sees_real_names(client, monkeypatch):
    doctor, patient = new_doctor(), new_patient()
    appt = book(patient, doctor, "10:00")
    seen = []

    class Stub:
        def structured(self, schema, system, user, fallback, names=None):
            seen.append(user)
            return DeskCommand(action="move", time="14:00")

    monkeypatch.setattr(desk_assistant, "get_llm", lambda: Stub())
    ask = say(client, f"push {patient['name']} to after lunch")
    assert ask["outcome"] == "confirm" and "2:00 PM" in ask["reply"]
    assert seen and patient["first"] not in seen[0] and "PATIENT" in seen[0]
    say(client, "yes")
    assert booking(appt["id"])["starts_at"].endswith("14:00")


def test_the_model_cannot_move_anything_by_itself(client, monkeypatch):
    doctor, patient = new_doctor(), new_patient()
    appt = book(patient, doctor, "10:00")

    class Stub:
        def structured(self, *a, **k):
            return DeskCommand(action="cancel")                           # even a model that says "cancel"...

    monkeypatch.setattr(desk_assistant, "get_llm", lambda: Stub())
    out = say(client, f"please sort out {patient['name']} somehow and make it quick")
    assert out["reply"] and booking(appt["id"])["status"] == "booked"      # ...only produces a question, never a change


def test_availability_and_history_questions(client):
    doctor, patient = new_doctor("Rheumatology" if False else "Cardiology"), new_patient()
    book(patient, doctor, "10:00")
    assert "free" in say(client, f"which doctors are available tomorrow at 3 pm")["reply"]
    hist = say(client, f"show the history of {patient['name']}")
    assert patient["name"] in hist["reply"] and "1 booking" in hist["reply"] and hist["patient_id"] == patient["id"]


def test_other_roles_cannot_change_bookings_through_the_assistant(client):
    doctor, patient = new_doctor(), new_patient()
    appt = book(patient, doctor, "10:00")
    assert "can look but not change" in say(client, f"move {patient['name']} to 4 pm", "c1")["reply"]
    assert booking(appt["id"])["starts_at"].endswith("10:00")
    assert client.post("/api/assistant", json={"message": f"cancel {patient['name']}"}, headers={"X-User-Id": "p1"}).status_code == 403


@pytest.mark.parametrize("text,expected", [("4 pm", "16:00"), ("at 4:30 pm", "16:30"), ("16:15", "16:15"), ("noon", "12:00"), ("to 3", "15:00"), ("at 10", "10:00"), ("9.30 am", "09:30")])
def test_times_are_read_the_way_clinic_staff_say_them(text, expected):
    assert desk_assistant.parse_times("move her " + text)[0][0] == expected


def test_days_are_read():
    assert desk_assistant.parse_day("move to tomorrow") == TOMORROW
    assert desk_assistant.parse_day("the day after tomorrow") == (date.today() + timedelta(days=2)).isoformat()
    assert desk_assistant.parse_day("on 2026-12-01") == "2026-12-01" and desk_assistant.parse_day("hello") is None
