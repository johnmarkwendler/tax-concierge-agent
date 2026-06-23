from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

DEFAULT_USER_ID = "default-user"
READINESS_STILL_LEARNING = "Still learning"
READINESS_NEEDS_CLARIFICATION = "Needs clarification"
READINESS_READY = "Ready for recommendation"
READINESS_SECURITY = "Security review required"
A2UI_VERSION = "0.9.1"
TAX_CONCIERGE_CATALOG_ID = "https://tax-concierge.local/catalogs/v1/tax-concierge.json"
FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"


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
    a2ui_messages: list[dict[str, Any]] = Field(default_factory=list)
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
if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")
if (FRONTEND_DIST / "images").exists():
    app.mount("/images", StaticFiles(directory=FRONTEND_DIST / "images"), name="images")


@app.get("/", response_class=HTMLResponse)
async def index() -> Response:
    index_file = FRONTEND_DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse(_fallback_index_html())


@app.get("/catalogs/v1/tax-concierge.json")
async def tax_concierge_catalog() -> dict[str, Any]:
    return {
        "id": TAX_CONCIERGE_CATALOG_ID,
        "version": "v1",
        "a2uiVersion": A2UI_VERSION,
        "components": [
            "StoryInputCard",
            "SegmentedChoiceCards",
            "ConfirmedFactsRail",
            "ReadinessStatus",
            "WhyAskingDrawer",
            "DocumentUploadCard",
            "DocumentFieldReviewCard",
            "SecurityReviewCard",
            "RecommendationCard",
            "RecommendationWorkbench",
        ],
        "events": [
            "submit_story",
            "select_answer",
            "toggle_chip",
            "upload_document",
            "confirm_document_field",
            "open_why_asking",
            "continue_intake",
            "submit_security_review",
            "continue_recommendation",
        ],
    }


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
    prior.redacted_categories = sorted({*prior.redacted_categories, *redacted_categories})
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
    prior.recommendation = _recommendation_for(prior)
    prior.explanation = _explanation_for(prior)
    prior.a2ui_messages = _build_a2ui_messages(prior)

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
    file: Annotated[UploadFile, File(...)],
    session_id: str | None = None,
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
    state.recommendation = _recommendation_for(state)
    state.explanation = "I pulled out the clearest details and marked anything uncertain for review."
    state.a2ui_messages = _build_a2ui_messages(state)
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
    state.recommendation = _recommendation_for(state)
    state.explanation = _explanation_for(state)
    state.a2ui_messages = _build_a2ui_messages(state)
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
        a2ui_messages=_build_a2ui_stub_messages(),
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


def _build_a2ui_messages(state: SessionState) -> list[dict[str, Any]]:
    if state.security_flags:
        explanation = "I redacted sensitive or unsafe content. Please continue with only business tax facts."
        return _surface_messages(
            "security-review",
            "security_card",
            {
                "taxIntake": {
                    "readinessState": READINESS_SECURITY,
                    "explanation": explanation,
                    "answers": {},
                    "securityFlags": state.security_flags,
                }
            },
            [
                {
                    "id": "security_card",
                    "component": "SecurityReviewCard",
                    "props": {
                        "title": "Security review",
                        "message": "Sensitive taxpayer information is redacted before model reasoning.",
                        "label": "Describe the business situation without sensitive taxpayer identifiers.",
                        "multiline": True,
                        "helperText": "Leave out SSNs, EINs, account numbers, and instructions to the assistant.",
                        "whyWeAreAsking": "I need a clean version of the business facts so the workflow can continue safely.",
                    },
                    "binding": {"path": "/taxIntake/answers/user_story"},
                    "action": {"event": "submit_security_review", "payload": {"fieldId": "user_story"}},
                },
            ],
        )
    if not state.missing_facts:
        if state.readiness_state == READINESS_READY and state.recommendation:
            return _surface_messages(
                "tax-intake",
                "recommendation_workbench",
                {
                    "taxIntake": {
                        "knownFacts": state.known_facts,
                        "missingFacts": state.missing_facts,
                        "candidateEntities": state.candidate_entities,
                        "readinessState": READINESS_READY,
                        "recommendation": state.recommendation,
                    }
                },
                [
                    {
                        "id": "recommendation_workbench",
                        "component": "RecommendationWorkbench",
                        "props": {
                            "headline": "We have a recommendation.",
                            "recommendation": state.recommendation,
                            "body": state.explanation or "The key setup details are clear enough to continue.",
                            "insights": _recommendation_insights(state),
                            "assumptions": _recommendation_assumptions(state),
                            "nextSteps": _recommendation_next_steps(state),
                            "profile": _advisor_profile(),
                        },
                        "action": {"event": "continue_recommendation", "payload": {}},
                    },
                ],
            )
        return []
    labels: dict[str, dict[str, Any]] = {
        "business_structure": {
            "label": "How is your business set up?",
            "options": ["LLC", "Sole proprietor", "Corporation", "Not sure"],
            "why": "This helps narrow which tax paths may apply before we ask anything more specific.",
            "group": "Business setup",
        },
        "owner_count": {
            "label": "How many owners does the business have?",
            "options": ["One owner", "Two or more owners", "Not sure"],
            "why": "Owner count can change how an LLC is usually treated for federal tax filing.",
            "group": "Ownership",
        },
        "s_corp_election_status": {
            "label": "Has the business filed an S-Corp election?",
            "options": ["Yes", "No", "Not sure"],
            "why": "Some LLCs choose S-Corp tax treatment. If you are not sure, that is okay.",
            "group": "Tax election",
        },
    }
    field_id = state.missing_facts[0]
    component = labels[field_id]
    return _surface_messages(
        "tax-intake",
        field_id,
        {
            "taxIntake": {
                "knownFacts": state.known_facts,
                "missingFacts": state.missing_facts,
                "candidateEntities": state.candidate_entities,
                "readinessState": READINESS_NEEDS_CLARIFICATION,
                "explanation": component["why"],
                "answers": {},
            }
        },
        [
            {
                "id": field_id,
                "component": "SegmentedChoiceCards",
                "props": {
                    "label": component["label"],
                    "options": [{"label": option, "value": option} for option in component["options"]],
                    "helperText": "Choose the closest answer. You can correct it later.",
                    "whyWeAreAsking": component["why"],
                    "readinessState": READINESS_NEEDS_CLARIFICATION,
                    "displayGroup": component["group"],
                    "submitOnSelect": False,
                },
                "binding": {"path": f"/taxIntake/answers/{field_id}"},
                "action": {"event": "select_answer", "payload": {"fieldId": field_id}},
            },
        ],
    )


