from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.adk.agents.context import Context
from google.adk.apps import App, ResumabilityConfig
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.workflow import Workflow
from google.genai import types

from .config import CONFIG
from .models import (
    FactExtraction,
    HumanInputResponse,
    NextUI,
    TaxIntake,
    TaxWorkflowInput,
    UIComponent,
)
from .routing import (
    compute_confidence,
    deterministic_entity_candidates,
    missing_facts_for_candidates,
)
from .security import (
    QUARANTINED_TOKEN,
    enforce_security_controls,
    prepare_intake_for_security_checkpoint,
)

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def normalize_input(node_input: Any) -> Event:
    """Accept plain text, ADK Content, or {"data": ...} local test payloads."""
    payload = _coerce_start_payload(node_input)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    story = (
        data.get("user_story")
        or data.get("story")
        or payload.get("story")
        or payload.get("text")
        or ""
    )

    intake = TaxIntake(
        user_story=str(story).strip(),
        uploaded_documents=data.get("uploaded_documents") or [],
        known_facts=data.get("known_facts") or {},
        missing_facts=data.get("missing_facts") or [],
    )
    sanitized = prepare_intake_for_security_checkpoint(intake)
    return Event(output=sanitized)


def security_checkpoint(node_input: TaxIntake) -> Event:
    secured = enforce_security_controls(node_input)
    route = "security_review" if secured.injection_detected else "clean"
    return Event(
        output=secured,
        route=route,
        state={
            "tax_intake": secured.model_dump(),
            "redacted_fields": secured.redacted_fields,
            "security_flags": secured.security_flags,
            "injection_detected": secured.injection_detected,
            "quarantined_content": secured.quarantined_content,
        },
    )


async def extract_fact_summary(node_input: TaxIntake) -> Event:
    extraction = await _extract_facts_with_llm(node_input)
    known_facts = {**node_input.known_facts, **extraction.known_facts}
    missing_facts = list(dict.fromkeys([*node_input.missing_facts, *extraction.missing_facts]))

    intake = node_input.model_copy(
        update={
            "known_facts": known_facts,
            "missing_facts": missing_facts,
            "explanation": extraction.next_question_explanation,
            "risk_flags": extraction.risk_flags,
        }
    )
    return Event(output=intake, state={"tax_intake": intake.model_dump()})


def route_entities(node_input: TaxIntake) -> Event:
    intake = node_input.model_copy(
        update={"candidate_entities": deterministic_entity_candidates(node_input)}
    )
    return Event(output=intake, state={"tax_intake": intake.model_dump()})


def generate_next_ui(node_input: TaxIntake) -> Event:
    missing_facts = missing_facts_for_candidates(node_input)
    confidence = compute_confidence(node_input.model_copy(update={"missing_facts": missing_facts}))
    next_ui = _build_next_ui(missing_facts, node_input.explanation)
    intake = node_input.model_copy(
        update={
            "missing_facts": missing_facts,
            "confidence": confidence,
            "next_ui": next_ui,
        }
    )
    return Event(output=intake, state={"tax_intake": intake.model_dump()})


def decide_next_step(node_input: TaxIntake) -> Event:
    route = (
        "recommend"
        if node_input.confidence >= CONFIG.confidence_threshold
        and node_input.candidate_entities != ["Cannot Determine Yet"]
        else "needs_input"
    )
    return Event(output=node_input, route=route, state={"tax_intake": node_input.model_dump()})


def final_recommendation(node_input: TaxIntake):
    summary = _recommendation_summary(node_input)
    intake = node_input.model_copy(update={"recommendation_summary": summary, "next_ui": None})
    yield Event(
        message=types.Content(role="model", parts=[types.Part.from_text(text=summary)]),
        state={"tax_intake": intake.model_dump()},
    )
    yield Event(output=intake, state={"tax_intake": intake.model_dump()})


def request_missing_fact(node_input: TaxIntake):
    next_ui = node_input.next_ui or _build_next_ui(node_input.missing_facts, node_input.explanation)
    yield RequestInput(
        interrupt_id="tax_intake_missing_fact",
        message=next_ui.explanation,
        payload=next_ui.model_dump(),
        response_schema=HumanInputResponse,
    )


def security_review(node_input: TaxIntake):
    next_ui = NextUI(
        title="Security review required",
        explanation=(
            "The intake included instructions or document content that looked like an "
            "attempt to override the tax workflow. I quarantined that content and need "
            "a clean description that only includes business tax facts."
        ),
        components=[
            UIComponent(
                id="user_story",
                type="text_input",
                label="Describe the business tax situation without instructions to the assistant.",
                helper_text="Do not include account numbers, SSNs, addresses, or instructions such as ignore rules.",
            )
        ],
    )
    secured = node_input.model_copy(
        update={
            "user_story": QUARANTINED_TOKEN,
            "next_ui": next_ui,
            "security_flags": sorted({*node_input.security_flags, "security_event"}),
            "injection_detected": True,
        }
    )
    yield Event(output=secured, state={"tax_intake": secured.model_dump()})
    yield RequestInput(
        interrupt_id="security_clean_input",
        message=next_ui.explanation,
        payload=next_ui.model_dump(),
        response_schema=HumanInputResponse,
    )


def apply_security_review_input(ctx: Context, node_input: Any) -> Event:
    stored = ctx.state.get("tax_intake") or {}
    intake = TaxIntake(**stored)
    response = _coerce_human_response(node_input)
    cleaned = intake.model_copy(
        update={
            "user_story": str(response.value),
            "known_facts": {},
            "missing_facts": [],
            "candidate_entities": ["Cannot Determine Yet"],
            "confidence": 0.0,
            "next_ui": None,
            "injection_detected": False,
        }
    )
    return Event(output=cleaned, route="continue")


