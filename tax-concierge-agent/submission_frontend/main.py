from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

DEFAULT_USER_ID = "default-user"
READINESS_STILL_LEARNING = "Still learning"
READINESS_NEEDS_CLARIFICATION = "Needs clarification"
READINESS_READY = "Ready for recommendation"
READINESS_SECURITY = "Security review required"


class IntakeRequest(BaseModel):
    session_id: str | None = None
    user_story: str
    known_facts: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)


class DocumentReviewItem(BaseModel):
    field_label: str
    extracted_value: str
    confidence_state: str
    needs_review: bool = False
    explanation: str


class SessionState(BaseModel):
    session_id: str
    current_stage: str = "intake"
    sanitized_user_story: str = ""
    redacted_categories: list[str] = Field(default_factory=list)
    known_facts: dict[str, Any] = Field(default_factory=dict)
    missing_facts: list[str] = Field(default_factory=list)
    candidate_entities: list[str] = Field(default_factory=lambda: ["Cannot Determine Yet"])
    readiness_state: str = READINESS_STILL_LEARNING
    next_ui: dict[str, Any] | None = None
    recommendation: str | None = None
    explanation: str | None = None
    security_flags: list[str] = Field(default_factory=list)
    document_review_items: list[DocumentReviewItem] = Field(default_factory=list)
    interrupt_id: str | None = None
    requested_input_payload: dict[str, Any] | None = None
    runtime_available: bool = False
    raw_session_state: dict[str, Any] = Field(default_factory=dict)


@dataclass
class RuntimeResult:
    events: list[Any] = field(default_factory=list)
    runtime_available: bool = False
    error: str | None = None


