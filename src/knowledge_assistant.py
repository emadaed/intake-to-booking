"""Staff knowledge assistant: extractive answers from approved documents, with code-level guardrails.

It never writes free text. It returns a passage copied word for word from a CURRENT approved document,
together with the document ID, title and effective date. Guardrails run in this fixed order, in plain code:

    1. emergency wording      -> fixed safety message, no search
    2. patient identifiers    -> refuse (the question is never echoed back)
    3. clinical questions     -> refuse, route to the clinical supervisor
    4. retrieval (BM25)       -> abstain when evidence is weak
    5. otherwise              -> quote the best passage + citation

Honest limit: it shows the closest approved passage. It cannot judge whether that passage answers
the question, so every answer says to confirm it fits.

Run:   python src/knowledge_assistant.py "How do I verify insurance?"
       python src/knowledge_assistant.py --list
"""
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "knowledge"

REQUIRED_FIELDS = ("id", "title", "status", "effective_date", "owner")
VALID_STATUS = {"current", "superseded", "draft"}

# ---------------------------------------------------------------- text handling
STOPWORDS = set("""a an the and or of to in on for with at by from is are was were be been do does did can could
should would will i we you he she it they me my our your their this that these those what how when where who which
there here as if about into any some than then so not please tell much need needed""".split())

# small, explicit word map so matching is predictable (no hidden stemmer)
CANON = {
    "verification": "verify", "verifying": "verify", "verified": "verify", "verifies": "verify", "check": "verify",
    "cancellation": "cancel", "cancellations": "cancel", "cancelled": "cancel", "canceled": "cancel",
    "cancelling": "cancel", "canceling": "cancel", "cancels": "cancel",
    "rescheduling": "reschedule", "rescheduled": "reschedule", "reschedules": "reschedule",
    "scheduling": "schedule", "scheduled": "schedule",
    "charge": "fee", "charged": "fee", "charges": "fee", "penalty": "fee", "penalties": "fee",
    "escalate": "escalate", "escalation": "escalate", "escalating": "escalate",
    "insurer": "insurance", "insured": "insurance",
    "noshow": "noshow", "noshows": "noshow",
    "browsers": "browser", "emails": "email",
}


def tokenize(text):
    text = text.lower().replace("no-show", "noshow").replace("no show", "noshow")
    out = []
    for tok in re.findall(r"[a-z0-9']+", text):
        tok = tok.replace("'s", "").strip("'")
        if not tok or tok in STOPWORDS:
            continue
        tok = CANON.get(tok, tok)
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        out.append(tok)
    return out


# ---------------------------------------------------------------- documents
@dataclass
class Chunk:
    doc_id: str
    title: str
    effective_date: str
    text: str
    tokens: list = field(default_factory=list)


class KnowledgeBaseError(Exception):
    pass


def parse_document(path):
    raw = Path(path).read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.S)
    if not m:
        raise KnowledgeBaseError(f"{Path(path).name}: missing header block")
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    missing = [f for f in REQUIRED_FIELDS if not meta.get(f)]
    if missing:
        raise KnowledgeBaseError(f"{Path(path).name}: missing header fields {missing}")
    if meta["status"] not in VALID_STATUS:
        raise KnowledgeBaseError(f"{Path(path).name}: invalid status '{meta['status']}'")
    try:
        meta["_date"] = date.fromisoformat(meta["effective_date"])
    except ValueError:
        raise KnowledgeBaseError(f"{Path(path).name}: effective_date must be YYYY-MM-DD")
    return meta, m.group(2).strip()


class KnowledgeBase:
    """Loads documents and keeps ONLY those that are current and already in effect."""

    def __init__(self, folder=DEFAULT_DIR, today=None):
        self.today = today or date.today()
        self.docs, self.excluded, self.chunks = [], [], []
        ids = set()
        for path in sorted(Path(folder).glob("*.md")):
            if path.name.startswith("_"):
                continue
            meta, body = parse_document(path)
            if meta["id"] in ids:
                raise KnowledgeBaseError(f"duplicate document id {meta['id']}")
            ids.add(meta["id"])
            if meta["status"] != "current":
                self.excluded.append((meta["id"], f"status is {meta['status']}"))
                continue
            if meta["_date"] > self.today:
                self.excluded.append((meta["id"], f"not effective until {meta['effective_date']}"))
                continue
            self.docs.append(meta)
            for block in re.split(r"\n\s*\n", body):
                block = block.strip()
                if block:
                    toks = tokenize(meta["title"] + " " + block)
                    self.chunks.append(Chunk(meta["id"], meta["title"], meta["effective_date"], block, toks))
        self._build_index()

    def _build_index(self):
        n = len(self.chunks)
        self.avgdl = (sum(len(c.tokens) for c in self.chunks) / n) if n else 0
        self.df = {}
        for c in self.chunks:
            for t in set(c.tokens):
                self.df[t] = self.df.get(t, 0) + 1
        self.vocab = set(self.df)

    def score(self, q_tokens, chunk, k1=1.5, b=0.75):
        n, s = len(self.chunks), 0.0
        for t in set(q_tokens):
            tf = chunk.tokens.count(t)
            if not tf:
                continue
            idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
            s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(chunk.tokens) / self.avgdl))
        return s

    def search(self, question):
        q = tokenize(question)
        terms = sorted(set(q))
        ranked = []
        for c in self.chunks:
            matched = [t for t in terms if t in c.tokens]
            ranked.append((self.score(q, c), len(matched) / len(terms) if terms else 0.0, c))
        ranked.sort(key=lambda r: r[0], reverse=True)
        return ranked


