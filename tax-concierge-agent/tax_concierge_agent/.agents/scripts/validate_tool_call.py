#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
AGENTS_DIR = Path(__file__).resolve().parents[1]
LOG_PATH = AGENTS_DIR / "tool_call_security.log"

ALLOWED_BINARIES = {
    "agents-cli",
    "make",
    "pre-commit",
    "pytest",
    "semgrep",
    "uv",
}

DANGEROUS_PATTERNS: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
    (
        "sudo",
        re.compile(r"(^|\s)sudo(\s|$)"),
        "sudo can execute commands with elevated privileges outside the project boundary.",
        "Use unprivileged project tooling or ask for explicit approval with a specific reason.",
    ),
    (
        "rm_rf",
        re.compile(
            r"(^|\s)rm\s+[^;&|]*-[^\s]*r[^\s]*f|"
            r"(^|\s)rm\s+[^;&|]*-[^\s]*f[^\s]*r"
        ),
        "Recursive forced deletion can destroy project, user, or system files.",
        "Use reviewed cleanup targets such as make clean, or request explicit approval.",
    ),
    (
        "chmod_777",
        re.compile(r"(^|\s)chmod\s+777(\s|$)"),
        "chmod 777 grants broad write/execute permissions and can weaken local security.",
        "Use the narrowest required permission change and request review before changing modes.",
    ),
    (
        "curl",
        re.compile(r"(^|\s)curl(\s|$)"),
        "curl performs arbitrary network calls and can download or exfiltrate data.",
        "Use reviewed API clients, checked-in fixtures, or explicitly approved network tooling.",
    ),
    (
        "wget",
        re.compile(r"(^|\s)wget(\s|$)"),
        "wget performs arbitrary network downloads and can introduce unreviewed code or data.",
        "Use reviewed API clients, checked-in fixtures, or explicitly approved network tooling.",
    ),
    (
        "pip_install",
        re.compile(r"(^|\s)(python\d*(\.\d+)?\s+-m\s+)?pip\s+install(\s|$)"),
        "pip install bypasses the uv-managed dependency and lockfile workflow.",
        "Add dependencies to pyproject.toml and install through uv with reviewed lockfile changes.",
    ),
)

ENV_MUTATION_PATTERN = re.compile(
    r"(^|\s)(export|unset|setenv|unsetenv)\s+[A-Za-z_][A-Za-z0-9_]*|"
    r"^[A-Z_][A-Z0-9_]*=.*\s+\S+"
)
NETWORK_URL_PATTERN = re.compile(r"\b(?:https?|ftp|s3|gs)://", re.IGNORECASE)
SHELL_REDIRECT_PATTERN = re.compile(r"(^|\s)(?:>|>>|2>|&>)\s*(\S+)")
DELETE_OUTSIDE_PATTERN = re.compile(r"(^|\s)(?:rm|unlink)\s+(?!-)(\S+)")
WRITE_FLAGS = {"-o", "--output", "--output-document", "--directory-prefix", "--target-directory"}


@dataclass(frozen=True)
class SecurityResponse:
    allowed: bool
    rule: str
    reason: str
    guidance: str
    command: str
    tool_type: str = "run_command"
    categories: list[str] = field(default_factory=list)


def main() -> int:
    configure_logging()
    payload = parse_stdin_payload()
    command = extract_command(payload)
    response = validate_tool_request(payload, command)
    log_decision(response)

    body = build_security_response(response)
    stream = sys.stdout if response.allowed else sys.stderr
    print(json.dumps(body), file=stream)
    return 0 if response.allowed else 2


def configure_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def parse_stdin_payload() -> dict[str, Any]:
    if len(sys.argv) > 1:
        return {"command": " ".join(sys.argv[1:])}

    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"command": raw}
    return parsed if isinstance(parsed, dict) else {"command": str(parsed)}


def extract_command(payload: dict[str, Any]) -> str:
    containers = [
        payload,
        payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {},
        payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {},
        payload.get("input") if isinstance(payload.get("input"), dict) else {},
    ]
    for container in containers:
        for key in ("command", "cmd"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list) and value:
                return " ".join(str(part) for part in value)
    return ""


def validate_tool_request(payload: dict[str, Any], command: str) -> SecurityResponse:
    tool_type = str(payload.get("tool_name") or payload.get("tool") or "run_command")
    if tool_type != "run_command":
        return validate_future_tool_request(tool_type, payload)
    return validate_run_command(command)


def validate_run_command(command: str) -> SecurityResponse:
    if not command:
        return deny(
            command,
            "empty_command",
            "No command was supplied to the tool validator.",
            "Provide a concrete command or skip tool execution.",
        )

    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return deny(
            command,
            "parse_error",
            f"Command could not be parsed safely: {exc}",
            "Use simple argv-style commands without unmatched quoting.",
        )

    if not tokens:
        return deny(
            command,
            "empty_command",
            "No executable was supplied to the tool validator.",
            "Provide a concrete command or skip tool execution.",
        )

    dangerous = is_dangerous_shell_command(command, tokens)
    if dangerous is not None:
        return dangerous

    if not is_allowed_command(tokens):
        executable = Path(tokens[0]).name
        return deny(
            command,
            "unknown_binary",
            f"Executable '{executable}' is not on the allowlist.",
            "Use pytest, uv, make, agents-cli, semgrep, pre-commit, or a Python script inside the repository.",
        )

    return allow(command, "allowed_developer_command", "Command matches the safe development allowlist.")


