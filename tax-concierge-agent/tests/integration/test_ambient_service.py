import pytest

from tax_concierge_agent.events import TaxEvent, short_subscription_name
from tax_concierge_agent.service import AmbientRuntime


@pytest.mark.asyncio
async def test_ambient_event_session_persists_and_resumes_followup() -> None:
    runtime = AmbientRuntime()

    first = await runtime.process_event(
        TaxEvent(
            event_type="USER_STORY_SUBMITTED",
            user_story="My wife and I formed an LLC and received a K-1.",
        )
    )

    assert first.session_id
    assert first.tax_intake is not None
    assert first.pending_input is not None

    followup = await runtime.process_event(
        TaxEvent(
            event_type="FOLLOWUP_RESPONSE",
            session_id=first.session_id,
            answers={"state": "California", "married": True},
        )
    )

    assert followup.session_id == first.session_id
    assert followup.tax_intake is not None
    assert followup.tax_intake.known_facts


@pytest.mark.asyncio
async def test_ambient_document_upload_adds_document_to_existing_session() -> None:
    runtime = AmbientRuntime()

    first = await runtime.process_event(
        TaxEvent(
            event_type="USER_STORY_SUBMITTED",
            user_story="My wife and I formed an LLC and received a K-1.",
        )
    )
    uploaded = await runtime.process_event(
        TaxEvent(
            event_type="DOCUMENT_UPLOADED",
            session_id=first.session_id,
            document_type="K1",
            file_path="tests/sample_k1.pdf",
        )
    )

    assert uploaded.tax_intake is not None
    assert uploaded.tax_intake.uploaded_documents
    assert uploaded.tax_intake.uploaded_documents[-1].document_type == "K1"


def test_short_subscription_name() -> None:
    assert (
        short_subscription_name("projects/tax-prod/subscriptions/tax-intake-events")
        == "tax-intake-events"
    )
    assert short_subscription_name("already-short") == "already-short"
