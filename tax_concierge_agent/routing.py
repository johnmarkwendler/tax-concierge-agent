import re
from typing import Any

from .models import EntityPath, TaxIntake

OWNER_PATTERNS = {
    "single_owner": re.compile(
        r"\b(only owner|sole owner|just me|by myself|single[- ]member|no partners)\b",
        re.IGNORECASE,
    ),
    "multiple_owners": re.compile(
        r"\b(my wife|my husband|my brother and i|my sister and i|spouse|partner|partners|co[- ]?owner|cofounder|"
        r"two owners|multiple owners|we own|owned with)\b",
        re.IGNORECASE,
    ),
    "s_election": re.compile(
        r"\b(s[- ]?corp|s corporation|s[- ]election|2553)\b", re.IGNORECASE
    ),
    "possible_s_election": re.compile(
        r"\b(filed something with the irs|filed .*2553|irs election|tax election)\b",
        re.IGNORECASE,
    ),
    "negative_s_election": re.compile(
        r"\b(never|did not|didn't|no|not) .{0,30}(s[- ]?corp|s corporation|s[- ]election|2553)\b",
        re.IGNORECASE,
    ),
    "c_corp": re.compile(
        r"\b(c[- ]?corp|c corporation|incorporated|corporation|inc\.?)\b",
        re.IGNORECASE,
    ),
    "llc": re.compile(r"\bllc|limited liability company\b", re.IGNORECASE),
}


def deterministic_entity_candidates(intake: TaxIntake) -> list[EntityPath]:
    story = _routing_text(intake)
    candidates: list[EntityPath] = []

    if OWNER_PATTERNS["s_election"].search(story) and not OWNER_PATTERNS[
        "negative_s_election"
    ].search(story):
        candidates.append("S-Corp")

    if OWNER_PATTERNS["c_corp"].search(story):
        candidates.append("C-Corp")

    if not candidates and OWNER_PATTERNS["multiple_owners"].search(story):
        candidates.append("Partnership")
        if OWNER_PATTERNS["possible_s_election"].search(story):
            candidates.append("S-Corp")

    if not candidates and OWNER_PATTERNS["single_owner"].search(story):
        candidates.extend(["Sole Proprietor", "Single-Member LLC"])

    if not candidates and _known_owner_count(intake.known_facts) == 1:
        candidates.extend(["Sole Proprietor", "Single-Member LLC"])

    if not candidates and (_known_owner_count(intake.known_facts) or 0) > 1:
        candidates.append("Partnership")

    return candidates or ["Cannot Determine Yet"]


def missing_facts_for_candidates(intake: TaxIntake) -> list[str]:
    facts = intake.known_facts
    missing = list(dict.fromkeys(intake.missing_facts))

    if (
        intake.candidate_entities == ["Sole Proprietor", "Single-Member LLC"]
        and OWNER_PATTERNS["single_owner"].search(_routing_text(intake))
        and OWNER_PATTERNS["negative_s_election"].search(_routing_text(intake))
    ):
        return []

    if _known_owner_count(facts) is None and not _story_has_owner_count(intake):
        missing.insert(0, "owner_count")

    if (
        intake.candidate_entities == ["Cannot Determine Yet"]
        and "entity_type_hint" not in missing
    ):
        missing.append("entity_type_hint")

    return missing[:3]


def compute_confidence(intake: TaxIntake) -> float:
    if intake.candidate_entities == ["Cannot Determine Yet"]:
        return 0.25

    if intake.candidate_entities == ["Sole Proprietor", "Single-Member LLC"]:
        score = 0.86
    else:
        score = 0.88 if len(intake.candidate_entities) == 1 else 0.68
    score -= min(len(intake.missing_facts), 3) * 0.12
    return max(0.0, min(1.0, score))


def _routing_text(intake: TaxIntake) -> str:
    values = [intake.user_story]
    values.extend(str(value) for value in intake.known_facts.values())
    return " ".join(values)


def _known_owner_count(facts: dict[str, Any]) -> int | None:
    value = facts.get("owner_count")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"one", "1", "single", "solo"}:
            return 1
        if lowered in {"two", "2"}:
            return 2
        match = re.search(r"\d+", lowered)
        if match:
            return int(match.group())
    return None


def _story_has_owner_count(intake: TaxIntake) -> bool:
    story = _routing_text(intake)
    return bool(
        OWNER_PATTERNS["single_owner"].search(story)
        or OWNER_PATTERNS["multiple_owners"].search(story)
    )
