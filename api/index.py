import os, sys, datetime, uuid
sys.path.insert(0, os.path.dirname(__file__))
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import core

app = FastAPI(title="AdverseWatch (demo, not medical advice)")
MEM = core.build_memory()
if isinstance(MEM, core.OfflineMemory):  # offline fallback: preload fictional seed history
    for _p, _d, _dr, _r, _o in core.SEED:
        MEM.retain(_p, core.visit_text(_p, _d, _dr, _r, _o), _d, doc_id=f"seed-{_p}-{_d}-{_dr}")
DISCLAIMER = "DEMO ONLY - NOT MEDICAL ADVICE. Fictional synthetic patients. Not clinically validated."

def _pid(pid):
    base = pid.split("-")[0]
    if base not in core.PATIENTS:
        raise HTTPException(404, "unknown patient")
    return base

class Visit(BaseModel):
    patient_id: str
    drug: str
    reaction: str = ""
    outcome: str = ""
    date: str = ""

class Check(BaseModel):
    patient_id: str
    drug: str
    memory: bool = True
    client_log: list[dict] = []  # offline-fallback only: visits this browser retained (stateless serverless)

@app.get("/api/health")
def health():
    return {"memory_mode": MEM.mode, "llm_mode": "Groq" if os.getenv("GROQ_API_KEY") else "mock LLM", "disclaimer": DISCLAIMER}

@app.get("/api/patients")
def patients():
    return {"patients": list(core.PATIENTS.values()), "disclaimer": DISCLAIMER}

@app.post("/api/seed")
def seed():
    ops = []
    for pid, date, drug, reaction, outcome in core.SEED:
        ops.append(MEM.retain(pid, core.visit_text(pid, date, drug, reaction, outcome), date, doc_id=f"seed-{pid}-{date}-{drug}"))
    return {"seeded": len(ops), "memory_ops": ops}

@app.post("/api/visit")
def visit(v: Visit):
    _pid(v.patient_id)
    date = v.date or datetime.date.today().isoformat()
    text = core.visit_text(v.patient_id, date, v.drug, v.reaction, v.outcome)
    op = MEM.retain(v.patient_id, text, date, doc_id=f"v-{uuid.uuid4().hex[:8]}")
    return {"memory_op": op}

@app.post("/api/check")
def check(c: Check):
    p = core.PATIENTS[_pid(c.patient_id)]
    if c.memory:
        query = f"adverse reactions or drugs that worked for patient {c.patient_id}; {c.drug}; {core.CLASS_LABEL.get(core.drug_class(c.drug) or '', '')}"
        mems = MEM.recall(c.patient_id, query)
        if isinstance(MEM, core.OfflineMemory):
            seen = {m["text"] for m in mems}
            mems += [{"text": x.get("text", ""), "date": x.get("date", "")} for x in c.client_log
                     if x.get("pid") == c.patient_id and x.get("text") not in seen]
    else:
        query, mems = None, []
    result = core.assess(c.drug, mems)
    text, llm = core.explain(p, result, mems)
    return {"memory": c.memory, "recall_query": query, "recalled": mems, "assessment": result,
            "explanation": text, "llm_mode": llm, "memory_mode": MEM.mode if c.memory else "OFF", "disclaimer": DISCLAIMER}

@app.get("/api/timeline/{pid}")
def timeline(pid: str):
    _pid(pid)
    return {"items": MEM.timeline(pid)}

@app.get("/")
def home():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "public", "index.html"))
