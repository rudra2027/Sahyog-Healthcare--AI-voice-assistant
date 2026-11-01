"""Small lookup tables: specialties, symptom keywords and medicines.

These are plain Python data. The fake offline model and the "fallback when the real
model fails" logic both use them, so the app keeps working without an LLM.
"""
from app.safety import normalise, phrase_present

# symptom keyword -> specialty. The first match with the most hits wins.
SYMPTOM_CROSSWALK = {
    "General Medicine": ["fever", "cold", "cough", "weakness", "tired", "unwell", "body ache", "flu", "nausea", "pukish", "feeling sick",
                         "not feeling well", "loss of appetite", "no appetite", "not eating", "dont feel like eating", "fatigue"],
    "Cardiology": ["palpitation", "blood pressure", "heart", "cholesterol", "swollen ankles"],
    "Orthopaedics": ["back pain", "knee", "joint", "fracture", "shoulder", "neck pain", "sprain", "hip pain"],
    "Physiotherapy": ["stiffness", "rehab", "posture", "recovery after surgery"],
    "Dermatology": ["rash", "itch", "skin", "acne", "hair fall"],
    "ENT": ["ear", "throat", "sinus", "nose", "hearing"],
    "Neurology": ["headache", "migraine", "numbness", "tingling", "dizzy", "dizziness"],
    "Gastroenterology": ["stomach", "acidity", "abdominal", "vomit", "diarrhoea", "constipation", "gut", "bloating", "indigestion", "heartburn"],
    "Pulmonology": ["wheeze", "asthma", "breathing trouble", "chronic cough"],
    "Paediatrics": ["my child", "my baby", "infant", "toddler"],
    "Gynaecology": ["period", "pregnan", "menstrual"],
    "Endocrinology": ["diabetes", "thyroid", "blood sugar"],
}
SPECIALTIES = list(SYMPTOM_CROSSWALK.keys())

# If a specialty has a natural "second opinion" partner, we offer it as an alternative.
ALTERNATIVES = {"Orthopaedics": "Physiotherapy", "Physiotherapy": "Orthopaedics", "Cardiology": "General Medicine"}


def rule_match(text: str) -> tuple[str, str | None, float]:
    """Match symptoms to a specialty by keyword counting. Returns (specialty, alternative, confidence).

    Confidence is deliberately simple: no keyword hit = 0.3 (too low, so we ask a
    clarifying question), one hit = 0.82, more hits = higher.
    """
    text = normalise(text)
    # "no fever" must NOT count as a fever, so negated words are skipped
    scores = {sp: sum(1 for word in words if phrase_present(text, word)) for sp, words in SYMPTOM_CROSSWALK.items()}
    # on a tie the more specific specialty wins: "my toddler is not eating" is Paediatrics, not General Medicine
    best = max(scores, key=lambda sp: (scores[sp], sp != "General Medicine"))
    hits = scores[best]
    if hits == 0:
        return "General Medicine", None, 0.3
    confidence = min(0.95, 0.62 + 0.2 * hits)
    return best, ALTERNATIVES.get(best), round(confidence, 2)


# molecule -> drug class. Used to tell "the doctor named a molecule" from "only a class".
MEDICINES = {
    "atorvastatin": "statin", "rosuvastatin": "statin", "simvastatin": "statin",
    "metformin": "antidiabetic", "amlodipine": "antihypertensive", "losartan": "antihypertensive",
    "aspirin": "antiplatelet", "paracetamol": "analgesic", "ibuprofen": "nsaid",
    "pantoprazole": "ppi", "cetirizine": "antihistamine", "azithromycin": "antibiotic",
}
# words a doctor might say instead of a molecule name
CLASS_WORDS = {"statin": "statin", "antibiotic": "antibiotic", "painkiller": "analgesic", "antacid": "ppi"}

# Starting stock (medicine, clinic, quantity). A zero shows the "out of stock" path.
STOCK_SEED = [
    ("atorvastatin", "Baner", 240), ("atorvastatin", "Kothrud", 90), ("rosuvastatin", "Baner", 60),
    ("metformin", "Baner", 500), ("amlodipine", "Baner", 150), ("losartan", "Baner", 75),
    ("aspirin", "Baner", 300), ("paracetamol", "Baner", 800), ("ibuprofen", "Baner", 0),
    ("pantoprazole", "Baner", 120), ("cetirizine", "Baner", 200), ("azithromycin", "Baner", 40),
    ("simvastatin", "Baner", 0),
]

