"""Unit tests for MCP tool policy integration."""

import pytest

from core.agent.policy_profiles import HarnessPolicyProfile
from core.agent.tool_policy_engine import (
    ToolPolicyEngine,
    effective_allowed_tool_names,
    effective_denied_tool_names,
    effective_mcp_allowed_patterns,
    effective_mcp_ask_patterns,
    effective_mcp_default_policy,
    effective_mcp_denied_patterns,
    is_mcp_tool,
    matches_any_pattern,
)
from core.schemas.harness import RunBudget
from core.schemas.tool_policy import ToolPolicyContext


class TestMCPToolDetection:
    """Tests for is_mcp_tool() function."""
    
    def test_is_mcp_tool_with_mcp_prefix(self):
        """MCP tools start with 'mcp_' prefix."""
        assert is_mcp_tool("mcp_database_query") is True
        assert is_mcp_tool("mcp_aws_s3_upload") is True
        assert is_mcp_tool("mcp_custom_send_email") is True
    
    def test_is_mcp_tool_without_mcp_prefix(self):
        """Built-in tools don't have 'mcp_' prefix."""
        assert is_mcp_tool("read_file") is False
        assert is_mcp_tool("write_file") is False
        assert is_mcp_tool("shell_exec") is False
    
    def test_is_mcp_tool_edge_cases(self):
        """Edge cases for MCP tool detection."""
        assert is_mcp_tool("mcp_") is True  # Just prefix
        assert is_mcp_tool("MCP_database") is False  # Wrong case
        assert is_mcp_tool("_mcp_database") is False  # Prefix not at start
        assert is_mcp_tool("") is False  # Empty string


class TestPatternMatching:
    """Tests for matches_any_pattern() function."""
    
    def test_matches_wildcard_star(self):
        """Test * wildcard matching."""
        patterns = frozenset(["mcp_database_*"])
        assert matches_any_pattern("mcp_database_query", patterns) is True
        assert matches_any_pattern("mcp_database_write", patterns) is True
        assert matches_any_pattern("mcp_database_", patterns) is True
        assert matches_any_pattern("mcp_aws_s3_upload", patterns) is False
    
    def test_matches_wildcard_question(self):
        """Test ? wildcard matching."""
        patterns = frozenset(["mcp_db_?"])
        assert matches_any_pattern("mcp_db_1", patterns) is True
        assert matches_any_pattern("mcp_db_a", patterns) is True
        assert matches_any_pattern("mcp_db_12", patterns) is False
        assert matches_any_pattern("mcp_db_", patterns) is False
    
    def test_matches_multiple_patterns(self):
        """Test matching against multiple patterns."""
        patterns = frozenset(["mcp_database_*", "mcp_aws_*"])
        assert matches_any_pattern("mcp_database_query", patterns) is True
        assert matches_any_pattern("mcp_aws_s3_upload", patterns) is True
        assert matches_any_pattern("mcp_custom_tool", patterns) is False
    
    def test_matches_no_patterns(self):
        """Test with empty pattern set."""
        patterns = frozenset()
        assert matches_any_pattern("mcp_database_query", patterns) is False
        assert matches_any_pattern("any_tool", patterns) is False
    
    def test_matches_exact_match(self):
        """Test exact match (no wildcards)."""
        patterns = frozenset(["mcp_database_query"])
        assert matches_any_pattern("mcp_database_query", patterns) is True
        assert matches_any_pattern("mcp_database_write", patterns) is False
    
    def test_matches_complex_patterns(self):
        """Test complex wildcard patterns."""
        patterns = frozenset(["mcp_*_read", "mcp_*_query"])
        assert matches_any_pattern("mcp_database_read", patterns) is True
        assert matches_any_pattern("mcp_file_read", patterns) is True
        assert matches_any_pattern("mcp_database_query", patterns) is True
        assert matches_any_pattern("mcp_database_write", patterns) is False


class TestHarnessPolicyProfileMCPFields:
    """Tests for HarnessPolicyProfile MCP fields."""
    
    def test_profile_with_no_mcp_fields(self):
        """Profile can be created without MCP fields (backward compatibility)."""
        profile = HarnessPolicyProfile(
            name="test_profile",
            allowed_tool_names=("read_file", "write_file"),
        )
        assert profile.name == "test_profile"
        assert profile.mcp_default_policy is None
        assert profile.mcp_allowed_patterns == ()
        assert profile.mcp_denied_patterns == ()
        assert profile.mcp_ask_patterns == ()
    
    def test_profile_with_mcp_default_policy(self):
        """Profile can set MCP default policy."""
        profile = HarnessPolicyProfile(
            name="test_profile",
            mcp_default_policy="ask",
        )
        assert profile.mcp_default_policy == "ask"
    
    def test_profile_with_mcp_patterns(self):
        """Profile can set MCP patterns."""
        profile = HarnessPolicyProfile(
            name="test_profile",
            mcp_allowed_patterns=("mcp_*_read",),
            mcp_denied_patterns=("mcp_*_delete",),
            mcp_ask_patterns=("mcp_*_write",),
        )
        assert profile.mcp_allowed_patterns == ("mcp_*_read",)
        assert profile.mcp_denied_patterns == ("mcp_*_delete",)
        assert profile.mcp_ask_patterns == ("mcp_*_write",)
    
    def test_profile_with_all_mcp_fields(self):
        """Profile can set all MCP fields."""
        profile = HarnessPolicyProfile(
            name="test_profile",
            mcp_default_policy="deny",
            mcp_allowed_patterns=("mcp_database_*",),
            mcp_denied_patterns=("mcp_aws_*",),
            mcp_ask_patterns=("mcp_*_admin",),
        )
        assert profile.mcp_default_policy == "deny"
        assert profile.mcp_allowed_patterns == ("mcp_database_*",)
        assert profile.mcp_denied_patterns == ("mcp_aws_*",)
        assert profile.mcp_ask_patterns == ("mcp_*_admin",)




