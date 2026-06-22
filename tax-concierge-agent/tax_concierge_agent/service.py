from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, Field

from .agent import app as workflow_app
from .events import TaxEvent, TaxEventType, normalize_event_to_intake
from .models import TaxIntake

logger = logging.getLogger("tax_concierge_agent.ambient")
logging.basicConfig(level=logging.INFO)

APP_NAME = workflow_app.name
DEFAULT_USER_ID = "ambient_user"


class AmbientSessionRecord(BaseModel):
    session_id: str
    user_id: str = DEFAULT_USER_ID
    adk_session_id: str
    tax_intake: TaxIntake | None = None
    pending_interrupt_id: str | None = None
    pending_field_id: str | None = None
    pending_invocation_id: str | None = None
    event_count: int = 0
    last_event_type: str | None = None


class AmbientEventResponse(BaseModel):
    session_id: str
    event_type: str
    route: str | None = None
    pending_input: dict[str, Any] | None = None
    tax_intake: TaxIntake | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)


class AmbientRuntime:
    def __init__(self) -> None:
        self.session_service = InMemorySessionService()
        self.runner = Runner(
            app=workflow_app,
            session_service=self.session_service,
            app_name=APP_NAME,
        )
        self.sessions: dict[str, AmbientSessionRecord] = {}

    async def ensure_session(
        self,
        session_id: str | None,
        user_id: str,
    ) -> AmbientSessionRecord:
        external_session_id = session_id or str(uuid4())
        existing = self.sessions.get(external_session_id)
        if existing:
            return existing

        await self.session_service.create_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=external_session_id,
            state={},
        )
        record = AmbientSessionRecord(
            session_id=external_session_id,
            user_id=user_id,
            adk_session_id=external_session_id,
        )
        self.sessions[external_session_id] = record
        logger.info("Created ambient session %s", external_session_id)
        return record

    async def process_event(self, event: TaxEvent) -> AmbientEventResponse:
        record = await self.ensure_session(event.session_id, event.user_id)
        record.last_event_type = event.event_type.value
        record.event_count += 1

        if event.event_type == TaxEventType.FOLLOWUP_RESPONSE:
            if not record.pending_interrupt_id:
                logger.info("FOLLOWUP_RESPONSE without pending interrupt; running as state update")
                intake = normalize_event_to_intake(event, record.tax_intake)
                message = _content_from_intake(intake)
                invocation_id = None
            else:
                message = _content_from_followup(record, event.answers)
                invocation_id = record.pending_invocation_id
        else:
            intake = normalize_event_to_intake(event, record.tax_intake)
            message = _content_from_intake(intake)
            invocation_id = None

        events = [
            emitted
            async for emitted in self.runner.run_async(
                user_id=record.user_id,
                session_id=record.adk_session_id,
                new_message=message,
                invocation_id=invocation_id,
            )
        ]

        summary = _summarize_events(events)
        latest_intake = _latest_intake(events)
        if latest_intake:
            record.tax_intake = latest_intake

        pending = _latest_request_input(events)
        if pending:
            record.pending_interrupt_id = pending["interrupt_id"]
            record.pending_field_id = pending.get("field_id")
            record.pending_invocation_id = pending.get("invocation_id")
        else:
            record.pending_interrupt_id = None
            record.pending_field_id = None
            record.pending_invocation_id = None

        route = next(
            (
                item["route"]
                for item in reversed(summary)
                if item.get("route") is not None
            ),
            None,
        )
        logger.info(
            "Processed %s for session %s route=%s pending=%s",
            event.event_type.value,
            record.session_id,
            route,
            record.pending_interrupt_id,
        )
        return AmbientEventResponse(
            session_id=record.session_id,
            event_type=event.event_type.value,
            route=route,
            pending_input=pending,
            tax_intake=record.tax_intake,
            events=summary,
        )

    async def get_session(self, session_id: str) -> AmbientSessionRecord:
        record = self.sessions.get(session_id)
        if not record:
            raise KeyError(session_id)
        return record


runtime = AmbientRuntime()


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Starting Tax Concierge ambient service with otel_to_cloud=False")
    yield
    logger.info("Stopping Tax Concierge ambient service")


app = FastAPI(
    title="Tax Concierge Ambient Service",
    description="Event-driven adapter for the Tax Concierge ADK workflow.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "otel_to_cloud": "false"}


@app.post("/event")
async def post_event(event: TaxEvent) -> AmbientEventResponse:
    return await runtime.process_event(event)


@app.post("/upload")
async def post_upload(event: TaxEvent) -> AmbientEventResponse:
    if event.event_type != TaxEventType.DOCUMENT_UPLOADED:
        event = event.model_copy(update={"event_type": TaxEventType.DOCUMENT_UPLOADED})
    return await runtime.process_event(event)


@app.get("/session/{session_id}")
async def get_session(session_id: str) -> AmbientSessionRecord:
    try:
        return await runtime.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


def _content_from_intake(intake: TaxIntake) -> types.Content:
    payload = {"data": intake.model_dump()}
    return types.Content(
        role="user",
        parts=[types.Part.from_text(text=json.dumps(payload))],
    )


def _content_from_followup(
    record: AmbientSessionRecord,
    answers: dict[str, Any],
) -> types.Content:
    field_id = record.pending_field_id or "answers"
    value = answers.get(field_id, answers)
    response = {
        "field_id": field_id,
        "value": value,
    }
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=record.pending_interrupt_id,
                    name="adk_request_input",
                    response=response,
                )
            )
        ],
    )


def _latest_intake(events: list[Any]) -> TaxIntake | None:
    for event in reversed(events):
        output = getattr(event, "output", None)
        if isinstance(output, TaxIntake):
            return output
        if isinstance(output, dict) and "user_story" in output:
            return TaxIntake.model_validate(output)
    return None


def _latest_request_input(events: list[Any]) -> dict[str, Any] | None:
    for event in reversed(events):
        content = getattr(event, "content", None)
        if not content or not content.parts:
            continue
        for part in content.parts:
            function_call = getattr(part, "function_call", None)
            if not function_call or function_call.name != "adk_request_input":
                continue
            args = function_call.args or {}
            payload = args.get("payload") or {}
            messages = payload.get("messages") or []
            return {
                "interrupt_id": args.get("interruptId") or args.get("interrupt_id"),
                "invocation_id": getattr(event, "invocation_id", None),
                "message": args.get("message"),
                "payload": payload,
                "field_id": _field_id_from_a2ui_messages(messages),
            }
    return None


def _field_id_from_a2ui_messages(messages: list[dict[str, Any]]) -> str | None:
    for message in messages:
        for component in message.get("components") or []:
            binding = component.get("binding") or {}
            path = binding.get("path")
            if path:
                return str(path).rstrip("/").split("/")[-1]
    return None


def _summarize_events(events: list[Any]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for event in events:
        content = getattr(event, "content", None)
        function_call = None
        if content and content.parts:
            for part in content.parts:
                if getattr(part, "function_call", None):
                    function_call = part.function_call.name
                    break
        summary.append(
            {
                "node": getattr(getattr(event, "node_info", None), "path", None),
                "route": getattr(getattr(event, "actions", None), "route", None),
                "has_output": getattr(event, "output", None) is not None,
                "function_call": function_call,
            }
        )
    return summary
