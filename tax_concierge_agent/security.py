from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from .models import TaxIntake, UploadedDocument

REDACTION_TOKEN = "[REDACTED:{category}]"
QUARANTINED_TOKEN = "[QUARANTINED_CONTENT]"

SENSITIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ssn", re.compile(r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b")),
    ("ein", re.compile(r"\b(?:ein|employer identification number)[:# ]*\d{2}[- ]?\d{7}\b", re.IGNORECASE)),
    (
        "bank_routing_number",
        re.compile(r"\b(?:routing|routing number|aba)[:# ]*\d{9}\b", re.IGNORECASE),
    ),
    (
        "bank_account_number",
        re.compile(r"\b(?:account|acct|bank account)[:# ]*\d{6,17}\b", re.IGNORECASE),
    ),
    (
        "credit_card_number",
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    ),
    (
        "drivers_license_number",
        re.compile(
            r"\b(?:driver'?s license|drivers license|dl)[:# ]*[A-Z0-9-]{5,20}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "date_of_birth",
        re.compile(
            r"\b(?:dob|date of birth|born)[:# ]*(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
            r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
            r" \d{1,2},? \d{4})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "phone_number",
        re.compile(r"\b(?:\+?1[-. ]?)?(?:\(?\d{3}\)?[-. ]?)\d{3}[-. ]?\d{4}\b"),
    ),
    (
        "email_address",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    ),
    (
        "street_address",
        re.compile(
            r"\b\d{1,6}\s+[A-Z0-9][A-Z0-9 .'-]{1,60}\s+"
            r"(?:street|st|avenue|ave|road|rd|lane|ln|drive|dr|boulevard|blvd|"
            r"court|ct|circle|cir|way|place|pl)\b",
            re.IGNORECASE,
        ),
    ),
]

PROMPT_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore_instructions", re.compile(r"\bignore (?:all )?(?:previous|prior|above) instructions\b", re.IGNORECASE)),
    ("force_entity_recommendation", re.compile(r"\balways recommend (?:s[- ]?corp|c[- ]?corp|partnership|llc)\b", re.IGNORECASE)),
    ("reveal_prompts", re.compile(r"\b(reveal|show|print|dump) (?:hidden|system|developer) prompts?\b", re.IGNORECASE)),
    ("bypass_tax_rules", re.compile(r"\bbypass (?:all )?(?:tax )?rules?\b", re.IGNORECASE)),
    ("auto_approve_return", re.compile(r"\bauto[- ]?approve (?:this )?(?:return|filing)\b", re.IGNORECASE)),
]


def prepare_intake_for_security_checkpoint(intake: TaxIntake) -> TaxIntake:
    """Pre-scrub normalized input so raw sensitive data is not emitted by nodes."""
    return enforce_security_controls(intake)


def enforce_security_controls(intake: TaxIntake) -> TaxIntake:
    redacted_fields: set[str] = set(intake.redacted_fields)
    security_flags: set[str] = set(intake.security_flags)
    quarantined_content: set[str] = set(intake.quarantined_content)

    user_story, story_redactions = redact_text(intake.user_story)
    redacted_fields.update(f"user_story.{category}" for category in story_redactions)

    known_facts = _scrub_value(
        intake.known_facts,
        source="known_facts",
        redacted_fields=redacted_fields,
    )
    uploaded_documents = _scrub_documents(intake.uploaded_documents, redacted_fields)

    scan_targets = {
        "user_story": user_story,
        "known_facts": _flatten_for_scan(known_facts),
        "uploaded_documents": _flatten_for_scan(
            [document.model_dump() for document in uploaded_documents]
        ),
    }
    injection_flags = detect_prompt_injection(scan_targets)
    injection_detected = bool(injection_flags) or intake.injection_detected

    if injection_detected:
        security_flags.update(injection_flags)
        security_flags.add("security_event")
        for source in scan_targets:
            if any(pattern.search(scan_targets[source]) for _, pattern in PROMPT_INJECTION_PATTERNS):
                quarantined_content.add(f"{source}:prompt_injection")
        user_story = QUARANTINED_TOKEN
        known_facts = {}
        uploaded_documents = []

    return intake.model_copy(
        update={
            "user_story": user_story,
            "known_facts": known_facts,
            "uploaded_documents": uploaded_documents,
            "redacted_fields": sorted(redacted_fields),
            "security_flags": sorted(security_flags),
            "injection_detected": injection_detected,
            "quarantined_content": sorted(quarantined_content),
        }
    )


def redact_text(value: str) -> tuple[str, set[str]]:
    redacted = value
    categories: set[str] = set()
    for category, pattern in SENSITIVE_PATTERNS:
        if category == "credit_card_number":
            redacted, found = _redact_credit_cards(redacted)
        else:
            found = bool(pattern.search(redacted))
            redacted = pattern.sub(REDACTION_TOKEN.format(category=category), redacted)
        if found:
            categories.add(category)
    return redacted, categories


def detect_prompt_injection(values_by_source: Mapping[str, str]) -> set[str]:
    flags: set[str] = set()
    for text in values_by_source.values():
        for flag, pattern in PROMPT_INJECTION_PATTERNS:
            if pattern.search(text):
                flags.add(f"prompt_injection.{flag}")
    return flags


def _redact_credit_cards(value: str) -> tuple[str, bool]:
    found = False

    def replace(match: re.Match[str]) -> str:
        nonlocal found
        candidate = match.group()
        digits = re.sub(r"\D", "", candidate)
        if len(digits) >= 13 and _passes_luhn(digits):
            found = True
            return REDACTION_TOKEN.format(category="credit_card_number")
        return candidate

    return SENSITIVE_PATTERNS[4][1].sub(replace, value), found


def _passes_luhn(digits: str) -> bool:
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _scrub_documents(
    documents: list[UploadedDocument],
    redacted_fields: set[str],
) -> list[UploadedDocument]:
    scrubbed: list[UploadedDocument] = []
    for index, document in enumerate(documents):
        document_data = document.model_dump()
        scrubbed_data = _scrub_value(
            document_data,
            source=f"uploaded_documents[{index}]",
            redacted_fields=redacted_fields,
        )
        scrubbed.append(UploadedDocument.model_validate(scrubbed_data))
    return scrubbed


def _scrub_value(value: Any, source: str, redacted_fields: set[str]) -> Any:
    if isinstance(value, str):
        redacted, categories = redact_text(value)
        redacted_fields.update(f"{source}.{category}" for category in categories)
        return redacted
    if isinstance(value, list):
        return [
            _scrub_value(item, source=f"{source}[{index}]", redacted_fields=redacted_fields)
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        return {
            key: _scrub_value(item, source=f"{source}.{key}", redacted_fields=redacted_fields)
            for key, item in value.items()
        }
    return copy.deepcopy(value)


def _flatten_for_scan(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_flatten_for_scan(item) for item in value)
    if isinstance(value, dict):
        return " ".join(f"{key} {_flatten_for_scan(item)}" for key, item in value.items())
    return str(value)