SAMPLE_TRANSCRIPT = """Doctor: So the tightness in the chest comes on when you climb stairs and settles after a few minutes of rest?
Patient: Yes, two or three times this week. Nothing at rest.
Doctor: Blood pressure is 138 over 86 today. I'll start you on a low-dose statin, and I want an ECG and a lipid panel before we meet again in two weeks."""


# ----------------------------------------------------------------------------
# Making a model's specialty name match ours
# ----------------------------------------------------------------------------
# A model may say "Orthopedics", "Orthopaedic surgery" or "Physical Medicine". Our doctor table only knows the exact names in
# SPECIALTIES, so a different spelling would find NO doctors and wrongly send the patient to the front desk.
SPECIALTY_ALIASES = {
    "orthopedics": "Orthopaedics", "orthopedic": "Orthopaedics", "orthopaedic": "Orthopaedics", "orthopedic surgery": "Orthopaedics",
    "pediatrics": "Paediatrics", "pediatric": "Paediatrics", "paediatric": "Paediatrics", "gynecology": "Gynaecology", "obstetrics": "Gynaecology",
    "family medicine": "General Medicine", "internal medicine": "General Medicine", "general practice": "General Medicine", "general practitioner": "General Medicine",
    "physical medicine": "Physiotherapy", "physical medicine and rehabilitation": "Physiotherapy", "rehabilitation": "Physiotherapy", "physiatry": "Physiotherapy",
    "ear nose and throat": "ENT", "otolaryngology": "ENT", "skin": "Dermatology", "heart": "Cardiology", "cardiologist": "Cardiology",
    "lungs": "Pulmonology", "respiratory": "Pulmonology", "gastroenterologist": "Gastroenterology", "digestive": "Gastroenterology",
    "neurologist": "Neurology", "diabetes": "Endocrinology", "endocrinologist": "Endocrinology", "hormone": "Endocrinology",
}


def normalise_specialty(name: str | None) -> str | None:
    """Return one of SPECIALTIES for what a model said, or None when we cannot tell."""
    import difflib

    if not name:
        return None
    key = name.strip().lower()
    for known in SPECIALTIES:
        if key == known.lower():
            return known
    if key in SPECIALTY_ALIASES:
        return SPECIALTY_ALIASES[key]
    for alias, known in SPECIALTY_ALIASES.items():          # "orthopaedic surgery (spine)" contains "orthopaedic"
        if alias in key:
            return known
    close = difflib.get_close_matches(key, [s.lower() for s in SPECIALTIES], n=1, cutoff=0.75)
    return next((s for s in SPECIALTIES if s.lower() == close[0]), None) if close else None


# "Can I just see a general physician?" is a request, not a symptom. When the patient names the kind of doctor, we use it.
REQUEST_WORDS = {
    "General Medicine": ["general physician", "general doctor", "general practitioner", "family doctor", "family physician", "physician", "gp"],
    "Cardiology": ["cardiologist", "heart doctor", "heart specialist"], "Orthopaedics": ["orthopaedic", "orthopedic", "bone doctor", "bone specialist"],
    "Dermatology": ["dermatologist", "skin doctor", "skin specialist"], "ENT": ["ent doctor", "ent specialist", "ear nose throat doctor"],
    "Neurology": ["neurologist", "brain doctor"], "Gastroenterology": ["gastroenterologist", "stomach doctor", "gastro doctor"],
    "Pulmonology": ["pulmonologist", "lung doctor", "chest physician"], "Paediatrics": ["paediatrician", "pediatrician", "child doctor", "child specialist", "kids doctor"],
    "Gynaecology": ["gynaecologist", "gynecologist", "women's doctor"], "Endocrinology": ["endocrinologist", "diabetes doctor", "thyroid doctor"],
    "Physiotherapy": ["physiotherapist", "physio"],
}


def requested_specialty(text: str) -> str | None:
    """The specialty the patient asked for by name ("can I consult a general physician?"), or None."""
    words = " " + normalise(text) + " "
    for specialty, phrases in REQUEST_WORDS.items():
        if any(f" {phrase} " in words or f" {phrase}s " in words for phrase in phrases):
            return specialty
    return None
