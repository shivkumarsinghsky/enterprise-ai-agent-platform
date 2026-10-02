"""Scenario-based evaluation of agent behaviour (not only answers): routing, tool selection, authorization,
approvals and refusals. Each JSONL line:

{"name": "...", "role": "technician", "input": "...", "expect_agent": "reporting-agent",
 "expect_tools": ["maintenance_kpis"], "expect_status": "completed", "expect_in_answer": ["MTTR"],
 "approve_as": "supervisor"}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_platform.security import Principal
from agent_platform.service import AgentService


def run_scenarios(service: AgentService, path: str | Path) -> dict[str, Any]:
    results = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        run = service.start(Principal("acme", f"eval-{case['name']}", frozenset({case["role"]})), case["input"])
        if run.status == "awaiting_approval" and case.get("approve_as"):
            approved = case.get("approve", True)
            run = service.decide(
                run.run_id, Principal("acme", "eval-approver", frozenset({case["approve_as"]})), approved
            )
        tools = [t["tool"] for t in run.trace if t.get("node") == "tool"]
        agent = next((t["agent"] for t in run.trace if t.get("node") == "route"), None)
        answer = (run.result or {}).get("answer", "")
        failures = []
        if case.get("expect_agent") and agent != case["expect_agent"]:
            failures.append(f"agent {agent} != {case['expect_agent']}")
        if "expect_tools" in case and tools != case["expect_tools"]:
            failures.append(f"tools {tools} != {case['expect_tools']}")
        if case.get("expect_status", "completed") != run.status:
            failures.append(f"status {run.status}")
        failures += [f"answer missing '{p}'" for p in case.get("expect_in_answer", []) if p not in answer]
        results.append({"name": case["name"], "passed": not failures, "failures": failures})
    return {"total": len(results), "passed": sum(r["passed"] for r in results), "results": results}
