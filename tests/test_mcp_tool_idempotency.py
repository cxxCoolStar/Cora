"""Tests for MCP tool idempotency metadata and retry classification."""

from __future__ import annotations

from core.agent.idempotency import generate_idempotency_key, is_tool_idempotent
from core.agent.retry_policy import ErrorCategory, classify_error
from core.mcp.metadata import (
    load_mcp_tool_metadata,
    metadata_as_mutating_dict,
    resolve_mcp_tool_metadata,
    set_eval_metadata_overrides,
)


def test_resolve_mcp_tool_metadata_from_config(tmp_path, monkeypatch) -> None:
    config = tmp_path / "mcp_tool_metadata.json"
    config.write_text(
        """
{
  "tools": {
    "mcp_test_write": {
      "is_mutating": true,
      "is_idempotent": true,
      "idempotency_key_extractor": "args.file_path"
    }
  }
}
""".strip(),
        encoding="utf-8",
    )
    load_mcp_tool_metadata(config)
    metadata = resolve_mcp_tool_metadata("mcp_test_write")
    assert metadata is not None
    assert metadata.is_mutating is True
    assert metadata.is_idempotent is True


def test_generate_idempotency_key_for_mcp_test_write(tmp_path, monkeypatch) -> None:
    config = tmp_path / "mcp_tool_metadata.json"
    config.write_text(
        """
{
  "tools": {
    "mcp_test_write": {
      "is_mutating": true,
      "is_idempotent": true,
      "idempotency_key_extractor": "args.file_path"
    }
  }
}
""".strip(),
        encoding="utf-8",
    )
    load_mcp_tool_metadata(config)
    key = generate_idempotency_key(
        run_id="run-mcp",
        task_id="task-1",
        tool_name="mcp_test_write",
        tool_arguments={"file_path": "mcp-checkpoint.txt", "content": "x"},
    )
    assert key == "run-mcp:task-1:mcp_test_write:mcp-checkpoint.txt"
    assert is_tool_idempotent("mcp_test_write") is True


def test_eval_metadata_override_takes_precedence() -> None:
    set_eval_metadata_overrides(
        {
            "mcp_custom_write": {
                "is_mutating": True,
                "is_idempotent": False,
                "idempotency_key_extractor": "args.path",
            }
        }
    )
    meta = metadata_as_mutating_dict("mcp_custom_write")
    assert meta is not None
    assert meta["is_idempotent"] is False
    set_eval_metadata_overrides(None)


def test_classify_mcp_connection_error_as_transient() -> None:
    category, retryable = classify_error(
        error="MCP server test is not connected",
        tool_name="mcp_test_write",
    )
    assert category == ErrorCategory.TRANSIENT
    assert retryable is True


def test_classify_mcp_tool_execution_failure_as_infrastructure() -> None:
    category, retryable = classify_error(
        error="MCP tool execution failed: timeout",
        tool_name="mcp_test_write",
    )
    assert category == ErrorCategory.INFRASTRUCTURE_FAILURE
    assert retryable is True
