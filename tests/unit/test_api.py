import json

from fastapi.testclient import TestClient

from agent_platform.api import create_app

KEYS = json.dumps(
    {
        "tech-key-000001": {"tenant": "acme", "user": "tara", "roles": ["technician"]},
        "super-key-00001": {"tenant": "acme", "user": "sam", "roles": ["supervisor"]},
        "globex-key-0001": {"tenant": "globex", "user": "gina", "roles": ["supervisor"]},
    }
)
TECH = {"x-api-key": "tech-key-000001"}
SUP = {"x-api-key": "super-key-00001"}
GLX = {"x-api-key": "globex-key-0001"}


def test_api_flow(service):
    c = TestClient(create_app(service, KEYS))
    assert c.get("/v1/agents").status_code == 401
    assert len(c.get("/v1/agents", headers=TECH).json()) == 4
    tools = {t["name"]: t for t in c.get("/v1/tools", headers=TECH).json()}
    assert tools["create_work_order"]["requiresApproval"] and tools["maintenance_kpis"]["allowed"]

    r = c.post("/v1/runs", json={"input": "Create a work order for P-101: loose guard"}, headers=TECH).json()
    assert r["status"] == "awaiting_approval"
    run_id = r["runId"]
    assert c.post(f"/v1/runs/{run_id}/decision", json={"approved": True}, headers=TECH).status_code == 403
    assert c.get(f"/v1/runs/{run_id}", headers=GLX).status_code == 404
    approved = c.post(f"/v1/runs/{run_id}/decision", json={"approved": True, "comment": "go"}, headers=SUP).json()
    assert approved["status"] == "completed" and approved["result"]["actions"][0]["status"] == "executed"
    assert c.post(f"/v1/runs/{run_id}/decision", json={"approved": True}, headers=SUP).status_code == 409
    trace = c.get(f"/v1/runs/{run_id}", headers=TECH).json()["trace"]
    assert [t["node"] for t in trace][:3] == ["guard_input", "route", "agent"]

    assert c.post("/v1/runs", json={"input": "hi", "agent": "nope"}, headers=TECH).status_code == 400
    assert c.post("/v1/runs", json={"input": "   "}, headers=TECH).status_code == 400
    assert "agent_runs_total" in c.get("/metrics").text
