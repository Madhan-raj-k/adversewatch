"""AdverseWatch core: drug-class map, memory adapters (Hindsight + offline), flag logic, LLM explainer.
NOT MEDICAL ADVICE. Demo with FICTIONAL synthetic patients only."""
from __future__ import annotations
import os, re, json, datetime, logging

log = logging.getLogger("adversewatch")

# Deterministic, well-known classes only.
DRUG_CLASS = {
    "amoxicillin": "penicillin", "ampicillin": "penicillin", "penicillin": "penicillin", "cloxacillin": "penicillin",
    "cephalexin": "cephalosporin", "cefuroxime": "cephalosporin", "ceftriaxone": "cephalosporin", "cefixime": "cephalosporin",
    "ibuprofen": "nsaid", "diclofenac": "nsaid", "naproxen": "nsaid", "aspirin": "nsaid",
    "sulfamethoxazole": "sulfonamide", "cotrimoxazole": "sulfonamide",
    "azithromycin": "macrolide", "clarithromycin": "macrolide", "erythromycin": "macrolide",
    "ciprofloxacin": "fluoroquinolone", "levofloxacin": "fluoroquinolone", "ofloxacin": "fluoroquinolone",
    "enalapril": "ace_inhibitor", "ramipril": "ace_inhibitor", "lisinopril": "ace_inhibitor",
    "atorvastatin": "statin", "rosuvastatin": "statin", "simvastatin": "statin",
    "paracetamol": "analgesic_other", "cetirizine": "antihistamine",
}
CLASS_LABEL = {"penicillin": "Penicillins", "cephalosporin": "Cephalosporins", "nsaid": "NSAIDs",
               "sulfonamide": "Sulfonamides", "macrolide": "Macrolides", "fluoroquinolone": "Fluoroquinolones",
               "ace_inhibitor": "ACE inhibitors", "statin": "Statins", "analgesic_other": "Other analgesic",
               "antihistamine": "Antihistamine"}
# Known cautions (well-known, conservative): penicillin <-> cephalosporin cross-reactivity caution.
CROSS = {"penicillin": {"cephalosporin"}, "cephalosporin": {"penicillin"}}
REACTION_WORDS = ["rash", "swelling", "angioedema", "hives", "urticaria", "itch", "cough", "anaphylaxis",
                  "breathless", "vomit", "not working", "no effect", "ineffective", "reaction", "allergy", "allergic"]

def drug_class(drug: str) -> str | None:
    return DRUG_CLASS.get((drug or "").strip().lower())

PATIENTS = {
    "P001": {"id": "P001", "name": "Asha K. (FICTIONAL)", "age": 34, "note": "Learning-loop demo patient: starts with no history"},
    "P002": {"id": "P002", "name": "Ravi S. (FICTIONAL)", "age": 52, "note": "Seeded: ibuprofen -> facial swelling"},
    "P003": {"id": "P003", "name": "Meena D. (FICTIONAL)", "age": 27, "note": "Seeded: cotrimoxazole -> rash; azithromycin worked"},
}
SEED = [
    ("P002", "2026-08-10", "ibuprofen", "facial swelling and lip swelling within 2 hours", ""),
    ("P002", "2026-08-12", "paracetamol", "", "fever controlled, no reaction"),
    ("P003", "2026-06-02", "cotrimoxazole", "itchy rash on arms", ""),
    ("P003", "2026-06-05", "azithromycin", "", "infection resolved, tolerated well"),
]

def visit_text(pid, date, drug, reaction="", outcome=""):
    cls = drug_class(drug)
    s = f"Visit on {date}: fictional patient {pid} was prescribed {drug} (class: {CLASS_LABEL.get(cls, 'unknown')})."
    if reaction:
        s += f" ADVERSE REACTION to {drug}: {reaction}."
    if outcome:
        s += f" Outcome with {drug}: {outcome}."
    return s

def tag(pid):
    return f"patient:{pid}"

# ---------------- memory ----------------
class OfflineMemory:
    mode = "offline in-memory fallback (labelled; not Hindsight)"
    def __init__(self):
        self.items: list[dict] = []
    def retain(self, pid, text, date, doc_id=None):
        if doc_id and any(i.get("doc_id") == doc_id for i in self.items):
            return {"op": "retain", "skipped": "duplicate", "doc_id": doc_id}
        self.items.append({"pid": pid, "text": text, "date": date, "doc_id": doc_id})
        return {"op": "retain", "bank": "offline", "tags": [tag(pid)], "text": text}
    def recall(self, pid, query):
        hits = [i for i in self.items if i["pid"] == pid]
        return [{"text": i["text"], "date": i["date"]} for i in sorted(hits, key=lambda x: x["date"])]
    def timeline(self, pid):
        return self.recall(pid, "")