# ---------------------------------------------------------------- guardrails
EMERGENCY = re.compile(r"\b(suicid\w*|self[- ]?harm\w*|overdos\w*|in danger|danger to (?:self|himself|herself)|emergency)\b", re.I)

PRIVACY_PATTERNS = [
    ("an email address", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("a phone number", re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)(?!\d)")),
    ("an ID-style number", re.compile(r"\b\d{3}-\d{2}-\d{4}\b|\b\d{6,}\b")),
    ("a date of birth", re.compile(r"\b(?:dob|date of birth|born on)\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b", re.I)),
    ("what looks like a patient name", re.compile(r"\b(?:patient|client|member)\s+(?:named\s+)?[A-Z][a-z]{2,}")),
]

CLINICAL = re.compile(
    r"\b(diagnos\w*|medicat\w*|medicine|dosage|dose|symptom\w*|prescri\w*|side effects?|lab results?|blood sugar|a1c"
    r"|meal plans?|diet plans?)\b|what (?:should|can|must) (?:the |a )?(?:patient|client|she|he|they)\s+eat", re.I)

EMERGENCY_MESSAGE = ("If someone may be in immediate danger, call local emergency services first, then tell the "
                     "clinical supervisor as soon as possible. Do not type patient details here. "
                     "See the escalation policy (POL-005) for the full steps.")


@dataclass
class Answer:
    status: str            # answered | emergency | refused_privacy | refused_clinical | no_evidence
    message: str
    citations: list = field(default_factory=list)   # dicts: doc_id, title, effective_date, quote
    also_see: list = field(default_factory=list)


MIN_SCORE = 1.0
MIN_MATCH_RATIO = 0.5


def ask(question, kb):
    if EMERGENCY.search(question):
        return Answer("emergency", EMERGENCY_MESSAGE)

    found = [label for label, rx in PRIVACY_PATTERNS if rx.search(question)]
    if found:
        # the question is NOT echoed back, only the kinds of identifiers detected
        return Answer("refused_privacy",
                      f"I can't process this question because it appears to contain {', '.join(found)}. "
                      "Please remove patient details and ask again in general terms, or use the internal case number.")

    if CLINICAL.search(question):
        return Answer("refused_clinical",
                      "This is a clinical question, so I can't answer it. Please ask the clinical supervisor.")

    ranked = kb.search(question)
    if not ranked or not tokenize(question):
        return Answer("no_evidence", "I couldn't find this in the approved documents. Please ask the operations manager.")
    score, ratio, best = ranked[0]
    if score < MIN_SCORE or ratio < MIN_MATCH_RATIO:
        return Answer("no_evidence",
                      "I couldn't find this in the approved documents, so I won't guess. "
                      "Please ask the operations manager.")

    cite = {"doc_id": best.doc_id, "title": best.title, "effective_date": best.effective_date, "quote": best.text}
    also = []
    for s, r, c in ranked[1:]:
        if c.doc_id != best.doc_id and c.doc_id not in [a[0] for a in also] and s >= 0.6 * score and r >= MIN_MATCH_RATIO:
            also.append((c.doc_id, c.title))
        if len(also) == 2:
            break
    return Answer("answered",
                  "Closest approved passage (confirm it fits your situation before acting on it):",
                  [cite], also)


def format_answer(a):
    lines = [a.message]
    for c in a.citations:
        lines += ["", f'  "{c["quote"]}"', f'  Source: {c["doc_id"]} - {c["title"]} (effective {c["effective_date"]}, synthetic demo document)']
    if a.also_see:
        lines += ["", "Also see: " + "; ".join(f"{i} - {t}" for i, t in a.also_see)]
    return "\n".join(lines)


def main(argv):
    kb = KnowledgeBase()
    if len(argv) < 2:
        sys.exit('Usage: python src/knowledge_assistant.py "your question"   |   --list')
    if argv[1] == "--list":
        print(f"Searchable documents (as of {kb.today}):")
        for d in kb.docs:
            print(f"  {d['id']}  {d['title']}  (effective {d['effective_date']})")
        for doc_id, why in kb.excluded:
            print(f"  not searched: {doc_id} ({why})")
        return
    print(format_answer(ask(" ".join(argv[1:]), kb)))


if __name__ == "__main__":
    main(sys.argv)