def _build_a2ui_stub_messages() -> list[dict[str, Any]]:
    return _surface_messages(
        "tax-intake",
        "intake_empty",
        {
            "taxIntake": {
                "readinessState": READINESS_STILL_LEARNING,
                "explanation": "Start with what you know. We will ask for the missing pieces one at a time.",
                "answers": {},
            }
        },
        [],
    )


def _surface_messages(
    surface_id: str,
    root: str,
    data: dict[str, Any],
    components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "version": A2UI_VERSION,
            "message": "createSurface",
            "surfaceId": surface_id,
            "catalogId": TAX_CONCIERGE_CATALOG_ID,
            "root": root,
        },
        {
            "version": A2UI_VERSION,
            "message": "updateDataModel",
            "surfaceId": surface_id,
            "catalogId": TAX_CONCIERGE_CATALOG_ID,
            "data": data,
        },
        {
            "version": A2UI_VERSION,
            "message": "updateComponents",
            "surfaceId": surface_id,
            "catalogId": TAX_CONCIERGE_CATALOG_ID,
            "components": components,
        },
    ]


def _recommendation_for(state: SessionState) -> str | None:
    if state.readiness_state != READINESS_READY:
        return None
    candidates = [item for item in state.candidate_entities if item != "Cannot Determine Yet"]
    return candidates[0] if candidates else None


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


def _recommendation_insights(state: SessionState) -> list[str]:
    insights: list[str] = []
    structure = str(state.known_facts.get("business_structure") or "")
    owners = str(state.known_facts.get("owner_count") or "")
    election = str(state.known_facts.get("s_corp_election_status") or "")
    if structure:
        insights.append(f"Your business setup is marked as {structure}.")
    if owners:
        insights.append(f"Ownership is marked as {owners.lower()}.")
    if election:
        insights.append(f"S-Corp election status is {election.lower()}.")
    if state.candidate_entities:
        insights.append(f"The matching path is {' or '.join(state.candidate_entities)}.")
    return insights or ["The facts you confirmed are enough to prepare a likely tax path."]


def _recommendation_assumptions(state: SessionState) -> list[str]:
    assumptions = [
        "This is based only on the facts provided in this session.",
        "State filing rules and late elections may change the next action.",
    ]
    election = str(state.known_facts.get("s_corp_election_status") or "").lower()
    if election == "not sure":
        assumptions.insert(0, "The S-Corp election should be confirmed before relying on this path.")
    return assumptions


def _recommendation_next_steps(state: SessionState) -> list[str]:
    recommendation = state.recommendation or "the likely tax path"
    return [
        f"Review the facts behind {recommendation}.",
        "Save any formation records, election letters, and income forms that support this setup.",
        "Get tax advice from John Mark Wendler before filing or making an election.",
    ]


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
        state.a2ui_messages = intake.get("a2ui_messages") or state.a2ui_messages
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
        state.a2ui_messages = unresolved.get("a2ui_messages") or state.a2ui_messages
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
                        "a2ui_messages": payload.get("messages") or [],
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


def _fallback_index_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Tax Concierge</title>
  <style>
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: oklch(1 0 0);
      color: oklch(0.205 0.028 260);
    }
    main {
      width: min(720px, calc(100% - 32px));
      margin: 12vh auto;
      border: 1px solid oklch(0.895 0.014 353);
      border-radius: 14px;
      background: oklch(0.985 0.006 353);
      padding: 24px;
    }
    h1 { margin: 0 0 8px; font-size: 1.5rem; }
    p { margin: 0; color: oklch(0.455 0.030 260); line-height: 1.55; }
  </style>
</head>
<body>
  <main>
    <h1>Tax Concierge frontend build missing</h1>
    <p>Run the React frontend build in <code>submission_frontend/frontend</code>, then restart this service.</p>
  </main>
</body>
</html>"""
