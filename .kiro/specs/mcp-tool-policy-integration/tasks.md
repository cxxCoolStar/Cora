# Tasks: MCP Tool Policy Integration

## 1. Add pattern matching utility functions

Add utility functions for MCP tool detection and wildcard pattern matching.

- Add `is_mcp_tool(tool_name: str) -> bool` function to `tool_policy_engine.py`
- Add `matches_any_pattern(tool_name: str, patterns: frozenset[str]) -> bool` function
- Import `fnmatch` module for pattern matching
- Add unit tests for `is_mcp_tool()` (test MCP and non-MCP tool names)
- Add unit tests for `matches_any_pattern()` (test various wildcard patterns)

**Acceptance Criteria:**
- `is_mcp_tool("mcp_database_query")` returns `True`
- `is_mcp_tool("read_file")` returns `False`
- `matches_any_pattern("mcp_database_query", {"mcp_database_*"})` returns `True`
- Unit tests pass (2 tests)

## 2. Extend HarnessPolicyProfile with MCP fields

Add MCP-specific policy fields to the `HarnessPolicyProfile` dataclass.

- Add `mcp_default_policy: Literal["allow", "ask", "deny"] | None = None` field
- Add `mcp_allowed_patterns: tuple[str, ...] = field(default_factory=tuple)` field
- Add `mcp_denied_patterns: tuple[str, ...] = field(default_factory=tuple)` field
- Add `mcp_ask_patterns: tuple[str, ...] = field(default_factory=tuple)` field
- Add docstrings for each new field
- Verify existing profiles still work (backward compatibility)

**Acceptance Criteria:**
- `HarnessPolicyProfile` dataclass has 4 new optional fields
- All new fields have default values
- Existing policy profiles instantiate without errors

## 3. Add effective MCP pattern functions
**Dependencies:** Task 2

Add functions to extract effective MCP patterns from policy profiles.

- Add `effective_mcp_denied_patterns(budget: RunBudget) -> frozenset[str]` function
- Add `effective_mcp_ask_patterns(budget: RunBudget) -> frozenset[str]` function
- Add `effective_mcp_allowed_patterns(budget: RunBudget) -> frozenset[str]` function
- Add `effective_mcp_default_policy(budget: RunBudget) -> str | None` function
- Add unit tests for each function
- Handle `None` profile gracefully (return empty frozenset)

**Acceptance Criteria:**
- Functions return patterns from profile if available
- Functions return empty frozenset if profile is None
- Unit tests pass (4 tests)

## 4. Extend ToolPolicyEngine.evaluate() with MCP pattern matching
**Dependencies:** Task 1, Task 2, Task 3

Extend the policy evaluation logic to support MCP tool pattern matching.

- Add MCP tool detection check after exact match checks
- Add denied pattern check (return deny decision if matched)
- Add ask pattern check (return ask decision if matched)
- Add allowed pattern check (continue to HITL/sandbox checks if matched)
- Add default policy check (return decision based on mcp_default_policy)
- Add new policy decision reasons: `mcp_pattern_denied`, `mcp_pattern_requires_confirmation`, `mcp_default_policy_deny`, `mcp_default_policy_ask`
- Update audit metadata to include `matched_pattern` or `mcp_default_policy`
- Add unit tests for each decision path

**Acceptance Criteria:**
- MCP tools are evaluated against patterns
- Denied patterns checked before ask/allow patterns
- Exact matches take precedence over patterns
- Default policy applied if no pattern matches
- Built-in tools unaffected
- Unit tests pass (7 tests)

## 5. Update policy profiles with MCP policies
**Dependencies:** Task 2

Update existing policy profiles with MCP-specific policies.

- Update `wechat_safe` profile: Set `mcp_default_policy="ask"` and `mcp_denied_patterns=("mcp_*_shell", "mcp_*_exec", "mcp_*_delete")`
- Update `background_readonly` profile: Set `mcp_allowed_patterns=("mcp_*_read", "mcp_*_query", "mcp_*_list", "mcp_*_search")` and `mcp_default_policy="deny"`
- Update `planner_readonly` profile: Set `mcp_allowed_patterns=("mcp_*_read", "mcp_*_query", "mcp_*_list")` and `mcp_default_policy="deny"`
- Update `coding_full` profile: Set `mcp_default_policy="allow"`
- Add unit tests for each profile's MCP policies

**Acceptance Criteria:**
- All 4 profiles updated with MCP policies
- `wechat_safe` requires confirmation for MCP tools by default
- `background_readonly` only allows read operations
- Unit tests pass (4 tests)

## 6. Add integration tests for MCP tool policy
**Dependencies:** Task 4, Task 5

Add integration tests to verify end-to-end MCP tool policy enforcement.

- Create `tests/test_mcp_tool_policy_integration.py`
- Add test for `wechat_safe` profile (MCP tool requires HITL)
- Add test for HITL confirmation flow (approve/deny)
- Add test for denied pattern (tool blocked)
- Add test for allowed pattern (tool executes)
- Mock MCP tool execution and HITL confirmation

**Acceptance Criteria:**
- Integration tests cover end-to-end flows
- Tests verify policy decisions are enforced
- All integration tests pass (4 tests)

## 7. Create eval test for MCP tool policy
**Dependencies:** Task 4, Task 5

Create harness eval test to verify MCP tool policy in realistic scenarios.

- Create `evals/cases/harness/mcp_tool_respects_policy.json`
- Add test scenario: MCP tool denied by pattern
- Add test scenario: MCP tool requires HITL by pattern
- Add test scenario: MCP tool allowed by pattern
- Add test scenario: MCP tool uses default policy
- Configure stub MCP server for testing

**Acceptance Criteria:**
- Eval test covers 4 scenarios
- Test uses stub MCP server
- Eval test passes consistently

## 8. Run existing harness evals
**Dependencies:** Task 4, Task 5

Verify that existing harness evals still pass (backward compatibility).

- Run `.\scripts\run_harness_evals.cmd`
- Verify all 39 existing evals pass
- Investigate and fix any failures

**Acceptance Criteria:**
- All 39 existing harness evals pass
- No regressions introduced

## 9. Update documentation
**Dependencies:** Task 4, Task 5, Task 7

Update project documentation to reflect MCP tool policy integration.

- Update PR-6c section in `docs/cora-phase6-mcp-integration-design.md`
- Mark PR-6c as complete with checkmarks
- Create `docs/pr-6c-summary.md` with implementation summary
- Add inline code comments for complex logic

**Acceptance Criteria:**
- Phase 6 design doc updated
- PR-6c marked as complete
- Summary document created

## 10. Code review and cleanup
**Dependencies:** Task 1, Task 2, Task 3, Task 4, Task 5, Task 6, Task 7, Task 8, Task 9

Final code review and cleanup before merging.

- Review all code changes
- Check for code duplication
- Verify consistent code style
- Run linter and fix issues
- Run type checker and fix issues
- Verify all tests pass

**Acceptance Criteria:**
- Code follows project style guidelines
- No linter errors
- No type checker errors
- All unit tests pass (13/13)
- All integration tests pass (4/4)
- All eval tests pass (1/1)
- All existing harness evals pass (39/39)
