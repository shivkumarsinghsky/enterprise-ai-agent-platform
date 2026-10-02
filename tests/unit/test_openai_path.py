"""Exercises the real-LLM path (LangChain ChatOpenAI → OpenAI-compatible HTTP API) against a scripted fake server:
structured-output routing, native tool calling, and the final answer."""

import json

import httpx

from agent_platform.config import Settings
from agent_platform.service import build_service

from ..conftest import TECH


def fake_openai(requests: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if "response_format" in body:  # router: structured output
            content = json.dumps({"agent": "reporting-agent", "reason": "asks for reliability KPIs"})
            message = {"role": "assistant", "content": content}
        elif not any(m["role"] == "tool" for m in body["messages"]):
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "maintenance_kpis", "arguments": json.dumps({"asset_id": "P-101"})},
                    }
                ],
            }
        else:
            tool_output = next(m["content"] for m in body["messages"] if m["role"] == "tool")
            mttr = json.loads(tool_output.split(">", 1)[1].rsplit("<", 1)[0])["data"]["mttr_hours"]
            message = {"role": "assistant", "content": f"P-101 MTTR is {mttr} hours this year."}
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            },
        )

    return httpx.MockTransport(handler)


def test_openai_compatible_model_drives_the_graph():
    from langchain_openai import ChatOpenAI

    requests: list[dict] = []
    llm = ChatOpenAI(
        model="test-model",
        api_key="sk-test",
        base_url="http://llm.test/v1",
        http_client=httpx.Client(transport=fake_openai(requests)),
        max_retries=0,
    )
    settings = Settings(_env_file=None, llm_provider="openai")  # type: ignore[call-arg]
    service = build_service(settings, llm)
    run = service.start(TECH, "How reliable was pump P-101 this year?")
    assert run.status == "completed"
    assert run.result["answer"] == "P-101 MTTR is 4.83 hours this year."
    assert run.result["agent"] == "reporting-agent"
    tool_schema_names = [t["function"]["name"] for t in requests[1]["tools"]]
    assert tool_schema_names == ["maintenance_kpis", "top_failure_modes", "list_open_work_orders"]
    agent_steps = [t for t in run.trace if t["node"] == "agent"]
    assert agent_steps[0]["tokens"] == {"input_tokens": 100, "output_tokens": 20}
