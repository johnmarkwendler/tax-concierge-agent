from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types


def _load_local_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_local_env()

JUDGE_MODEL = os.getenv("TAX_EVAL_JUDGE_MODEL", "gemini-3.1-flash-lite")


def evaluate_metric(instance: dict[str, Any], metric_name: str, rubric: str) -> dict[str, Any]:
    prompt = f"""
You are grading an ADK Tax Concierge workflow trace.

Metric: {metric_name}
Rubric:
{rubric}

Score from 1 to 5, where 5 is excellent and 1 is failing.
Return only JSON with:
{{"score": <integer 1-5>, "explanation": "<short explanation>"}}

Expected behavior:
{json.dumps(instance.get("expected_behavior", {}), indent=2)}

User prompt:
{json.dumps(instance.get("prompt", {}), indent=2)}

Final response:
{json.dumps(instance.get("response", {}), indent=2)}

Trace summary:
{json.dumps(instance.get("trace_summary", []), indent=2)}

Full agent_data:
{json.dumps(instance.get("agent_data", {}), indent=2)}
"""
    client = genai.Client()
    response = client.models.generate_content(
        model=JUDGE_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0,
        ),
    )
    try:
        parsed = json.loads(response.text or "{}")
    except json.JSONDecodeError:
        return {"score": 1, "explanation": f"Judge returned invalid JSON: {response.text}"}
    score = int(parsed.get("score", 1))
    return {
        "score": max(1, min(5, score)),
        "explanation": str(parsed.get("explanation", "")),
    }