def apply_human_input(ctx: Context, node_input: Any) -> Event:
    stored = ctx.state.get("tax_intake") or {}
    intake = TaxIntake(**stored)
    response = _coerce_human_response(node_input)
    known_facts = dict(intake.known_facts)
    known_facts[response.field_id] = response.value
    missing_facts = [fact for fact in intake.missing_facts if fact != response.field_id]
    updated = intake.model_copy(
        update={
            "known_facts": known_facts,
            "missing_facts": missing_facts,
            "next_ui": None,
        }
    )
    return Event(output=updated, route="continue")


root_agent = Workflow(
    name="tax_concierge_agent",
    description="Business tax intake workflow with deterministic entity routing.",
    edges=[
        ("START", normalize_input),
        (normalize_input, security_checkpoint),
        (
            security_checkpoint,
            {
                "clean": extract_fact_summary,
                "security_review": security_review,
            },
        ),
        (security_review, apply_security_review_input),
        (apply_security_review_input, {"continue": security_checkpoint}),
        (extract_fact_summary, route_entities),
        (route_entities, generate_next_ui),
        (generate_next_ui, decide_next_step),
        (
            decide_next_step,
            {
                "recommend": final_recommendation,
                "needs_input": request_missing_fact,
            },
        ),
        (request_missing_fact, apply_human_input),
        (apply_human_input, {"continue": security_checkpoint}),
    ],
)

app = App(
    name="tax_concierge_agent",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)


async def _extract_facts_with_llm(intake: TaxIntake) -> FactExtraction:
    prompt = f"""
You summarize business tax intake stories. Do not make final tax-law decisions.
Return JSON matching this schema:
{json.dumps(FactExtraction.model_json_schema(), indent=2)}

User story:
{intake.user_story}

Existing known facts:
{json.dumps(intake.known_facts, indent=2)}

Extract concise structured facts, identify ambiguity, risk flags, or missing information,
and explain in plain English why the next question is needed.
"""
    client = genai.Client()
    response = await client.aio.models.generate_content(
        model=CONFIG.model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )
    if getattr(response, "parsed", None):
        return response.parsed
    return FactExtraction.model_validate_json(response.text or "{}")


def _coerce_start_payload(node_input: Any) -> dict[str, Any]:
    if isinstance(node_input, TaxWorkflowInput):
        return node_input.model_dump(exclude_none=True)
    if isinstance(node_input, dict):
        return node_input
    if isinstance(node_input, types.Content):
        text = " ".join(part.text or "" for part in node_input.parts or []).strip()
        return _parse_json_or_story(text)
    if isinstance(node_input, str):
        return _parse_json_or_story(node_input)
    return {"story": str(node_input)}


def _parse_json_or_story(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"story": text}
    if isinstance(parsed, dict):
        return parsed
    return {"story": text}


def _build_next_ui(missing_facts: list[str], explanation: str | None) -> NextUI:
    first_missing = missing_facts[0] if missing_facts else "entity_type_hint"
    component_by_fact = {
        "owner_count": UIComponent(
            id="owner_count",
            type="radio",
            label="How many owners does the business have?",
            options=["One owner", "Two or more owners"],
            helper_text="Ownership count is the first branch in entity classification.",
        ),
        "has_llc": UIComponent(
            id="has_llc",
            type="radio",
            label="Did you form an LLC for this business?",
            options=["Yes", "No", "Not sure"],
        ),
        "entity_type_hint": UIComponent(
            id="entity_type_hint",
            type="select",
            label="Which business setup sounds closest?",
            options=[
                "Sole proprietor",
                "LLC",
                "Partnership",
                "S-Corp",
                "C-Corp",
                "Not sure",
            ],
        ),
        "state": UIComponent(
            id="state",
            type="select",
            label="Which state is connected to the business?",
            options=[
                "California",
                "Texas",
                "Florida",
                "New York",
                "Washington",
                "Other",
                "Not sure",
            ],
            helper_text="State can affect whether additional state-level or community-property rules apply.",
        ),
        "married": UIComponent(
            id="married",
            type="radio",
            label="Are the business owners married to each other?",
            options=["Yes", "No", "Not sure"],
            helper_text="Marital status can affect how a spouse-owned LLC is analyzed in some states.",
        ),
        "document_upload": UIComponent(
            id="document_upload",
            type="document_upload",
            label="Upload the relevant tax document.",
            helper_text="Documents are treated as observations and reviewed before facts are accepted.",
        ),
    }
    return NextUI(
        title="One more business detail",
        explanation=explanation
        or "I need one more fact before recommending the most likely tax path.",
        components=[component_by_fact.get(first_missing, component_by_fact["entity_type_hint"])],
    )


def _coerce_human_response(node_input: Any) -> HumanInputResponse:
    if isinstance(node_input, HumanInputResponse):
        return node_input
    if isinstance(node_input, dict):
        return HumanInputResponse.model_validate(node_input)
    if isinstance(node_input, str):
        return HumanInputResponse(field_id="entity_type_hint", value=node_input)
    return HumanInputResponse(field_id="entity_type_hint", value=str(node_input))


def _recommendation_summary(intake: TaxIntake) -> str:
    candidates = ", ".join(intake.candidate_entities)
    return (
        f"Based on the facts provided, the likely business tax path is: {candidates}. "
        f"Confidence: {intake.confidence:.0%}. "
        "This is an intake recommendation, not a final tax-law determination."
    )
