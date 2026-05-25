"""Unit tests for replay command parsing."""

import pytest

from core.agent.plan_execute import parse_replay_command, ReplayCommand


class TestParseReplayCommand:
    """Test replay command parsing."""

    def test_parse_replay_basic(self):
        """Test basic /replay command."""
        cmd = parse_replay_command("/replay")
        assert cmd is not None
        assert isinstance(cmd, ReplayCommand)
        assert cmd.format == "markdown"

    def test_parse_replay_markdown(self):
        """Test /replay markdown command."""
        cmd = parse_replay_command("/replay markdown")
        assert cmd is not None
        assert cmd.format == "markdown"

    def test_parse_replay_json(self):
        """Test /replay json command."""
        cmd = parse_replay_command("/replay json")
        assert cmd is not None
        assert cmd.format == "json"

    def test_parse_replay_case_insensitive(self):
        """Test case insensitive parsing."""
        cmd = parse_replay_command("/REPLAY JSON")
        assert cmd is not None
        assert cmd.format == "json"

    def test_parse_replay_with_whitespace(self):
        """Test parsing with extra whitespace."""
        cmd = parse_replay_command("  /replay   markdown  ")
        assert cmd is not None
        assert cmd.format == "markdown"

    def test_parse_replay_invalid_format(self):
        """Test invalid format defaults to markdown."""
        cmd = parse_replay_command("/replay html")
        assert cmd is not None
        assert cmd.format == "markdown"  # Invalid format defaults to markdown

    def test_parse_replay_not_replay_command(self):
        """Test non-replay commands return None."""
        assert parse_replay_command("/execute") is None
        assert parse_replay_command("/plan") is None
        assert parse_replay_command("replay") is None
        assert parse_replay_command("") is None
        assert parse_replay_command(None) is None

    def test_parse_replay_empty_string(self):
        """Test empty string returns None."""
        assert parse_replay_command("") is None

    def test_parse_replay_none(self):
        """Test None returns None."""
        assert parse_replay_command(None) is None
