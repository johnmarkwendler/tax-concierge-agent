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
    TAX_CONCIERGE_CATALOG_ID,
    A2UIAction,
    A2UIBinding,
    A2UIComponent,
    A2UIMessage,
    FactExtraction,
    HumanInputResponse,
    TaxIntake,
    TaxWorkflowInput,
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


def generate_a2ui_surface(node_input: TaxIntake) -> Event:
    missing_facts = missing_facts_for_candidates(node_input)
    confidence = compute_confidence(node_input.model_copy(update={"missing_facts": missing_facts}))
    a2ui_messages = _build_a2ui_followup_messages(node_input, missing_facts, node_input.explanation)
    intake = node_input.model_copy(
        update={
            "missing_facts": missing_facts,
            "confidence": confidence,
            "a2ui_messages": a2ui_messages,
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
    intake = node_input.model_copy(
        update={
            "recommendation_summary": summary,
            "a2ui_messages": _build_recommendation_a2ui_messages(node_input, summary),
        }
    )
    yield Event(
        message=types.Content(role="model", parts=[types.Part.from_text(text=summary)]),
        state={"tax_intake": intake.model_dump()},
    )
    yield Event(output=intake, state={"tax_intake": intake.model_dump()})


def request_missing_fact(node_input: TaxIntake):
    messages = node_input.a2ui_messages or _build_a2ui_followup_messages(
        node_input, node_input.missing_facts, node_input.explanation
    )
    explanation = _surface_explanation(messages)
    yield RequestInput(
        interrupt_id="tax_intake_missing_fact",
        message=explanation,
        payload=_a2ui_payload(messages),
        response_schema=HumanInputResponse,
    )


def security_review(node_input: TaxIntake):
    explanation = (
        "The intake included instructions or document content that looked like an "
        "attempt to override the tax workflow. I quarantined that content and need "
        "a clean description that only includes business tax facts."
    )
    messages = _build_security_review_messages(explanation)
    secured = node_input.model_copy(
        update={
            "user_story": QUARANTINED_TOKEN,
            "a2ui_messages": messages,
            "security_flags": sorted({*node_input.security_flags, "security_event"}),
            "injection_detected": True,
        }
    )
    yield Event(output=secured, state={"tax_intake": secured.model_dump()})
    yield RequestInput(
        interrupt_id="security_clean_input",
        message=explanation,
        payload=_a2ui_payload(messages),
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
            "a2ui_messages": [],
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
            "a2ui_messages": [],
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
        (route_entities, generate_a2ui_surface),
        (generate_a2ui_surface, decide_next_step),
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


def _build_a2ui_followup_messages(
    node_input: TaxIntake, missing_facts: list[str], explanation: str | None
) -> list[A2UIMessage]:
    first_missing = missing_facts[0] if missing_facts else "entity_type_hint"
    component_by_fact = {
        "owner_count": _choice_question(
            field_id="owner_count",
            label="How many owners does the business have?",
            options=["One owner", "Two or more owners"],
            helper_text="Choose the closest answer. You can correct it later.",
            why=(
                "Owner count is the first branch in entity classification, especially "
                "for LLCs that may be treated differently depending on ownership."
            ),
        ),
        "has_llc": _choice_question(
            field_id="has_llc",
            label="Did you form an LLC for this business?",
            options=["Yes", "No", "Not sure"],
            helper_text="If you are not sure, choose Not sure and we will keep going.",
            why=(
                "LLC status changes which default tax classifications may apply."
            ),
        ),
        "entity_type_hint": _choice_question(
            field_id="entity_type_hint",
            label="Which business setup sounds closest?",
            options=[
                "Sole proprietor",
                "LLC",
                "Partnership",
                "S-Corp",
                "C-Corp",
                "Not sure",
            ],
            helper_text="Pick the plain-language option that sounds closest.",
            why=(
                "This gives the workflow a starting point when the story does not yet "
                "contain enough facts for a recommendation."
            ),
        ),
        "state": _choice_question(
            field_id="state",
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
            why=(
                "Some state and community-property rules can affect how spouse-owned "
                "businesses are analyzed."
            ),
        ),
        "married": _choice_question(
            field_id="married",
            label="Are the business owners married to each other?",
            options=["Yes", "No", "Not sure"],
            helper_text="Marital status can affect how a spouse-owned LLC is analyzed in some states.",
            why=(
                "Spouse-owned LLCs can be treated differently in some states, so this "
                "helps avoid a premature recommendation."
            ),
        ),
        "document_upload": _choice_question(
            field_id="document_upload",
            label="Do you have a formation document or tax notice to upload?",
            options=["Yes", "No", "Not sure"],
            helper_text="Documents are treated as observations and reviewed before facts are accepted.",
            why=(
                "A formation document, 1099, or election notice can provide clues, but "
                "low-confidence extracted fields still need review."
            ),
        ),
    }
    question_components = component_by_fact.get(
        first_missing, component_by_fact["entity_type_hint"]
    )
    return _surface_messages(
        surface_id="tax-intake",
        root=first_missing,
        data={
            "taxIntake": {
                "knownFacts": node_input.known_facts,
                "missingFacts": missing_facts,
                "candidateEntities": node_input.candidate_entities,
                "readinessState": "Needs clarification",
                "explanation": explanation
                or "I need one more fact before recommending the most likely tax path.",
                "answers": {},
            }
        },
        components=question_components,
    )


def _choice_question(
    field_id: str,
    label: str,
    options: list[str],
    helper_text: str,
    why: str,
) -> list[A2UIComponent]:
    return [
        A2UIComponent(
            id=field_id,
            component="SegmentedChoiceCards",
            props={
                "label": label,
                "options": [{"label": option, "value": option} for option in options],
                "helperText": helper_text,
                "whyWeAreAsking": why,
                "readinessState": "Needs clarification",
                "submitOnSelect": False,
            },
            binding=A2UIBinding(path=f"/taxIntake/answers/{field_id}"),
            action=A2UIAction(event="select_answer", payload={"fieldId": field_id}),
        ),
    ]


def _build_security_review_messages(explanation: str) -> list[A2UIMessage]:
    return _surface_messages(
        surface_id="security-review",
        root="security_card",
        data={
            "taxIntake": {
                "readinessState": "Security review required",
                "explanation": explanation,
                "answers": {},
                "securityFlags": ["security_event"],
            }
        },
        components=[
            A2UIComponent(
                id="security_card",
                component="SecurityReviewCard",
                props={
                    "title": "Security review required",
                    "message": explanation,
                    "label": "Describe the business tax situation without instructions to the assistant.",
                    "multiline": True,
                    "helperText": "Do not include account numbers, SSNs, addresses, or instructions such as ignore rules.",
                    "whyWeAreAsking": (
                        "I need a clean version of the business facts so the workflow can "
                        "continue without unsafe instructions or sensitive identifiers."
                    ),
                },
                binding=A2UIBinding(path="/taxIntake/answers/user_story"),
                action=A2UIAction(event="submit_security_review", payload={"fieldId": "user_story"}),
            ),
        ],
    )


def _build_recommendation_a2ui_messages(
    intake: TaxIntake, summary: str
) -> list[A2UIMessage]:
    return _surface_messages(
        surface_id="tax-intake",
        root="recommendation_workbench",
        data={
            "taxIntake": {
                "knownFacts": intake.known_facts,
                "missingFacts": intake.missing_facts,
                "candidateEntities": intake.candidate_entities,
                "readinessState": "Ready for recommendation",
                "recommendation": summary,
            }
        },
        components=[
            A2UIComponent(
                id="recommendation_workbench",
                component="RecommendationWorkbench",
                props={
                    "headline": "We have a recommendation.",
                    "recommendation": summary,
                    "body": summary,
                    "insights": _recommendation_insights(intake),
                    "assumptions": _recommendation_assumptions(intake),
                    "nextSteps": _recommendation_next_steps(summary),
                    "profile": _advisor_profile(),
                },
                action=A2UIAction(event="continue_recommendation", payload={}),
            ),
        ],
    )


def _advisor_profile() -> dict[str, Any]:
    return {
        "avatar": "/images/john-mark-wendler.jpg",
        "name": "John Mark Wendler",
        "credential": "Certified Public Accountant",
        "bio": "17 years of tax experience helping business owners make careful filing decisions.",
        "years": 17,
        "website": "https://www.johnmarkwendler.com",
        "linkedin": "https://linkedin.com/in/johnmarkwendler",
    }


def _recommendation_insights(intake: TaxIntake) -> list[str]:
    insights: list[str] = []
    structure = str(intake.known_facts.get("business_structure") or "")
    owners = str(intake.known_facts.get("owner_count") or "")
    election = str(intake.known_facts.get("s_corp_election_status") or "")
    if structure:
        insights.append(f"Your business setup is marked as {structure}.")
    if owners:
        insights.append(f"Ownership is marked as {owners.lower()}.")
    if election:
        insights.append(f"S-Corp election status is {election.lower()}.")
    if intake.candidate_entities:
        insights.append(f"The matching path is {' or '.join(intake.candidate_entities)}.")
    return insights or ["The facts you confirmed are enough to prepare a likely tax path."]


def _recommendation_assumptions(intake: TaxIntake) -> list[str]:
    assumptions = [
        "This is based only on the facts provided in this session.",
        "State filing rules and late elections may change the next action.",
    ]
    election = str(intake.known_facts.get("s_corp_election_status") or "").lower()
    if election == "not sure":
        assumptions.insert(0, "The S-Corp election should be confirmed before relying on this path.")
    return assumptions


def _recommendation_next_steps(summary: str) -> list[str]:
    return [
        f"Review the facts behind {summary}.",
        "Save any formation records, election letters, and income forms that support this setup.",
        "Get tax advice from John Mark Wendler before filing or making an election.",
    ]


def _surface_messages(
    surface_id: str,
    root: str,
    data: dict[str, Any],
    components: list[A2UIComponent],
) -> list[A2UIMessage]:
    return [
        A2UIMessage(
            message="createSurface",
            surfaceId=surface_id,
            catalogId=TAX_CONCIERGE_CATALOG_ID,
            root=root,
        ),
        A2UIMessage(
            message="updateDataModel",
            surfaceId=surface_id,
            catalogId=TAX_CONCIERGE_CATALOG_ID,
            data=data,
        ),
        A2UIMessage(
            message="updateComponents",
            surfaceId=surface_id,
            catalogId=TAX_CONCIERGE_CATALOG_ID,
            components=components,
        ),
    ]


def _a2ui_payload(messages: list[A2UIMessage]) -> dict[str, Any]:
    return {
        "messages": [
            message.model_dump(mode="json", by_alias=True, exclude_none=True)
            for message in messages
        ]
    }


def _surface_explanation(messages: list[A2UIMessage]) -> str:
    for message in messages:
        if message.data:
            explanation = message.data.get("taxIntake", {}).get("explanation")
            if explanation:
                return str(explanation)
    return "I need one more detail before recommending the next step."


def _coerce_human_response(node_input: Any) -> HumanInputResponse:
    if isinstance(node_input, HumanInputResponse):
        return node_input
    if isinstance(node_input, dict):
        answers = node_input.get("answers")
        if isinstance(answers, dict):
            if len(answers) == 1:
                field_id, value = next(iter(answers.items()))
                return HumanInputResponse(field_id=field_id, value=value)
            return HumanInputResponse(field_id="answers", value=answers)
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
