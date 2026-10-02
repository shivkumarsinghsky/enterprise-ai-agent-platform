"""CLI: python -m agent_platform <serve|ask|eval>

serve                                   run the API
ask "question" [--role technician] [--agent reporting-agent] [--approve-as supervisor]
eval eval/scenarios.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from agent_platform.config import get_settings
from agent_platform.llm import build_chat_model
from agent_platform.security import Principal
from agent_platform.service import build_service


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="agent_platform")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--role", default="technician")
    ask.add_argument("--agent", default=None)
    ask.add_argument("--approve-as", default=None, help="role of a second user who approves pending actions")
    ev = sub.add_parser("eval")
    ev.add_argument("scenarios")
    args = parser.parse_args(argv)

    settings = get_settings()
    logging.basicConfig(level=settings.log_level, stream=sys.stderr)
    service = build_service(settings, build_chat_model(settings))

    if args.cmd == "serve":
        import uvicorn

        from agent_platform.api import create_app

        uvicorn.run(create_app(service, settings.api_keys), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
    elif args.cmd == "ask":
        run = service.start(Principal("acme", "cli-user", frozenset({args.role})), args.question, args.agent)
        if run.status == "awaiting_approval":
            print(f"Pending approval: {json.dumps(run.pending)}")
            if args.approve_as:
                run = service.decide(run.run_id, Principal("acme", "cli-approver", frozenset({args.approve_as})), True)
        if run.result:
            print(run.result["answer"])
            for s in run.result["sources"]:
                print(f"  [{s['source']}] {s['title']} — {s['section']}")
            for a in run.result["actions"]:
                print(f"  action: {a['tool']} {a['status']}")
    elif args.cmd == "eval":
        from agent_platform.evaluation import run_scenarios

        report = run_scenarios(service, args.scenarios)
        print(json.dumps(report, indent=2))
        sys.exit(0 if report["passed"] == report["total"] else 1)


if __name__ == "__main__":
    main()
