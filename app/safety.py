"""Safety rules written as plain code (not prompts).

A prompt can be argued with; code cannot. So the most important rules live here:
red-flag detection, personal-data scrubbing, prompt-injection detection, and the
"we do not diagnose or give doses" check.
"""
import re
from dataclasses import dataclass

# ----------------------------------------------------------------------------
# 1. Red flags: symptoms that mean "stop and send to emergency care"
# ----------------------------------------------------------------------------
# A rule fires when EVERY group in it has at least one phrase present in the text.
# Example: ["arm"], ["ache", "pain"], ["breathless"] means arm AND pain AND breathless.
RED_FLAG_RULES: dict[str, list[list[str]]] = {
    "arm pain with breathlessness": [["arm"], ["ache", "pain", "numb", "heavy"], ["breathless", "short of breath", "shortness of breath", "saans"]],
    "chest pain or tightness": [["chest pain", "chest pains", "chest tightness", "chest pressure", "chest discomfort", "chest hurts",
                                 "chest is hurting", "pain in my chest", "pain in the chest", "pain in chest", "heart attack",
                                 "seene mein dard", "heart pain"]],
    "stroke signs": [["droop", "slurred", "slurring", "cannot lift arm", "can't lift", "weakness on one side"]],
    "cannot breathe": [["cannot breathe", "can't breathe", "not breathing", "gasping", "saans nahi"]],
    "heavy bleeding": [["heavy bleeding", "bleeding will not stop", "vomiting blood", "coughing blood"]],
    "unresponsive or seizure": [["unconscious", "not responding", "seizure", "fainted"]],
    "self-harm": [["want to die", "suicide", "kill myself", "end my life"]],
    "severe allergic reaction": [["throat swelling", "swollen tongue", "anaphylaxis"]],
}
_NEGATIONS = {"no", "not", "without", "denies", "nahi", "never"}


def phrase_present(text: str, phrase: str) -> bool:
    """True if `phrase` appears in `text` and is not negated ("no chest pain" is not a flag).

    We only look at the two words just before the phrase, so "no fever but chest pain"
    is still flagged while "denies any chest pain" is not.
    """
    start = 0
    while (idx := text.find(phrase, start)) != -1:
        words_before = [w.strip(",.;:") for w in text[:idx].split()[-2:]]
        if not any(word in _NEGATIONS for word in words_before):
            return True
        start = idx + 1
    return False


def normalise(text: str) -> str:
    """Lower case, punctuation and repeated spaces removed. "Chest   PAIN!!" and "chest-pain" both become "chest pain".
    (A LangSmith trace showed "chest  pain" with two spaces slipping past the red-flag rule: never match raw text.)"""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", text.lower())).strip()


def check_red_flags(text: str) -> list[str]:
    """Return the names of every red-flag rule that fires. An empty list means all clear."""
    text = normalise(text)
    fired = []
    for name, groups in RED_FLAG_RULES.items():
        if all(any(phrase_present(text, phrase) for phrase in group) for group in groups):
            fired.append(name)
    return fired


EMERGENCY_REPLY = (
    "These symptoms need urgent attention now. Please call 112 or go to the nearest "
    "emergency department. I have alerted Sahyog's emergency desk, and a nurse will call you within two minutes."
)


def extract_age(text: str) -> int | None:
    """Pull an age out of free text like "my mother (62)" or "aged 62" or "62 years"."""
    for pattern in (r"\((\d{1,3})\)", r"\bage[d]?\s*(\d{1,3})\b", r"\b(\d{1,3})\s*(?:years?|yrs?|y/o|yo)\b"):
        match = re.search(pattern, text.lower())
        if match and 0 < int(match.group(1)) < 120:
            return int(match.group(1))
    return None


# ----------------------------------------------------------------------------
# 2. Scrubbing personal data before anything leaves the building
# ----------------------------------------------------------------------------
@dataclass
class Scrubbed:
    text: str
    count: int  # how many things were replaced


def scrub(text: str, names: list[str] | None = None) -> Scrubbed:
    """Replace phone numbers, ABHA ids, emails and known names with placeholders."""
    count = 0
    patterns = [
        (r"\b(?:\+91[- ]?)?[6-9]\d{9}\b", "<PHONE>"),
        (r"\b\d{2}-\d{4}-\d{4}-\d{4}\b", "<ABHA_ID>"),
        (r"[\w.+-]+@[\w-]+\.[\w.-]+", "<EMAIL>"),
    ]
    for pattern, token in patterns:
        text, n = re.subn(pattern, token, text)
        count += n
    for name in names or []:
        if name:
            text, n = re.subn(re.escape(name), "<PATIENT>", text, flags=re.IGNORECASE)
            count += n
    return Scrubbed(text, count)


# ----------------------------------------------------------------------------
# 3. Prompt injection and clinical-advice checks
# ----------------------------------------------------------------------------
_INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior|your) (instructions|rules)|reveal .*other patients?|"
    r"show .*other patients?|disregard .*instructions|system prompt)",
    re.IGNORECASE,
)


def looks_like_injection(text: str) -> bool:
    """True if text tries to give the AI new orders (e.g. hidden inside an uploaded PDF)."""
    return bool(_INJECTION.search(text))


_CLINICAL = re.compile(
    r"\b(dose|dosage|how much (should|can) i take|diagnos\w*|what disease|prescribe|which medicine should)\b"
    # "do I have diabetes?" asks for a diagnosis; "when do I have to see the doctor?" or "do I have an appointment?" does not
    r"|\bdo i have (?!to\b|an? (appointment|visit|follow|booking|report)|any (appointment|visit|follow|booking|report|message)|my\b)",
    re.IGNORECASE,
)


def is_clinical_request(text: str) -> bool:
    """True if the user asks for a diagnosis or a dose. The assistant never answers those."""
    return bool(_CLINICAL.search(text))


# ----------------------------------------------------------------------------
# 4. Making text easy to speak
# ----------------------------------------------------------------------------
_SPOKEN = [
    (r"\b112\b", "one one two"), (r"\bER\b", "emergency room"), (r"\bSMS\b", "text message"),
    (r"\bDr\.", "Doctor"), (r"\bLDL\b", "L D L"), (r"\bHDL\b", "H D L"), (r"mg/dL", "milligrams per decilitre"),
    (r"\bBP\b", "blood pressure"), (r"\bECG\b", "E C G"), (r"[*_`#]", ""),
]


def speakable(text: str) -> str:
    """Expand numbers and abbreviations: the Phonon voice model does not do this by itself."""
    for pattern, spoken in _SPOKEN:
        text = re.sub(pattern, spoken, text)
    return re.sub(r"\s+", " ", text).strip()


# ----------------------------------------------------------------------------
# 5. Turning a question into search words
# ----------------------------------------------------------------------------
_STOPWORDS = {
    "what", "does", "the", "report", "reports", "say", "about", "her", "his", "mum", "mom", "mother", "father",
    "tell", "show", "with", "that", "this", "have", "from", "please", "can", "you", "are", "was", "were", "how",
    "my", "me", "is", "it", "of", "in", "on", "for", "and", "a", "an", "to", "any", "there", "which", "when",
}


def question_terms(question: str) -> list[str]:
    """Keep the meaningful words of a question: "What does the report say about her cholesterol?" -> ["cholesterol"]."""
    words = re.findall(r"[a-zA-Z0-9]+", question.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 2]