def is_dangerous_shell_command(
    command: str, tokens: list[str]
) -> SecurityResponse | None:
    for rule, pattern, reason, guidance in DANGEROUS_PATTERNS:
        if pattern.search(command):
            return deny(command, rule, reason, guidance)

    if ENV_MUTATION_PATTERN.search(command):
        return deny(
            command,
            "environment_mutation",
            "The command modifies environment variables, which can alter tool behavior or leak secrets.",
            "Use checked-in configuration, documented .env setup, or request explicit approval.",
        )

    if NETWORK_URL_PATTERN.search(command):
        return deny(
            command,
            "arbitrary_network_call",
            "The command contains a network URL and may call an unreviewed external service.",
            "Use reviewed API clients, fixtures, or explicitly approved network tooling.",
        )

    redirect_match = SHELL_REDIRECT_PATTERN.search(command)
    if redirect_match and not is_path_inside_repo(Path(redirect_match.group(2))):
        return deny(
            command,
            "write_outside_repository",
            f"Shell redirection writes outside the repository root: {redirect_match.group(2)}",
            "Write only within the repository using reviewed file-editing paths.",
        )

    for index, token in enumerate(tokens):
        if token in WRITE_FLAGS and index + 1 < len(tokens):
            target = Path(tokens[index + 1])
            if not is_path_inside_repo(target):
                return deny(
                    command,
                    "write_outside_repository",
                    f"Output flag writes outside the repository root: {target}",
                    "Write only within the repository or request explicit approval.",
                )

    delete_match = DELETE_OUTSIDE_PATTERN.search(command)
    if delete_match and not is_path_inside_repo(Path(delete_match.group(2))):
        return deny(
            command,
            "delete_outside_project",
            f"The command deletes a path outside the repository root: {delete_match.group(2)}",
            "Use reviewed cleanup targets inside the project, or request explicit approval.",
        )

    return None


def is_allowed_command(tokens: list[str]) -> bool:
    executable = Path(tokens[0]).name
    if executable in ALLOWED_BINARIES:
        return True
    if executable.startswith("python"):
        return is_repository_python_script(tokens)
    return False


def is_repository_python_script(tokens: list[str]) -> bool:
    script_token = None
    for token in tokens[1:]:
        if token == "-m":
            return False
        if token.startswith("-"):
            continue
        script_token = token
        break
    if script_token is None:
        return False
    script_path = Path(script_token)
    return script_path.suffix == ".py" and is_path_inside_repo(script_path)


def is_path_inside_repo(path: Path) -> bool:
    candidate = path if path.is_absolute() else REPOSITORY_ROOT / path
    try:
        candidate.resolve().relative_to(REPOSITORY_ROOT)
    except ValueError:
        return False
    return True


def validate_future_tool_request(tool_type: str, payload: dict[str, Any]) -> SecurityResponse:
    validators = {
        "document_extraction": validate_document_extraction_tool,
        "runpod_flash": validate_runpod_flash_request,
        "external_api": validate_external_api_request,
        "mcp_tool": validate_mcp_tool_invocation,
        "database_write": validate_database_write,
        "file_upload": validate_file_upload,
    }
    validator = validators.get(tool_type)
    if validator is None:
        return deny(
            "",
            "unknown_tool_type",
            f"No validator is registered for tool type '{tool_type}'.",
            "Register a deterministic validator before using this tool type.",
            tool_type=tool_type,
        )
    return validator(payload)


def validate_document_extraction_tool(payload: dict[str, Any]) -> SecurityResponse:
    return allow("", "future_validator_placeholder", "Document extraction validator extension point.", "document_extraction")


def validate_runpod_flash_request(payload: dict[str, Any]) -> SecurityResponse:
    return allow("", "future_validator_placeholder", "Runpod Flash validator extension point.", "runpod_flash")


def validate_external_api_request(payload: dict[str, Any]) -> SecurityResponse:
    return allow("", "future_validator_placeholder", "External API validator extension point.", "external_api")


def validate_mcp_tool_invocation(payload: dict[str, Any]) -> SecurityResponse:
    return allow("", "future_validator_placeholder", "MCP tool validator extension point.", "mcp_tool")


def validate_database_write(payload: dict[str, Any]) -> SecurityResponse:
    return allow("", "future_validator_placeholder", "Database write validator extension point.", "database_write")


def validate_file_upload(payload: dict[str, Any]) -> SecurityResponse:
    return allow("", "future_validator_placeholder", "File upload validator extension point.", "file_upload")


def allow(
    command: str,
    rule: str,
    reason: str,
    tool_type: str = "run_command",
) -> SecurityResponse:
    return SecurityResponse(
        allowed=True,
        rule=rule,
        reason=reason,
        guidance="Proceed with the requested tool call.",
        command=command,
        tool_type=tool_type,
    )


def deny(
    command: str,
    rule: str,
    reason: str,
    guidance: str,
    tool_type: str = "run_command",
) -> SecurityResponse:
    return SecurityResponse(
        allowed=False,
        rule=rule,
        reason=reason,
        guidance=guidance,
        command=command,
        tool_type=tool_type,
        categories=[rule],
    )


def build_security_response(response: SecurityResponse) -> dict[str, Any]:
    body: dict[str, Any] = {
        "allowed": response.allowed,
        "rule": response.rule,
        "reason": response.reason,
        "guidance": response.guidance,
        "tool_type": response.tool_type,
    }
    if not response.allowed:
        body["error"] = (
            f"Blocked by {response.rule}: {response.reason} "
            f"Safe path: {response.guidance}"
        )
        body["categories"] = response.categories
    return body


def log_decision(response: SecurityResponse) -> None:
    level = logging.INFO if response.allowed else logging.WARNING
    logging.log(
        level,
        json.dumps(
            {
                "allowed": response.allowed,
                "rule": response.rule,
                "reason": response.reason,
                "guidance": response.guidance,
                "command": response.command,
                "tool_type": response.tool_type,
            },
            sort_keys=True,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
