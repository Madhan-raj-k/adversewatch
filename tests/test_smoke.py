import os, sys
os.environ.pop("HINDSIGHT_API_KEY", None); os.environ.pop("GROQ_API_KEY", None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
from fastapi.testclient import TestClient
import core, index

c = TestClient(index.app)

def test_class_map():
    assert core.drug_class("Amoxicillin") == "penicillin" and core.drug_class("cephalexin") == "cephalosporin"

def test_learning_loop_and_memory_off_vs_on():
    pid = "P001-test"
    r = c.post("/api/check", json={"patient_id": pid, "drug": "amoxicillin"}).json()
    assert r["assessment"]["level"] == "NONE"
    c.post("/api/visit", json={"patient_id": pid, "drug": "amoxicillin", "reaction": "itchy rash", "date": "2026-11-01"})
    on = c.post("/api/check", json={"patient_id": pid, "drug": "ampicillin", "memory": True}).json()
    off = c.post("/api/check", json={"patient_id": pid, "drug": "ampicillin", "memory": False}).json()
    assert on["assessment"]["level"] == "HIGH" and on["assessment"]["flags"][0]["date"] == "2026-11-01"
    assert off["assessment"]["level"] == "NONE"
    ceph = c.post("/api/check", json={"patient_id": pid, "drug": "cephalexin"}).json()
    assert ceph["assessment"]["level"] == "CAUTION"

def test_seeded_patient_worked_before():
    r = c.post("/api/check", json={"patient_id": "P003", "drug": "sulfamethoxazole"}).json()
    assert r["assessment"]["level"] == "HIGH"
    assert any(w["drug"] == "azithromycin" for w in r["assessment"]["worked_before"])

def test_home_and_health():
    assert c.get("/").status_code == 200 and "NOT MEDICAL ADVICE" in c.get("/").text
    assert "fallback" in c.get("/api/health").json()["memory_mode"]