class HindsightMemory:
    def __init__(self, base_url, api_key, bank_id):
        from hindsight_client import Hindsight
        self.c = Hindsight(base_url=base_url, api_key=api_key, timeout=55)
        self.bank = bank_id
        self.mode = f"Hindsight (bank {bank_id}, patient-scoped tags)"
        try:
            self.c.create_bank(bank_id=bank_id, retain_mission=(
                "Memory of a small clinic. For each visit keep: patient id, date, drug prescribed, drug class, "
                "any adverse reaction (exact symptom) and what worked. Keep exact drug names and dates."))
        except Exception as e:  # noqa
            log.warning("bank config: %s", e)
    def retain(self, pid, text, date, doc_id=None):
        self.c.retain(bank_id=self.bank, content=text, timestamp=date + "T10:00:00Z", context="clinic visit log",
                      document_id=doc_id, tags=[tag(pid)], metadata={"patient": pid})
        return {"op": "retain", "bank": self.bank, "tags": [tag(pid)], "text": text}
    def recall(self, pid, query):
        r = self.c.recall(bank_id=self.bank, query=query, tags=[tag(pid)], tags_match="all_strict", budget="mid", max_tokens=2048)
        return [{"text": x.text, "date": (x.occurred_start or x.mentioned_at or "")[:10] if isinstance(x.occurred_start or x.mentioned_at, str) else str(x.occurred_start or "")[:10]}
                for x in (r.results or [])]
    def timeline(self, pid):
        r = self.c.list_memories(bank_id=self.bank, tags=[tag(pid)], tags_match="all_strict", limit=50)
        return [{"text": i.text, "date": str(i.occurred_start or i.mentioned_at or "")[:10]} for i in r.items]

def build_memory():
    key, url = os.getenv("HINDSIGHT_API_KEY"), os.getenv("HINDSIGHT_BASE_URL") or os.getenv("HINDSIGHT_API_URL")
    if key:
        try:
            return HindsightMemory(url or "https://api.hindsight.vectorize.io", key, os.getenv("HINDSIGHT_BANK_ID", "adversewatch-clinic-demo"))
        except Exception as e:  # noqa
            log.warning("Hindsight unavailable, using fallback: %s", e)
    return OfflineMemory()

# ---------------- flag logic ----------------
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")

def assess(drug: str, memories: list[dict]) -> dict:
    cls = drug_class(drug)
    flags, worked = [], []
    for m in memories:
        t = m["text"].lower()
        date = (_DATE.search(m["text"]) or [None, m.get("date", "")])[1] if _DATE.search(m["text"]) else m.get("date", "")
        mentioned = [d for d in DRUG_CLASS if re.search(rf"\b{d}\b", t)]
        has_reaction = any(w in t for w in REACTION_WORDS) and "no reaction" not in t
        for past in mentioned:
            pc = drug_class(past)
            if has_reaction and cls and pc:
                if past == drug.lower():
                    flags.append({"level": "HIGH", "why": f"Same drug: {past} caused a reaction", "past_drug": past, "date": date, "evidence": m["text"]})
                elif pc == cls:
                    flags.append({"level": "HIGH", "why": f"Same class ({CLASS_LABEL[cls]}): past reaction to {past}", "past_drug": past, "date": date, "evidence": m["text"]})
                elif cls in CROSS.get(pc, set()):
                    flags.append({"level": "CAUTION", "why": f"Known cross-reactivity caution {CLASS_LABEL[pc]} -> {CLASS_LABEL[cls]}: past reaction to {past}", "past_drug": past, "date": date, "evidence": m["text"]})
            elif not has_reaction and ("tolerated" in t or "resolved" in t or "controlled" in t or "worked" in t):
                worked.append({"drug": past, "date": date, "evidence": m["text"]})
    level = "HIGH" if any(f["level"] == "HIGH" for f in flags) else ("CAUTION" if flags else "NONE")
    return {"drug": drug, "drug_class": CLASS_LABEL.get(cls, "unknown (not in demo class map)"), "level": level,
            "flags": flags, "worked_before": worked}

# ---------------- LLM ----------------
def explain(patient: dict, result: dict, memories: list[dict]) -> tuple[str, str]:
    key = os.getenv("GROQ_API_KEY")
    if key:
        try:
            from openai import OpenAI
            c = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1", timeout=25, max_retries=0)
            r = c.chat.completions.create(model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"), temperature=0.2, max_tokens=500,
                messages=[{"role": "system", "content": "You assist a clinic doctor (decision support demo, NOT medical advice). "
                           "In <=90 words explain the deterministic flag below using ONLY the recalled memories. Cite the past visit date and drug. "
                           "Mention what worked before if listed. Do not invent drug facts or statistics."},
                          {"role": "user", "content": json.dumps({"patient": patient["name"], "assessment": result, "recalled_memories": memories})}])
            txt = re.sub(r"<think>.*?</think>", "", r.choices[0].message.content or "", flags=re.S).strip()
            if txt:
                return txt, "Groq " + os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        except Exception as e:  # noqa
            log.warning("Groq failed: %s", e)
    # mock explainer (template)
    if result["level"] == "NONE":
        s = f"No reaction in memory linked to {result['drug']} or its class ({result['drug_class']})."
    else:
        f = result["flags"][0]
        s = f"{result['level']}: {f['why']} (visit {f['date']}). Review before prescribing {result['drug']}."
    if result["worked_before"]:
        w = result["worked_before"][0]
        s += f" Previously tolerated: {w['drug']} (visit {w['date']})."
    return s, "mock LLM (template; set GROQ_API_KEY for Groq)"
