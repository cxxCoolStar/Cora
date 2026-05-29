# Requirements: MCP Tool Policy Integration

## Functional Requirements

### FR-1: Wildcard Pattern Matching
**Priority:** Must Have  
**Description:** The system must support Unix shell-style wildcard patterns for matching MCP tool names in policy configurations.

**Acceptance Criteria:**
- `*` matches any sequence of characters
- `?` matches any single character
- `[seq]` matches any character in seq
- `[!seq]` matches any character not in seq
- Pattern `mcp_database_*` matches `mcp_database_query_users`, `mcp_database_write_log`, etc.
- Pattern `mcp_*_read` matches `mcp_database_read`, `mcp_file_read`, etc.

### FR-2: MCP-Specific Policy Fields
**Priority:** Must Have  
**Description:** Policy profiles must support MCP-specific configuration fields for fine-grained control.

**Acceptance Criteria:**
- `mcp_default_policy` field supports "allow", "ask", "deny" values
- `mcp_allowed_patterns` field accepts tuple of wildcard patterns
- `mcp_denied_patterns` field accepts tuple of wildcard patterns
- `mcp_ask_patterns` field accepts tuple of wildcard patterns
- All new fields are optional with sensible defaults
- Existing policy profiles work without modification

### FR-3: Pattern-Based Policy Decisions
**Priority:** Must Have  
**Description:** The policy engine must evaluate MCP tools against wildcard patterns and make allow/deny/ask decisions.

**Acceptance Criteria:**
- MCP tools (names starting with `mcp_`) are evaluated against patterns
- Denied patterns are checked before ask/allow patterns (security-first)
- Exact matches in `allowed_tool_names`/`denied_tool_names` take precedence over patterns
- If no pattern matches, `mcp_default_policy` is applied
- Built-in tools (non-MCP) are unaffected by pattern matching

### FR-4: HITL Integration for MCP Tools
**Priority:** Must Have  
**Description:** MCP tools must integrate with existing HITL (Human-in-the-Loop) confirmation flow.

**Acceptance Criteria:**
- When policy decision is "ask", HITL confirmation is triggered
- User can approve or deny MCP tool execution
- Approved tools execute normally
- Denied tools are blocked with appropriate error message
- HITL flow works identically for MCP and built-in tools

### FR-5: Updated Policy Profiles
**Priority:** Must Have  
**Description:** Existing policy profiles must be updated with MCP-specific policies.

**Acceptance Criteria:**
- `wechat_safe` profile: MCP tools default to "ask", dangerous operations denied
- `background_readonly` profile: Only read/query MCP tools allowed
- `planner_readonly` profile: Only read/query/list MCP tools allowed
- `coding_full` profile: All MCP tools allowed
- Existing built-in tool policies unchanged

### FR-6: Audit Logging
**Priority:** Must Have  
**Description:** All MCP tool policy decisions must be logged with metadata for security review.

**Acceptance Criteria:**
- Policy decisions include `matched_pattern` in audit metadata
- MCP default policy decisions include `mcp_default_policy` in metadata
- Audit logs include tool name, decision, reason, and profile
- Logs are structured for easy querying and analysis

## Non-Functional Requirements

### NFR-1: Backward Compatibility
**Priority:** Must Have  
**Description:** The implementation must maintain 100% backward compatibility with existing code.

**Acceptance Criteria:**
- Existing policy profiles work without modification
- Built-in tools behavior unchanged
- No changes to `ToolPolicyEngine.evaluate()` signature
- All existing harness evals pass (39/39)
- No breaking changes to public APIs

### NFR-2: Performance
**Priority:** Should Have  
**Description:** Pattern matching must have negligible performance impact on policy evaluation.

**Acceptance Criteria:**
- Pattern matching adds < 1ms to policy evaluation time
- `fnmatch.fnmatch()` is O(n) where n is pattern length (< 20 chars typical)
- Pattern matching only applied to MCP tools (subset of all tools)
- No performance regression in existing harness evals

### NFR-3: Security
**Priority:** Must Have  
**Description:** The implementation must follow security best practices and defense-in-depth principles.

**Acceptance Criteria:**
- Deny patterns checked before allow patterns
- Conservative defaults (e.g., "ask" for wechat_safe)
- Exact matches override patterns (fine-grained control)
- Audit logging for all policy decisions
- Pattern matching cannot bypass existing security checks

### NFR-4: Testability
**Priority:** Must Have  
**Description:** The implementation must be thoroughly testable at unit, integration, and eval levels.

**Acceptance Criteria:**
- 13 unit tests covering pattern matching and policy evaluation
- 4 integration tests covering end-to-end flows
- 1 eval test covering real-world scenarios
- All tests pass consistently
- Test coverage > 90% for new code

### NFR-5: Maintainability
**Priority:** Should Have  
**Description:** The code must be clean, well-documented, and easy to maintain.

**Acceptance Criteria:**
- Clear function names and docstrings
- Inline comments explaining design decisions
- Consistent code style with existing codebase
- No code duplication
- Easy to add new patterns or policy types

## Constraints

### C-1: Technology Stack
- Must use Python's `fnmatch` module for pattern matching
- Must integrate with existing `ToolPolicyEngine` class
- Must use existing `HarnessPolicyProfile` dataclass structure

### C-2: Compatibility
- Must work with Python 3.10+
- Must not break existing policy profiles
- Must not require database schema changes

### C-3: Scope
- Only stdio MCP transport supported (HTTP/WebSocket out of scope)
- Only fnmatch patterns supported (regex out of scope for this PR)
- Only policy integration (idempotency/retry in separate PRs)

## Success Metrics

### M-1: Test Coverage
- Unit tests: 13/13 passing
- Integration tests: 4/4 passing
- Eval tests: 1/1 passing
- Existing harness evals: 39/39 passing

### M-2: Functionality
- MCP tools respect policy profiles
- Wildcard patterns work correctly
- HITL confirmation works for MCP tools
- Zero breaking changes

### M-3: Performance
- Policy evaluation time increase < 1ms
- No regression in existing eval performance

### M-4: Security
- All policy decisions audited
- Deny patterns checked first
- Conservative defaults applied

## Out of Scope

The following are explicitly out of scope for this PR:

1. **Regular Expression Patterns** - Only fnmatch wildcards supported
2. **Dynamic Policy Updates** - Policies loaded at startup only
3. **Per-User Policies** - Only profile-based policies
4. **MCP Tool Idempotency** - Covered in PR-6d
5. **MCP Tool Retry Logic** - Covered in PR-6d
6. **HTTP/WebSocket Transport** - Only stdio transport
7. **Policy Analytics** - Future enhancement
8. **Policy Testing CLI** - Future enhancement
