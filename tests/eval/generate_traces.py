from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from google.adk.runners import InMemoryRunner
from google.genai import types

from tax_concierge_agent.agent import app

DEFAULT_DATASET = Path("tests/eval/datasets/basic-dataset.json")
DEFAULT_OUTPUT = Path("artifacts/traces/generated_traces.json")
MAX_AUTO_FOLLOWUPS = 4


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    output_path = Path(args.output)
    dataset = json.loads(dataset_path.read_text())

    generated_cases = []
    for case in dataset["eval_cases"]:
        generated_cases.append(await run_case(case))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"eval_cases": generated_cases}, indent=2))
    print(f"Wrote {len(generated_cases)} traces to {output_path}")


async def run_case(case: dict[str, Any]) -> dict[str, Any]:
    runner = InMemoryRunner(app=app)
    user_id = f"eval_{case['eval_case_id']}"
    session = await runner.session_service.create_session(
        app_name=app.name,
        user_id=user_id,
        session_id=str(uuid4()),
        state={},
    )

    prompt = case["prompt"]
    message = _content_from_dict(prompt)
    all_events = []
    turns = [
        {
            "turn_index": 0,
            "events": [{"author": "user", "content": prompt}],
        }
    ]

    current_message = message
    invocation_id = None
    for _turn_index in range(MAX_AUTO_FOLLOWUPS + 1):
        emitted = [
            event
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session.id,
                new_message=current_message,
                invocation_id=invocation_id,
            )
        ]
        all_events.extend(emitted)
        turns.append(
            {
                "turn_index": len(turns),
                "events": [_event_to_agent_event(event) for event in emitted],
            }
        )

        request = _latest_request_input(emitted)
        if not request:
            break

        response_value = _answer_for_request(case, request)
        response_payload = {
            "field_id": request["field_id"] or "answers",
            "value": response_value,
        }
        current_message = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=request["interrupt_id"],
                        name="adk_request_input",
                        response=response_payload,
                    )
                )
            ],
        )
        invocation_id = request["invocation_id"]
        turns.append(
            {
                "turn_index": len(turns),
                "events": [
                    {
                        "author": "user",
                        "content": current_message.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        ),
                    }
                ],
            }
        )

    final_text = _final_response_text(all_events)
    generated = {
        "eval_case_id": case["eval_case_id"],
        "prompt": prompt,
        "responses": [
            {
                "response": {
                    "role": "model",
                    "parts": [{"text": final_text}],
                }
            }
        ],
        "expected_behavior": case.get("expected_behavior", {}),
        "agent_data": {
            "agents": {
                "tax_concierge_agent": {
                    "agent_id": "tax_concierge_agent",
                    "agent_type": "ADK Workflow",
                    "instruction": "Event-driven business tax intake workflow with security checkpoint and deterministic routing.",
                }
            },
            "turns": turns,
        },
        "trace_summary": _trace_summary(all_events),
    }
    return generated


def _content_from_dict(content: dict[str, Any]) -> types.Content:
    return types.Content.model_validate(content)


def _event_to_agent_event(event: Any) -> dict[str, Any]:
    content = event.content
    if content is None and event.output is not None:
        content = types.Content(
            role="model",
            parts=[
                types.Part.from_text(
                    text=json.dumps(
                        {
                            "node": event.node_info.path,
                            "route": event.actions.route,
                            "output": _jsonable_output(event.output),
                        },
                        default=str,
                    )
                )
            ],
        )
    elif content is None:
        content = types.Content(
            role="model",
            parts=[
                types.Part.from_text(
                    text=json.dumps(
                        {
                            "node": event.node_info.path,
                            "route": event.actions.route,
                        }
                    )
                )
            ],
        )
    return {
        "author": event.author or "tax_concierge_agent",
        "content": content.model_dump(mode="json", by_alias=True, exclude_none=True),
    }


def _jsonable_output(output: Any) -> Any:
    if hasattr(output, "model_dump"):
        return output.model_dump(mode="json")
    return output


def _latest_request_input(events: list[Any]) -> dict[str, Any] | None:
    for event in reversed(events):
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            function_call = part.function_call
            if not function_call or function_call.name != "adk_request_input":
                continue
            args = function_call.args or {}
            payload = args.get("payload") or {}
            components = payload.get("components") or []
            return {
                "interrupt_id": args.get("interruptId") or args.get("interrupt_id"),
                "invocation_id": event.invocation_id,
                "field_id": components[0].get("id") if components else None,
                "message": args.get("message"),
            }
    return None


def _answer_for_request(case: dict[str, Any], request: dict[str, Any]) -> Any:
    field_id = request.get("field_id") or "answers"
    followups = case.get("auto_followups") or {}
    if field_id in followups:
        return followups[field_id]
    if field_id == "owner_count":
        return "Two or more owners"
    if field_id == "entity_type_hint":
        return "LLC, no S-Corp or C-Corp election filed"
    if field_id == "user_story":
        return "I formed an LLC and need help understanding the likely business tax return."
    return "Not sure"


def _final_response_text(events: list[Any]) -> str:
    for event in reversed(events):
        if event.content and event.content.parts:
            texts = [part.text for part in event.content.parts if part.text]
            if texts:
                return "\n".join(texts)
    latest_output = None
    for event in reversed(events):
        if event.output is not None:
            latest_output = _jsonable_output(event.output)
            break
    return json.dumps(latest_output or {}, default=str)


def _trace_summary(events: list[Any]) -> list[dict[str, Any]]:
    summary = []
    for event in events:
        output = _jsonable_output(event.output) if event.output is not None else None
        summary.append(
            {
                "node": event.node_info.path,
                "route": event.actions.route,
                "has_output": event.output is not None,
                "candidate_entities": (output or {}).get("candidate_entities")
                if isinstance(output, dict)
                else None,
                "confidence": (output or {}).get("confidence")
                if isinstance(output, dict)
                else None,
                "missing_facts": (output or {}).get("missing_facts")
                if isinstance(output, dict)
                else None,
                "redacted_fields": (output or {}).get("redacted_fields")
                if isinstance(output, dict)
                else None,
                "security_flags": (output or {}).get("security_flags")
                if isinstance(output, dict)
                else None,
                "injection_detected": (output or {}).get("injection_detected")
                if isinstance(output, dict)
                else None,
            }
        )
    return summary


if __name__ == "__main__":
    asyncio.run(main())
