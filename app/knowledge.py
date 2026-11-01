"""What the assistant may say about doctors, specialties and how the hospital works.

Patients ask general questions: "what does a general physician do?", "what should I bring to my visit?", "can I cancel?".
Those answers are NOT guessed. Each fact below is written once, here, by a person, and the model only phrases it
(see app/graphs/info.py). Anything not in this file (fees, insurance, opening hours of a clinic) is answered with
"I do not have that, the front desk can tell you" instead of an invention.
"""
import re

from app.formulary import REQUEST_WORDS, SPECIALTIES

# What each kind of doctor looks after, in plain words that sound natural when read aloud.
SPECIALTY_INFO = {
    "General Medicine": ("A general physician, also called a family doctor or GP, is the first doctor most adults see. They look at a wide range of "
                         "problems such as fevers, infections, stomach upsets, tiredness, blood pressure and sugar checks, arrange basic tests, "
                         "and refer you to a specialist if you need one. If you are not sure which doctor to see, start here."),
    "Cardiology": ("A cardiologist looks after the heart and blood vessels: high blood pressure, high cholesterol, palpitations and heart disease. "
                   "Chest pain with sweating or breathlessness is an emergency, so call 112 instead of booking."),
    "Orthopaedics": "An orthopaedic doctor looks after bones, joints and muscles: back, neck, knee and shoulder pain, fractures and sprains.",
    "Physiotherapy": "A physiotherapist helps you recover movement and strength with guided exercise, for example after an injury or surgery, or for stiffness and posture.",
    "Dermatology": "A dermatologist looks after the skin, hair and nails: rashes, itching, acne and hair fall.",
    "ENT": "An ENT doctor looks after the ear, nose and throat: ear pain, hearing problems, sinus trouble and sore throats.",
    "Neurology": "A neurologist looks after the brain and nerves: long-lasting headaches and migraines, numbness or tingling, dizziness and seizures.",
    "Gastroenterology": "A gastroenterologist looks after the digestive system: stomach pain, acidity, constipation, diarrhoea and liver problems.",
    "Pulmonology": "A pulmonologist looks after the lungs and breathing: asthma, wheezing and a cough that will not go away.",
    "Paediatrics": "A paediatrician looks after babies, children and teenagers: fevers, growth, vaccines and childhood illnesses.",
    "Gynaecology": "A gynaecologist looks after women's reproductive health: periods, pregnancy care and related problems.",
    "Endocrinology": "An endocrinologist looks after hormones: diabetes, thyroid problems and other hormone conditions.",
}

# (words that point at it, the answer). The first words listed are the strongest.
FAQ = [
    (("bring", "carry", "prepare", "ready", "before my visit", "before the visit", "what to take"),
     "Bring a photo ID, any earlier reports or prescriptions, a list of the medicines you take, and arrive about ten minutes early. "
     "You can also upload reports in the Records section so your doctor can see them before the visit."),
    (("book", "booking", "make an appointment", "get an appointment", "how do i see"),
     "Tell me what is wrong, or which kind of doctor you want. I will suggest the right specialty, offer three doctors with their next free times, and you choose one. You need to be signed in."),
    (("cancel", "reschedule", "change my appointment", "move my appointment"),
     "Open My visits on the website and press Cancel (twice, to be safe). Then tell me what the visit is for and I will find you a new time."),
    (("follow up", "followup", "revisit", "come back", "after tests", "after the tests", "after my tests"),
     "After tests your doctor can ask you to come back. A follow-up appears under My visits, and you can upload your test results in the Records section before you go."),
    (("upload", "reports", "report", "documents", "scan", "share my reports", "send my reports"),
     "Open the Records section, add a PDF or text report, and it is read within a minute. Doctors you have a booking with can see it, and you can ask me about it."),
    (("privacy", "private", "consent", "who can see", "safe", "secure"),
     "Only you can see your records, plus doctors you have a booking with. Every time a record is opened it is written to an audit trail."),
    (("emergency", "urgent", "ambulance", "serious"),
     "In an emergency call 112 or go to the nearest emergency department. If you describe warning signs such as chest pain or trouble breathing, I will tell you to do that straight away."),
    (("clinic", "clinics", "location", "locations", "where are you", "branch", "address", "city", "cities"),
     "We have clinics in Baner, Kothrud, Wakad and Hadapsar in Pune, and Banjara Hills and Gachibowli in Hyderabad."),
    (("time", "timing", "timings", "hours", "open", "slot", "slots", "when are you"),
     "Appointments run from 9 in the morning to 5:30 in the evening, in half-hour slots. Tell me what you need and I will show the next free times."),
    (("language", "languages", "hindi", "marathi", "english", "speak"),
     "Our doctors speak English and Hindi, and some also speak Marathi. You can talk to me in English; I understand common Hindi words too."),
    (("fee", "fees", "cost", "price", "charge", "charges", "pay", "payment", "insurance", "cashless", "bill"),
     "I do not have fees or insurance details. The front desk can tell you, and they can also help with payment questions."),
    (("video", "online consultation", "telemedicine", "teleconsultation", "home visit"),
     "I do not have details about video consultations or home visits. The front desk can tell you what is available."),
    (("who are you", "what can you do", "what are you", "help me", "how can you help", "how do you work"),
     "I am Sahyog, the hospital's assistant. I can find you a doctor and book a visit, show your visits and follow-ups, share details with your doctor, and answer questions about your reports. I never diagnose or give doses."),
]

STOP = {"what", "does", "do", "the", "a", "an", "is", "are", "of", "to", "i", "my", "me", "you", "can", "how", "who", "why", "which", "when", "about", "for", "and", "in", "it", "tell", "please"}


def _normalise(text: str) -> str:
    return " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", text.lower())).strip() + " "


def specialties_mentioned(text: str) -> list[str]:
    """Which specialties does the question name (by name, or by 'cardiologist', 'skin doctor', 'GP'...)?"""
    words = _normalise(text)
    found = []
    for specialty in SPECIALTIES:
        phrases = [specialty.lower()] + REQUEST_WORDS.get(specialty, [])
        if any(f" {p} " in words or f" {p}s " in words for p in phrases):
            found.append(specialty)
    return found


def lookup(text: str) -> tuple[list[str], str | None]:
    """The facts that answer this question, and a specialty worth offering. Both come only from this file."""
    words = _normalise(text)
    facts = []
    specialties = specialties_mentioned(text)
    for specialty in specialties[:2]:
        facts.append(SPECIALTY_INFO[specialty])
    scored = []
    for triggers, answer in FAQ:
        hits = sum(1 for t in triggers if f" {t} " in words or f" {t}s " in words)
        if hits:
            scored.append((hits, answer))
    for _, answer in sorted(scored, key=lambda x: x[0], reverse=True)[:2]:
        facts.append(answer)
    return facts, (specialties[0] if specialties else None)


NO_FACT = ("I do not have that information. The front desk can help, and I can find you a doctor or answer questions "
           "about specialties, booking, your visits and your reports.")