class TestEffectiveMCPPatternFunctions:
    """Tests for effective_mcp_*_patterns() functions."""

    def test_effective_mcp_denied_patterns_without_profile(self):
        """Return empty frozenset when profile is None."""
        budget = RunBudget(policy_profile=None)
        patterns = effective_mcp_denied_patterns(budget)
        assert patterns == frozenset()
    
    def test_effective_mcp_ask_patterns_without_profile(self):
        """Return empty frozenset when profile is None."""
        budget = RunBudget(policy_profile=None)
        patterns = effective_mcp_ask_patterns(budget)
        assert patterns == frozenset()
    
    def test_effective_mcp_allowed_patterns_without_profile(self):
        """Return empty frozenset when profile is None."""
        budget = RunBudget(policy_profile=None)
        patterns = effective_mcp_allowed_patterns(budget)
        assert patterns == frozenset()
    
    def test_effective_mcp_default_policy_without_profile(self):
        """Return None when profile is None."""
        budget = RunBudget(policy_profile=None)
        policy = effective_mcp_default_policy(budget)
        assert policy is None



class TestPolicyProfilesMCPConfiguration:
    """Tests for policy profiles MCP configuration."""
    
    def test_wechat_safe_profile_mcp_config(self):
        """wechat_safe profile requires confirmation for MCP tools."""
        budget = RunBudget(policy_profile="wechat_safe")
        
        # Should have default policy "ask"
        assert effective_mcp_default_policy(budget) == "ask"
        
        # Should deny dangerous operations
        denied = effective_mcp_denied_patterns(budget)
        assert "mcp_*_shell" in denied
        assert "mcp_*_exec" in denied
        assert "mcp_*_delete" in denied
    
    def test_background_readonly_profile_mcp_config(self):
        """background_readonly profile only allows read operations."""
        budget = RunBudget(policy_profile="background_readonly")
        
        # Should have default policy "deny"
        assert effective_mcp_default_policy(budget) == "deny"
        
        # Should allow read operations
        allowed = effective_mcp_allowed_patterns(budget)
        assert "mcp_*_read" in allowed
        assert "mcp_*_query" in allowed
        assert "mcp_*_list" in allowed
        assert "mcp_*_search" in allowed
    
    def test_planner_readonly_profile_mcp_config(self):
        """planner_readonly profile only allows read/query/list operations."""
        budget = RunBudget(policy_profile="planner_readonly")
        
        # Should have default policy "deny"
        assert effective_mcp_default_policy(budget) == "deny"
        
        # Should allow read operations
        allowed = effective_mcp_allowed_patterns(budget)
        assert "mcp_*_read" in allowed
        assert "mcp_*_query" in allowed
        assert "mcp_*_list" in allowed
    
    def test_coding_full_profile_mcp_config(self):
        """coding_full profile allows all MCP tools."""
        budget = RunBudget(policy_profile="coding_full")
        
        # Should have default policy "allow"
        assert effective_mcp_default_policy(budget) == "allow"


class TestToolPolicyEngineMCPDecisions:
    """Integration-style tests for MCP policy evaluation paths."""

    @staticmethod
    def _context(*, tool_name: str, policy_profile: str) -> ToolPolicyContext:
        budget = RunBudget(policy_profile=policy_profile)
        return ToolPolicyContext(
            tool_name=tool_name,
            policy_profile=policy_profile,
            allowed_tool_names=effective_allowed_tool_names(budget),
            denied_tool_names=effective_denied_tool_names(budget),
        )

    def test_wechat_safe_denies_mcp_delete_pattern(self) -> None:
        decision = ToolPolicyEngine().evaluate(
            self._context(tool_name="mcp_example_delete", policy_profile="wechat_safe")
        )
        assert decision.decision == "deny"
        assert decision.reason == "mcp_pattern_denied"

    def test_wechat_safe_asks_for_unmatched_mcp_tool(self) -> None:
        decision = ToolPolicyEngine().evaluate(
            self._context(tool_name="mcp_example_echo", policy_profile="wechat_safe")
        )
        assert decision.decision == "ask"
        assert decision.reason == "mcp_default_policy_ask"

    def test_background_readonly_denies_non_read_mcp_tool(self) -> None:
        decision = ToolPolicyEngine().evaluate(
            self._context(tool_name="mcp_example_write", policy_profile="background_readonly")
        )
        assert decision.decision == "deny"
        assert decision.reason == "mcp_default_policy_deny"

    def test_background_readonly_allows_read_pattern_mcp_tool(self) -> None:
        decision = ToolPolicyEngine().evaluate(
            self._context(tool_name="mcp_filesystem_read", policy_profile="background_readonly")
        )
        assert decision.decision == "allow"
        assert decision.reason == "tool_allowed"

    def test_coding_full_allows_mcp_tool(self) -> None:
        decision = ToolPolicyEngine().evaluate(
            self._context(tool_name="mcp_example_echo", policy_profile="coding_full")
        )
        assert decision.decision == "allow"