class AgentRuntimeClient:
    """Best-effort adapter for deployed Agent Runtime with a safe local fallback."""

    def __init__(self) -> None:
        self.project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
        self.location = os.getenv("GCP_LOCATION")
        self.runtime_id = os.getenv("AGENT_RUNTIME_ID")
        self._agent: Any | None = None
        self._init_error: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.project and self.location and self.runtime_id)

    def _load_agent(self) -> Any | None:
        if self._agent is not None:
            return self._agent
        if not self.configured:
            self._init_error = "GCP_PROJECT, GCP_LOCATION, or AGENT_RUNTIME_ID is missing."
            return None
        try:
            import vertexai
            from vertexai import agent_engines

            vertexai.init(project=self.project, location=self.location)
            resource_name = self.runtime_id
            if not resource_name.startswith("projects/"):
                resource_name = (
                    f"projects/{self.project}/locations/{self.location}/"
                    f"reasoningEngines/{self.runtime_id}"
                )
            self._agent = agent_engines.get(resource_name)
            return self._agent
        except Exception as exc:  # pragma: no cover - depends on deployed runtime
            self._init_error = str(exc)
            return None

    async def send_message(
        self,
        message: dict[str, Any],
        session_id: str | None,
    ) -> RuntimeResult:
        agent = self._load_agent()
        if agent is None:
            return RuntimeResult(error=self._init_error)

        call_shapes = [
            {
                "message": message,
                "user_id": DEFAULT_USER_ID,
                "session_id": session_id,
            },
            {
                "input": message,
                "user_id": DEFAULT_USER_ID,
                "session_id": session_id,
            },
            {
                "message": message,
                "user_id": DEFAULT_USER_ID,
            },
        ]
        for kwargs in call_shapes:
            try:
                events = await self._collect_agent_events(agent, kwargs)
                if session_id and _events_contain_session_not_found(events):
                    retry_kwargs = dict(kwargs)
                    retry_kwargs.pop("session_id", None)
                    events = await self._collect_agent_events(agent, retry_kwargs)
                return RuntimeResult(events=events, runtime_available=True)
            except TypeError:
                continue
            except Exception as exc:  # pragma: no cover - depends on deployed runtime
                return RuntimeResult(runtime_available=True, error=str(exc))
        return RuntimeResult(
            runtime_available=True,
            error="No compatible Agent Runtime query method accepted the request.",
        )

    async def resume(
        self,
        session_id: str,
        interrupt_id: str,
        answers: dict[str, Any],
    ) -> RuntimeResult:
        resume_message = {
            "role": "user",
            "parts": [
                {
                    "function_response": {
                        "id": interrupt_id,
                        "name": "adk_request_input",
                        "response": {"answers": answers},
                    }
                }
            ],
        }
        return await self.send_message(resume_message, session_id=session_id)

    async def list_sessions(self) -> list[dict[str, Any]]:
        agent = self._load_agent()
        if agent is None:
            return []
        for method_name in ("list_sessions", "get_sessions"):
            method = getattr(agent, method_name, None)
            if not method:
                continue
            try:
                result = method(user_id=DEFAULT_USER_ID)
                if hasattr(result, "__aiter__"):
                    return [self._jsonable(item) async for item in result]
                if asyncio.iscoroutine(result):
                    result = await result
                return [self._jsonable(item) for item in result]
            except Exception:
                continue
        return []

    async def fetch_history(self, session_id: str) -> list[Any]:
        agent = self._load_agent()
        if agent is None:
            return []
        shapes = [
            ("get_session", {"user_id": DEFAULT_USER_ID, "session_id": session_id}),
            ("session", {"user_id": DEFAULT_USER_ID, "session_id": session_id}),
        ]
        for method_name, kwargs in shapes:
            method = getattr(agent, method_name, None)
            if not method:
                continue
            try:
                result = method(**kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                jsonable = self._jsonable(result)
                if isinstance(jsonable, dict):
                    return jsonable.get("events") or jsonable.get("history") or []
                if isinstance(jsonable, list):
                    return jsonable
            except Exception:
                continue
        return []

    async def _collect_agent_events(self, agent: Any, kwargs: dict[str, Any]) -> list[Any]:
        stream = getattr(agent, "async_stream_query", None)
        if stream:
            result = stream(**{k: v for k, v in kwargs.items() if v is not None})
            if hasattr(result, "__aiter__"):
                return [event async for event in result]
            if asyncio.iscoroutine(result):
                result = await result
                if hasattr(result, "__aiter__"):
                    return [event async for event in result]
                return result if isinstance(result, list) else [result]

        for method_name in ("stream_query", "query"):
            method = getattr(agent, method_name, None)
            if not method:
                continue
            result = method(**{k: v for k, v in kwargs.items() if v is not None})
            if asyncio.iscoroutine(result):
                result = await result
            if hasattr(result, "__iter__") and not isinstance(result, (dict, str, bytes)):
                return list(result)
            return [result]
        raise TypeError("Agent object has no supported query method.")

    def _jsonable(self, value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json", by_alias=True, exclude_none=True)
        if hasattr(value, "to_dict"):
            return value.to_dict()
        return value


runtime_client = AgentRuntimeClient()
SESSIONS: dict[str, SessionState] = {}

app = FastAPI(title="Tax Concierge Submission Frontend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return INDEX_HTML


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/session/{session_id}", response_model=SessionState)
async def get_session(session_id: str) -> SessionState:
    state = SESSIONS.get(session_id)
    history = await runtime_client.fetch_history(session_id)
    if history:
        state = _state_from_events(session_id, history, state)
        state.runtime_available = True
        SESSIONS[session_id] = state
    if state is None:
        state = _new_session_state(session_id)
        SESSIONS[session_id] = state
    return state


@app.post("/api/intake", response_model=SessionState)
async def intake(request: IntakeRequest) -> SessionState:
    session_id = request.session_id or str(uuid4())
    sanitized, redacted_categories = _sanitize_story(request.user_story)
    prior = SESSIONS.get(session_id) or _new_session_state(session_id)
    prior.sanitized_user_story = sanitized
    prior.redacted_categories = sorted(set([*prior.redacted_categories, *redacted_categories]))
    prior.known_facts = {**prior.known_facts, **request.known_facts, **_extract_demo_facts(sanitized)}
    prior.missing_facts = _missing_facts(prior.known_facts)
    prior.candidate_entities = _candidate_entities(prior.known_facts)
    prior.security_flags = _security_flags(request.user_story)
    prior.readiness_state = _readiness_state(prior)
    prior.current_stage = (
        "security_review"
        if prior.security_flags
        else "recommendation"
        if prior.readiness_state == READINESS_READY
        else "understanding"
    )
    prior.next_ui = _build_next_ui(prior)
    prior.recommendation = _recommendation_for(prior)
    prior.explanation = _explanation_for(prior)

    message = {
        "role": "user",
        "parts": [
            {
                "text": json.dumps(
                    {
                        "data": {
                            "user_story": sanitized,
                            "known_facts": prior.known_facts,
                        }
                    }
                )
            }
        ],
    }
    result = await runtime_client.send_message(message, session_id=session_id)
    if result.events:
        prior = _state_from_events(session_id, result.events, prior)
    prior.runtime_available = result.runtime_available and not result.error
    if result.error:
        prior.raw_session_state["runtime_warning"] = result.error
    SESSIONS[session_id] = prior
    return prior


@app.post("/api/upload", response_model=SessionState)
async def upload(
    session_id: str | None = None,
    file: UploadFile = File(...),
) -> SessionState:
    session_id = session_id or str(uuid4())
    state = SESSIONS.get(session_id) or _new_session_state(session_id)
    content = await file.read()
    extracted = _mock_document_extraction(file.filename or "uploaded document", content)
    existing = [item.model_dump() for item in state.document_review_items]
    state.document_review_items = [
        DocumentReviewItem.model_validate(item) for item in [*existing, *extracted]
    ]
    for item in state.document_review_items:
        if not item.needs_review:
            key = _slug(item.field_label)
            state.known_facts.setdefault(key, item.extracted_value)
            if key == "possible_business_structure":
                state.known_facts.setdefault("business_structure", item.extracted_value)
    state.missing_facts = _missing_facts(state.known_facts)
    state.candidate_entities = _candidate_entities(state.known_facts)
    state.readiness_state = _readiness_state(state)
    state.current_stage = "document_review"
    state.next_ui = _build_next_ui(state)
    state.explanation = "I pulled out the clearest details and marked anything uncertain for review."
    SESSIONS[session_id] = state
    return state


@app.post("/api/action/{session_id}", response_model=SessionState)
async def action(session_id: str, request: ActionRequest) -> SessionState:
    state = SESSIONS.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Session not found")
    interrupt_id = state.interrupt_id or "tax_intake_missing_fact"
    result = await runtime_client.resume(
        session_id=session_id,
        interrupt_id=interrupt_id,
        answers=request.answers,
    )
    state.known_facts = {**state.known_facts, **request.answers}
    state.missing_facts = _missing_facts(state.known_facts)
    state.candidate_entities = _candidate_entities(state.known_facts)
    state.readiness_state = _readiness_state(state)
    state.current_stage = "recommendation" if state.readiness_state == READINESS_READY else "follow_up"
    state.next_ui = None if state.readiness_state == READINESS_READY else _build_next_ui(state)
    state.recommendation = _recommendation_for(state)
    state.explanation = _explanation_for(state)
    if result.events:
        state = _state_from_events(session_id, result.events, state)
    state.runtime_available = result.runtime_available and not result.error
    if result.error:
        state.raw_session_state["runtime_warning"] = result.error
    SESSIONS[session_id] = state
    return state


def _new_session_state(session_id: str) -> SessionState:
    return SessionState(
        session_id=session_id,
        current_stage="intake",
        next_ui=_build_next_ui_stub(),
        explanation="Tell us what you know, in your own words.",
    )


def _sanitize_story(story: str) -> tuple[str, list[str]]:
    categories: list[str] = []
    patterns = {
        "SSN": r"\b\d{3}-\d{2}-\d{4}\b",
        "EIN": r"\b\d{2}-\d{7}\b",
        "credit card": r"\b(?:\d[ -]*?){13,16}\b",
        "email": r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    }
    sanitized = story
    for label, pattern in patterns.items():
        sanitized, count = re.subn(pattern, f"[REDACTED {label}]", sanitized, flags=re.I)
        if count:
            categories.append(label)
    return sanitized.strip(), categories


def _security_flags(story: str) -> list[str]:
    lowered = story.lower()
    suspicious = [
        "ignore previous",
        "ignore all",
        "system prompt",
        "developer message",
        "reveal",
        "jailbreak",
    ]
    return ["security_event"] if any(token in lowered for token in suspicious) else []


def _extract_demo_facts(story: str) -> dict[str, Any]:
    lowered = story.lower()
    facts: dict[str, Any] = {}
    if "llc" in lowered:
        facts["business_structure"] = "LLC"
    if "sole" in lowered or "just me" in lowered or "only owner" in lowered:
        facts["owner_count"] = "One owner"
    if "two owner" in lowered or "partner" in lowered or "spouse" in lowered:
        facts["owner_count"] = "Two or more owners"
    if "s-corp" in lowered or "s corp" in lowered or "2553" in lowered:
        facts["s_corp_election_status"] = "S-Corp election mentioned"
    if "1099" in lowered:
        facts["income_document"] = "1099 mentioned"
    return facts


def _missing_facts(known_facts: dict[str, Any]) -> list[str]:
    required = ["business_structure", "owner_count", "s_corp_election_status"]
    return [field for field in required if not known_facts.get(field)]


def _candidate_entities(known_facts: dict[str, Any]) -> list[str]:
    structure = str(known_facts.get("business_structure", "")).lower()
    owners = str(known_facts.get("owner_count", "")).lower()
    election = str(known_facts.get("s_corp_election_status", "")).lower()
    if "s-corp" in election or "s corp" in election:
        return ["S-Corp"]
    if "llc" in structure and "two" in owners:
        return ["Partnership", "S-Corp"]
    if "llc" in structure and ("one" in owners or "sole" in owners):
        return ["Single-Member LLC", "S-Corp"]
    if "sole" in structure:
        return ["Sole Proprietor"]
    return ["Cannot Determine Yet"]


def _readiness_state(state: SessionState) -> str:
    if state.security_flags:
        return READINESS_SECURITY
    if not state.sanitized_user_story and not state.known_facts:
        return READINESS_STILL_LEARNING
    if state.missing_facts:
        return READINESS_NEEDS_CLARIFICATION
    return READINESS_READY


def _build_next_ui(state: SessionState) -> dict[str, Any] | None:
    if state.security_flags:
        return {
            "schema_version": "a2ui-lite/v1",
            "title": "Security review required",
            "explanation": "I redacted sensitive or unsafe content. Please continue with only business tax facts.",
            "components": [
                {
                    "id": "user_story",
                    "type": "text_input",
                    "label": "Describe the business situation without sensitive taxpayer identifiers.",
                    "required": True,
                    "helper_text": "Leave out SSNs, EINs, account numbers, and instructions to the assistant.",
                }
            ],
            "submit_label": "Continue",
        }
    if not state.missing_facts:
        return None
    labels = {
        "business_structure": (
            "How is your business set up?",
            ["LLC", "Sole proprietor", "Corporation", "Not sure"],
            "This helps narrow which tax paths may apply before we ask anything more specific.",
        ),
        "owner_count": (
            "How many owners does the business have?",
            ["One owner", "Two or more owners", "Not sure"],
            "Owner count can change how an LLC is usually treated for federal tax filing.",
        ),
        "s_corp_election_status": (
            "Has the business filed an S-Corp election?",
            ["Yes", "No", "Not sure"],
            "Some LLCs choose S-Corp tax treatment. If you are not sure, that is okay.",
        ),
    }
    field_id = state.missing_facts[0]
    label, options, why = labels[field_id]
    return {
        "schema_version": "a2ui-lite/v1",
        "title": label,
        "explanation": why,
        "components": [
            {
                "id": field_id,
                "type": "radio",
                "label": label,
                "options": options,
                "required": True,
                "helper_text": "Choose the closest answer. You can correct it later.",
            }
        ],
        "submit_label": "Continue",
    }


def _build_next_ui_stub() -> dict[str, Any]:
    return {
        "schema_version": "a2ui-lite/v1",
        "title": "Tell us about your business",
        "explanation": "Start with what you know. We will ask for the missing pieces one at a time.",
        "components": [],
        "submit_label": "Continue",
    }


def _recommendation_for(state: SessionState) -> str | None:
    if state.readiness_state != READINESS_READY:
        return None
    candidates = [item for item in state.candidate_entities if item != "Cannot Determine Yet"]
    return candidates[0] if candidates else None


def _explanation_for(state: SessionState) -> str:
    if state.readiness_state == READINESS_SECURITY:
        return "Sensitive taxpayer information is redacted before model reasoning, and unsafe instructions are held for review."
    if state.readiness_state == READINESS_READY:
        return "The key setup details are clear enough to prepare a recommendation with assumptions."
    if state.missing_facts:
        return "A few details are still missing, so I will ask one question at a time."
    return "Tell us what you know and upload anything useful. We will organize it from there."


def _mock_document_extraction(filename: str, content: bytes) -> list[dict[str, Any]]:
    text_hint = content[:2000].decode("utf-8", errors="ignore").lower()
    items = [
        {
            "field_label": "Document name",
            "extracted_value": filename,
            "confidence_state": "Confident",
            "needs_review": False,
            "explanation": "This came from the uploaded file name.",
        }
    ]
    if "llc" in text_hint or filename.lower().endswith((".pdf", ".txt", ".docx")):
        items.append(
            {
                "field_label": "Possible business structure",
                "extracted_value": "LLC",
                "confidence_state": "Looks reliable",
                "needs_review": False,
                "explanation": "The document appears to reference an LLC or formation record.",
            }
        )
    items.append(
        {
            "field_label": "S-Corp election status",
            "extracted_value": "Not found",
            "confidence_state": "Needs your review",
            "needs_review": True,
            "explanation": "The mock extractor did not find a clear Form 2553 or S-Corp election reference.",
        }
    )
    return items


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _state_from_events(
    session_id: str,
    events: list[Any],
    prior: SessionState | None,
) -> SessionState:
    state = prior or _new_session_state(session_id)
    json_events = [_jsonable_event(event) for event in events]
    unresolved = _find_unresolved_request_input(json_events)
    intake = _latest_tax_intake(json_events)
    if intake:
        state.sanitized_user_story = intake.get("user_story") or state.sanitized_user_story
        state.known_facts = intake.get("known_facts") or state.known_facts
        state.missing_facts = intake.get("missing_facts") or state.missing_facts
        state.candidate_entities = intake.get("candidate_entities") or state.candidate_entities
        state.next_ui = intake.get("next_ui") or state.next_ui
        recommendation_summary = intake.get("recommendation_summary")
        if recommendation_summary:
            state.recommendation = _recommendation_label_from_intake(intake, state)
            state.explanation = _sanitize_recommendation_summary(recommendation_summary)
        else:
            state.recommendation = state.recommendation or _recommendation_label_from_intake(
                intake, state
            )
            state.explanation = intake.get("explanation") or state.explanation
        state.security_flags = intake.get("security_flags") or state.security_flags
        state.redacted_categories = intake.get("redacted_fields") or state.redacted_categories
    if unresolved:
        state.interrupt_id = unresolved.get("interrupt_id")
        state.requested_input_payload = unresolved.get("requested_input_payload")
        state.next_ui = unresolved.get("next_ui") or state.next_ui
        state.current_stage = "follow_up"
    state.readiness_state = _readiness_state(state)
    if state.readiness_state == READINESS_READY:
        state.current_stage = "recommendation"
        state.recommendation = state.recommendation or _recommendation_for(state)
    state.raw_session_state = {"events": json_events[-12:], "unresolved_request_input": unresolved}
    return state


def _events_contain_session_not_found(events: list[Any]) -> bool:
    for event in events:
        event_data = _jsonable_event(event)
        message = str(event_data.get("message", "")).lower()
        if event_data.get("code") == 498 and "session not found" in message:
            return True
    return False


def _recommendation_label_from_intake(
    intake: dict[str, Any],
    state: SessionState,
) -> str | None:
    candidates = intake.get("candidate_entities") or state.candidate_entities
    for candidate in candidates:
        if candidate and candidate != "Cannot Determine Yet":
            return str(candidate)
    return state.recommendation


def _sanitize_recommendation_summary(summary: str) -> str:
    summary = re.sub(r"\s*Confidence:\s*\d+(?:\.\d+)?%\.\s*", " ", summary)
    summary = re.sub(r"\s+", " ", summary).strip()
    return summary


def _jsonable_event(event: Any) -> dict[str, Any]:
    if hasattr(event, "model_dump"):
        return event.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(event, dict):
        return event
    if hasattr(event, "to_dict"):
        return event.to_dict()
    return json.loads(json.dumps(event, default=str))


def _latest_tax_intake(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        for candidate in (
            event.get("output"),
            event.get("state", {}).get("tax_intake") if isinstance(event.get("state"), dict) else None,
        ):
            if isinstance(candidate, dict) and (
                "user_story" in candidate or "known_facts" in candidate
            ):
                return candidate
        content = event.get("content") or {}
        for part in content.get("parts") or []:
            text = part.get("text")
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            output = parsed.get("output") if isinstance(parsed, dict) else None
            if isinstance(output, dict) and ("user_story" in output or "known_facts" in output):
                return output
    return None


def _find_unresolved_request_input(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    calls: dict[str, dict[str, Any]] = {}
    responses: set[str] = set()
    for event in events:
        content = event.get("content") or {}
        for part in content.get("parts") or []:
            function_call = part.get("function_call") or part.get("functionCall")
            if function_call and function_call.get("name") == "adk_request_input":
                args = function_call.get("args") or {}
                interrupt_id = args.get("interruptId") or args.get("interrupt_id") or function_call.get("id")
                if interrupt_id:
                    payload = args.get("payload") or {}
                    calls[interrupt_id] = {
                        "interrupt_id": interrupt_id,
                        "requested_input_payload": payload,
                        "next_ui": payload,
                    }
            function_response = part.get("function_response") or part.get("functionResponse")
            if function_response and function_response.get("name") == "adk_request_input":
                response_id = function_response.get("id")
                if response_id:
                    responses.add(response_id)
    for interrupt_id, call in reversed(list(calls.items())):
        if interrupt_id not in responses:
            return call
    return None


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Tax Concierge</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;560;620;650;700&family=Outfit:wght@500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      color-scheme: light;
      --bg: oklch(0.985 0.006 353);
      --bg-strong: oklch(1 0 0);
      --surface: oklch(1 0 0 / 0.74);
      --surface-solid: oklch(0.990 0.004 353);
      --surface-raised: oklch(0.968 0.010 353 / 0.78);
      --ink: oklch(0.205 0.028 260);
      --muted: oklch(0.455 0.030 260);
      --border: oklch(0.895 0.014 353 / 0.72);
      --primary: oklch(0.540 0.135 353);
      --primary-strong: oklch(0.450 0.140 353);
      --primary-soft: oklch(0.940 0.035 353);
      --accent: oklch(0.470 0.095 195);
      --accent-soft: oklch(0.930 0.035 195);
      --warning: oklch(0.620 0.105 78);
      --danger: oklch(0.520 0.135 25);
      --shadow: 0 18px 44px oklch(0.205 0.028 260 / 0.10);
      --focus: 0 0 0 3px oklch(0.940 0.035 353), 0 0 0 5px oklch(0.540 0.135 353 / 0.22);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      background:
        radial-gradient(circle at 16% 10%, oklch(0.900 0.070 353 / 0.34), transparent 34rem),
        radial-gradient(circle at 86% 16%, oklch(0.880 0.075 195 / 0.28), transparent 30rem),
        linear-gradient(135deg, var(--bg), oklch(1 0 0));
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background: radial-gradient(circle at 50% 100%, oklch(0.970 0.018 78 / 0.28), transparent 36rem);
    }
    button, textarea, input, select { font: inherit; }
    button, .upload-label {
      border: 0;
      border-radius: 10px;
      cursor: pointer;
      transition: transform 180ms cubic-bezier(.16,1,.3,1), background 180ms cubic-bezier(.16,1,.3,1), border-color 180ms cubic-bezier(.16,1,.3,1);
    }
    button:focus-visible, textarea:focus-visible, input:focus-visible, select:focus-visible, .upload-label:focus-within {
      outline: none;
      box-shadow: var(--focus);
    }
    button:hover, .upload-label:hover { transform: translateY(-1px); }
    button:disabled { cursor: not-allowed; opacity: .62; transform: none; }
    .shell { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 34px 0 56px; position: relative; }
    .topbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 40px; }
    .brand { display: flex; align-items: center; gap: 12px; font-weight: 700; }
    .mark { width: 36px; height: 36px; border-radius: 12px; background: linear-gradient(135deg, var(--primary), var(--accent)); box-shadow: 0 10px 18px oklch(0.540 0.135 353 / .22); }
    .session-pill { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 999px; background: oklch(1 0 0 / .55); color: var(--muted); font-size: 14px; backdrop-filter: blur(18px); }
    .hero { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 28px; align-items: start; }
    .hero-copy { padding: 16px 0 0; }
    h1 { font-family: Outfit, Inter, sans-serif; margin: 0 0 14px; font-size: clamp(3rem, 8vw, 5.75rem); line-height: .94; letter-spacing: -0.035em; text-wrap: balance; }
    .subhead { max-width: 720px; color: var(--muted); font-size: 20px; line-height: 1.55; margin: 0; text-wrap: pretty; }
    .grid { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 28px; margin-top: 28px; align-items: start; }
    .stack { display: grid; gap: 18px; }
    .card {
      border: 1px solid var(--border);
      border-radius: 16px;
      background: var(--surface);
      backdrop-filter: blur(22px) saturate(130%);
      box-shadow: var(--shadow);
      padding: 24px;
    }
    .card.soft { background: oklch(1 0 0 / .50); box-shadow: none; }
    .card h2, .card h3 { margin: 0; letter-spacing: -0.01em; }
    .card h2 { font-size: 24px; line-height: 1.2; }
    .card h3 { font-size: 17px; line-height: 1.35; }
    .helper { color: var(--muted); line-height: 1.55; margin: 8px 0 0; }
    .story-input {
      width: 100%;
      min-height: 190px;
      resize: vertical;
      margin-top: 18px;
      padding: 18px;
      color: var(--ink);
      background: oklch(1 0 0 / .78);
      border: 1px solid var(--border);
      border-radius: 14px;
      line-height: 1.55;
    }
    .story-input::placeholder { color: oklch(0.405 0.030 260); }
    .actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 16px; align-items: center; }
    .primary { background: var(--primary); color: white; padding: 12px 18px; font-weight: 650; }
    .primary:hover { background: var(--primary-strong); }
    .secondary, .upload-label { background: oklch(1 0 0 / .62); color: var(--ink); border: 1px solid var(--border); padding: 11px 16px; font-weight: 620; }
    .upload-label input { position: absolute; inline-size: 1px; block-size: 1px; opacity: 0; pointer-events: none; }
    .status { display: inline-flex; align-items: center; gap: 7px; padding: 6px 10px; border-radius: 999px; font-size: 13px; font-weight: 650; background: var(--surface-raised); color: var(--muted); }
    .status.ready, .status.confident { background: var(--accent-soft); color: var(--accent); }
    .status.needs { background: var(--primary-soft); color: var(--primary); }
    .status.security { background: oklch(0.940 0.045 25); color: var(--danger); }
    .fact-list, .review-list { display: grid; gap: 10px; margin-top: 16px; }
    .fact-row, .review-row, .missing-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 12px;
      background: var(--surface-raised);
      border: 1px solid oklch(0.895 0.014 353 / .55);
      border-radius: 12px;
    }
    .empty { color: var(--muted); background: var(--surface-raised); border-radius: 12px; padding: 14px; line-height: 1.5; margin-top: 14px; }
    .journey { display: grid; gap: 12px; margin-top: 16px; }
    .step { display: grid; grid-template-columns: 24px 1fr; gap: 10px; align-items: center; color: var(--muted); }
    .dot { width: 12px; height: 12px; border-radius: 50%; background: oklch(0.820 0.018 260); margin-left: 6px; }
    .step.active { color: var(--ink); font-weight: 650; }
    .step.active .dot { background: var(--primary); box-shadow: 0 0 0 5px oklch(0.940 0.035 353); }
    .step.done .dot { background: var(--accent); }
    .a2ui { display: grid; gap: 14px; margin-top: 16px; }
    .choice-group { display: grid; gap: 10px; }
    .choice { display: flex; gap: 10px; align-items: center; padding: 12px; border: 1px solid var(--border); border-radius: 12px; background: oklch(1 0 0 / .56); cursor: pointer; }
    .choice:has(input:checked) { border-color: oklch(0.540 0.135 353 / .55); background: var(--primary-soft); }
    .why-button { margin-top: 2px; color: var(--primary); background: transparent; padding: 0; font-weight: 650; }
    .privacy { display: flex; gap: 12px; align-items: flex-start; }
    .privacy-icon { width: 34px; height: 34px; flex: 0 0 auto; display: grid; place-items: center; border-radius: 11px; background: var(--accent-soft); color: var(--accent); font-weight: 800; }
    .debug { margin-top: 16px; }
    pre { overflow: auto; max-height: 420px; padding: 16px; border-radius: 12px; background: oklch(0.205 0.028 260); color: oklch(0.985 0.006 353); font-size: 12px; line-height: 1.5; }
    .toast-wrap { position: fixed; right: 20px; bottom: 20px; display: grid; gap: 10px; z-index: 30; }
    .toast { padding: 12px 14px; border-radius: 12px; color: var(--ink); background: oklch(1 0 0 / .86); border: 1px solid var(--border); box-shadow: var(--shadow); backdrop-filter: blur(18px); }
    .drawer-backdrop { position: fixed; inset: 0; background: oklch(0.205 0.028 260 / .28); display: none; z-index: 20; }
    .drawer-backdrop.open { display: block; }
    .drawer { position: fixed; inset: 0 0 0 auto; width: min(440px, 100%); padding: 28px; background: oklch(1 0 0 / .90); backdrop-filter: blur(24px); transform: translateX(100%); visibility: hidden; transition: transform 220ms cubic-bezier(.16,1,.3,1), visibility 0ms linear 220ms; z-index: 21; border-left: 1px solid var(--border); }
    .drawer.open { transform: translateX(0); visibility: visible; transition: transform 220ms cubic-bezier(.16,1,.3,1); }
    .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); border: 0; }
    .spinner { width: 16px; height: 16px; border-radius: 50%; border: 2px solid oklch(1 0 0 / .5); border-top-color: white; display: inline-block; animation: spin 800ms linear infinite; vertical-align: -3px; margin-right: 8px; }
    @keyframes spin { to { transform: rotate(360deg); } }
    @media (max-width: 900px) {
      .hero, .grid { grid-template-columns: 1fr; }
      .hero-copy { padding-top: 0; }
      h1 { font-size: clamp(3rem, 15vw, 4.25rem); }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; scroll-behavior: auto !important; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="brand"><div class="mark" aria-hidden="true"></div><span>Tax Concierge</span></div>
      <div class="session-pill"><span id="runtimeDot">●</span><span id="sessionLabel">No session yet</span></div>
    </header>

    <main>
      <section class="hero">
        <div class="hero-copy">
          <h1>Come as you are.</h1>
          <p class="subhead">Tell us about your business or upload what you have. We’ll figure out the next step together.</p>
        </div>
        <aside class="card privacy">
          <div class="privacy-icon" aria-hidden="true">✓</div>
          <div>
            <h3>Private by design</h3>
            <p class="helper">Sensitive taxpayer information is redacted before model reasoning. We show security events calmly and ask for a cleaner version when needed.</p>
          </div>
        </aside>
      </section>

      <section class="grid" aria-label="Tax Concierge intake">
        <div class="stack">
          <section class="card">
            <h2>Tell us about your business.</h2>
            <p class="helper">Use your own words. You do not need to know the right tax terms.</p>
            <label class="sr-only" for="story">Business story</label>
            <textarea id="story" class="story-input" placeholder="Example: I started an LLC last year, I’m the only owner, and I received 1099 income. I’m not sure what return I need."></textarea>
            <div class="actions">
              <button id="submitStory" class="primary">Continue</button>
              <label class="upload-label">Upload a document<input id="fileInput" type="file" /></label>
            </div>
          </section>

          <section class="card" id="understandingCard">
            <h2>We think we understand the following</h2>
            <p class="helper">You can correct anything. We will only move forward when the important pieces are clear.</p>
            <div id="facts" class="fact-list"></div>
          </section>

          <section class="card" id="followupCard">
            <h2>Next question</h2>
            <div id="a2ui" class="a2ui"></div>
          </section>

          <section class="card" id="recommendationCard">
            <h2>We have a recommendation.</h2>
            <div id="recommendationBody" class="empty">When the key facts are clear, the recommendation will appear here with the assumptions that shaped it.</div>
          </section>
        </div>

        <aside class="stack">
          <section class="card soft">
            <h3>Progress</h3>
            <div id="readiness" style="margin-top:12px"></div>
            <div class="journey" id="journey"></div>
          </section>

          <section class="card soft">
            <h3>Missing facts</h3>
            <div id="missingFacts"></div>
          </section>

          <section class="card soft">
            <h3>Document review</h3>
            <div id="docReview" class="review-list"></div>
          </section>

          <section class="card soft">
            <h3>Security events</h3>
            <div id="securityEvents"></div>
          </section>

          <section class="card soft debug">
            <label class="choice"><input type="checkbox" id="debugToggle" /> Show developer state</label>
            <pre id="debugState" hidden></pre>
          </section>
        </aside>
      </section>
    </main>
  </div>

  <div id="drawerBackdrop" class="drawer-backdrop"></div>
  <aside id="drawer" class="drawer" aria-label="Why we are asking this" aria-hidden="true">
    <button class="secondary" id="closeDrawer">Close</button>
    <h2 style="margin-top:24px">Why we’re asking</h2>
    <p id="drawerText" class="helper"></p>
  </aside>
  <div id="toasts" class="toast-wrap" aria-live="polite"></div>

  <script>
    const state = { session: null, busy: false };
    const $ = (id) => document.getElementById(id);

    function toast(message) {
      const el = document.createElement("div");
      el.className = "toast";
      el.textContent = message;
      $("toasts").appendChild(el);
      setTimeout(() => el.remove(), 3600);
    }

    async function api(path, options = {}) {
      const res = await fetch(path, options);
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    }

    function setBusy(button, busy, label) {
      button.disabled = busy;
      button.innerHTML = busy ? `<span class="spinner"></span>${label}` : label;
    }

    $("submitStory").addEventListener("click", async () => {
      const story = $("story").value.trim();
      if (!story) return toast("Tell us a little about the business first.");
      setBusy($("submitStory"), true, "Working");
      try {
        state.session = await api("/api/intake", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: state.session?.session_id, user_story: story })
        });
        render();
        toast("I organized what we know so far.");
      } catch (err) {
        toast("Could not reach the workflow. The local fallback is still available.");
      } finally {
        setBusy($("submitStory"), false, "Continue");
      }
    });

    $("fileInput").addEventListener("change", async (event) => {
      const file = event.target.files[0];
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      const suffix = state.session?.session_id ? `?session_id=${encodeURIComponent(state.session.session_id)}` : "";
      try {
        state.session = await api(`/api/upload${suffix}`, { method: "POST", body: form });
        render();
        toast("Document details are ready for review.");
      } catch (err) {
        toast("Upload failed. Try a smaller or simpler file.");
      } finally {
        event.target.value = "";
      }
    });

    $("debugToggle").addEventListener("change", render);
    $("closeDrawer").addEventListener("click", closeDrawer);
    $("drawerBackdrop").addEventListener("click", closeDrawer);

    function openDrawer(text) {
      $("drawerText").textContent = text || "This answer helps narrow the next step.";
      $("drawer").classList.add("open");
      $("drawer").setAttribute("aria-hidden", "false");
      $("drawerBackdrop").classList.add("open");
    }
    function closeDrawer() {
      $("drawer").classList.remove("open");
      $("drawer").setAttribute("aria-hidden", "true");
      $("drawerBackdrop").classList.remove("open");
    }

    async function submitA2UI(nextUi) {
      const answers = {};
      for (const component of nextUi.components || []) {
        const selected = document.querySelector(`[name="${component.id}"]:checked`);
        const input = document.querySelector(`[name="${component.id}"]`);
        answers[component.id] = selected ? selected.value : input?.value;
      }
      if (!Object.values(answers).some(Boolean)) return toast("Choose an answer before continuing.");
      try {
        state.session = await api(`/api/action/${state.session.session_id}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ answers })
        });
        render();
        toast("Answer saved.");
      } catch (err) {
        toast("Could not resume the paused workflow.");
      }
    }

    function render() {
      const s = state.session || {};
      $("sessionLabel").textContent = s.session_id ? `Session ${s.session_id.slice(0, 8)}` : "No session yet";
      $("runtimeDot").style.color = s.runtime_available ? "var(--accent)" : "var(--warning)";
      $("readiness").innerHTML = `<span class="status ${statusClass(s.readiness_state)}">${s.readiness_state || "Still learning"}</span>`;
      renderJourney(s.readiness_state);
      renderFacts(s);
      renderMissing(s);
      renderA2UI(s.next_ui);
      renderDocs(s.document_review_items || []);
      renderSecurity(s.security_flags || [], s.redacted_categories || []);
      renderRecommendation(s);
      $("debugState").hidden = !$("debugToggle").checked;
      $("debugState").textContent = JSON.stringify(s, null, 2);
    }

    function statusClass(label = "") {
      if (label.includes("Ready") || label.includes("Confident")) return "ready";
      if (label.includes("Security")) return "security";
      if (label.includes("Needs")) return "needs";
      return "";
    }

    function renderJourney(readiness = "Still learning") {
      const steps = ["Still learning", "Needs clarification", "Ready for recommendation"];
      const index = readiness.includes("Ready") ? 2 : readiness.includes("Needs") ? 1 : 0;
      $("journey").innerHTML = steps.map((step, i) => `<div class="step ${i < index ? "done" : i === index ? "active" : ""}"><span class="dot"></span><span>${step}</span></div>`).join("");
    }

    function renderFacts(s) {
      const entries = Object.entries(s.known_facts || {});
      $("facts").innerHTML = entries.length ? entries.map(([key, value]) => `<div class="fact-row"><div><strong>${pretty(key)}</strong><div class="helper">${value}</div></div><span class="status confident">Confident</span></div>`).join("") : `<div class="empty">Tell us your story or upload a document, and we’ll reflect back what we understand.</div>`;
    }

    function renderMissing(s) {
      const missing = s.missing_facts || [];
      $("missingFacts").innerHTML = missing.length ? `<div class="fact-list">${missing.map(item => `<div class="missing-row"><span>${pretty(item)}</span><span class="status needs">Needs clarification</span></div>`).join("")}</div>` : `<div class="empty">No missing facts are blocking the next step.</div>`;
    }

    function renderA2UI(nextUi) {
      if (!nextUi || !(nextUi.components || []).length) {
        $("a2ui").innerHTML = `<div class="empty">Follow-up questions will appear here one at a time.</div>`;
        return;
      }
      const body = [`<h3>${nextUi.title}</h3><p class="helper">${nextUi.explanation || ""}</p>`];
      for (const component of nextUi.components || []) {
        if (component.type === "radio") {
          body.push(`<div class="choice-group" role="radiogroup" aria-label="${component.label}">${(component.options || []).map(option => `<label class="choice"><input type="radio" name="${component.id}" value="${option}" /> ${option}</label>`).join("")}</div>`);
        } else {
          body.push(`<label class="sr-only" for="${component.id}">${component.label}</label><input id="${component.id}" name="${component.id}" class="story-input" style="min-height:auto" placeholder="${component.helper_text || component.label}" />`);
        }
      }
      body.push(`<button class="why-button" type="button" id="whyBtn">Why we’re asking</button>`);
      body.push(`<div class="actions"><button class="primary" id="answerBtn">${nextUi.submit_label || "Continue"}</button></div>`);
      $("a2ui").innerHTML = body.join("");
      $("whyBtn").addEventListener("click", () => openDrawer(nextUi.explanation));
      $("answerBtn").addEventListener("click", () => submitA2UI(nextUi));
    }

    function renderDocs(items) {
      if (!items.length) {
        $("docReview").innerHTML = `<div class="empty">Uploaded document details will appear as editable review cards. We never show raw OCR by default.</div>`;
        return;
      }
      const reliable = items.filter(item => !item.needs_review);
      const review = items.filter(item => item.needs_review);
      $("docReview").innerHTML = `${section("Looks reliable", reliable)}${section("Needs your review", review)}`;
    }

    function section(title, rows) {
      if (!rows.length) return "";
      return `<h3 style="margin-top:14px">${title}</h3>${rows.map(item => `<div class="review-row"><div><strong>${item.field_label}</strong><div class="helper">${item.extracted_value}</div><small>${item.explanation}</small></div><span class="status ${item.needs_review ? "needs" : "confident"}">${item.confidence_state}</span></div>`).join("")}`;
    }

    function renderSecurity(flags, redacted) {
      if (!flags.length && !redacted.length) {
        $("securityEvents").innerHTML = `<div class="empty">No security events. Sensitive identifiers are still checked before model reasoning.</div>`;
        return;
      }
      $("securityEvents").innerHTML = `<div class="fact-list">${[...flags, ...redacted.map(x => `Redacted ${x}`)].map(item => `<div class="missing-row"><span>${item}</span><span class="status security">Review</span></div>`).join("")}</div>`;
    }

    function renderRecommendation(s) {
      if (!s.recommendation) {
        $("recommendationBody").className = "empty";
        $("recommendationBody").innerHTML = "When the key facts are clear, the recommendation will appear here with the assumptions that shaped it.";
        return;
      }
      $("recommendationBody").className = "fact-list";
      $("recommendationBody").innerHTML = `<div class="fact-row"><div><strong>Business type</strong><div class="helper">${s.recommendation}</div></div><span class="status ready">Ready</span></div><div class="fact-row"><div><strong>Why</strong><div class="helper">${s.explanation || "The answer is based on the facts you confirmed."}</div></div></div><div class="fact-row"><div><strong>Assumptions</strong><div class="helper">${Object.entries(s.known_facts || {}).map(([k,v]) => `${pretty(k)}: ${v}`).join("; ")}</div></div></div><div class="actions"><button class="primary">Continue</button></div>`;
    }

    function pretty(key) {
      return key.replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase());
    }

    render();
  </script>
</body>
</html>
"""
