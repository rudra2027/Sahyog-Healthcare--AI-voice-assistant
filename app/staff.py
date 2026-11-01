"""Administrator tool: decide who is a doctor or front-desk person.

Anyone can create an account on the website, and every new account is a PATIENT. Staff roles are never
granted from the browser. An administrator (you, on the server) grants them here:

    python -m app.staff users                          who has signed in so far, and their role
    python -m app.staff doctors                        the doctor ids you can attach a login to
    python -m app.staff add-doctor "Dr. Adarsh" Cardiology Baner     create a new doctor (with appointment slots)
    python -m app.staff promote sb-<id> doctor d31     make a login that doctor
    python -m app.staff promote sb-<id> frontdesk      make a login a front-desk person
    python -m app.staff promote sb-<id> patient        take a role away

The person must sign in once first (that creates their row). Their id is shown when they open /doctor or /desk.
"""
import sys

from app import database as db


def main(argv: list[str]) -> int:
    db.init_db()
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    command = argv[0]
    if command == "users":
        for u in db.rows("SELECT id, name, role, doctor_id FROM app_user WHERE id LIKE 'sb-%' ORDER BY role, name"):
            print(f"{u['id']}  {u['role']:<10} {u['name']}" + (f"  ({u['doctor_id']})" if u["doctor_id"] else ""))
        return 0
    if command == "doctors":
        for d in db.rows("SELECT id, name, specialty, clinic FROM doctor ORDER BY CAST(SUBSTR(id, 2) AS INTEGER)"):
            print(f"{d['id']:<5} {d['name']:<28} {d['specialty']:<16} {d['clinic']}")
        return 0
    if command == "add-doctor" and len(argv) == 4:
        print("created doctor", db.add_doctor(argv[1], argv[2], argv[3]))
        return 0
    if command == "promote" and len(argv) in (3, 4):
        try:
            user = db.set_staff_role(argv[1], argv[2], argv[3] if len(argv) == 4 else None)
        except ValueError as err:
            print("Error:", err)
            return 1
        print(f"OK: {user['name']} is now {user['role']}" + (f" ({user['doctor_id']})" if user.get("doctor_id") else ""))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
