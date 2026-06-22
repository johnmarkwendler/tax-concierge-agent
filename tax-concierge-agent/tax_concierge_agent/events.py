from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .models import TaxIntake, UploadedDocument


class TaxEventType(StrEnum):
    USER_STORY_SUBMITTED = "USER_STORY_SUBMITTED"
    DOCUMENT_UPLOADED = "DOCUMENT_UPLOADED"
    FOLLOWUP_RESPONSE = "FOLLOWUP_RESPONSE"


class TaxEvent(BaseModel):
    event_type: TaxEventType
    session_id: str | None = None
    user_id: str = "ambient_user"
    user_story: str | None = None
    uploaded_documents: list[UploadedDocument] = Field(default_factory=list)
    document_type: str | None = None
    file_path: str | None = None
    answers: dict[str, Any] = Field(default_factory=dict)
    known_facts: dict[str, Any] = Field(default_factory=dict)
    source_subscription: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def normalize_event_to_intake(
    event: TaxEvent,
    existing_intake: TaxIntake | None = None,
) -> TaxIntake:
    base = existing_intake or TaxIntake(user_story="")
    known_facts = dict(base.known_facts)
    known_facts.update(event.known_facts)
    uploaded_documents = list(base.uploaded_documents)
    user_story = base.user_story

    if event.source_subscription:
        known_facts["source_subscription"] = short_subscription_name(
            event.source_subscription
        )

    if event.event_type == TaxEventType.USER_STORY_SUBMITTED:
        user_story = event.user_story or ""
        uploaded_documents.extend(event.uploaded_documents)

    if event.event_type == TaxEventType.DOCUMENT_UPLOADED:
        document = UploadedDocument(
            name=event.file_path or "uploaded_document",
            document_type=event.document_type,
            metadata={
                **event.metadata,
                "file_path": event.file_path,
            },
        )
        uploaded_documents.append(document)
        known_facts["last_document_type"] = event.document_type
        if not user_story:
            user_story = f"Document uploaded for tax intake: {event.document_type or 'unknown document'}."

    if event.event_type == TaxEventType.FOLLOWUP_RESPONSE:
        known_facts.update(event.answers)

    return base.model_copy(
        update={
            "user_story": user_story,
            "uploaded_documents": uploaded_documents,
            "known_facts": known_facts,
            "next_ui": None,
        }
    )


def short_subscription_name(value: str) -> str:
    match = re.fullmatch(r"projects/[^/]+/subscriptions/([^/]+)", value)
    return match.group(1) if match else value
