# PR-6c: MCP Tool Policy Integration - Technical Design

## 1. Overview

This design extends Cora's existing Tool Policy Engine to support MCP (Model Context Protocol) tools with the same policy controls as built-in tools. MCP tools will respect policy profiles, support wildcard pattern matching, and integrate with HITL (Human-in-the-Loop) confirmation flows.

**Key Goals:**
- MCP tools respect policy profiles (allow/deny/ask decisions)
- Support wildcard patterns for MCP tool policies (e.g., `mcp_database_*`, `mcp_aws_*`)
- MCP tools trigger HITL confirmation when required
- Maintain backward compatibility with existing policy system
- Zero changes to existing built-in tool policies

## 2. Current Architecture Analysis

### 2.1 Existing Tool Policy System

**Components:**
- `ToolPolicyEngine.evaluate()`: Main policy evaluation logic
- `HarnessPolicyProfile`: Policy profile definitions (wechat_safe, background_readonly, etc.)
- `ToolPolicyContext`: Input context for policy evaluation
- `ToolPolicyDecision`: Output decision (allow/ask/deny/sandbox)

**Current Flow:**
```
ToolPolicyContext → ToolPolicyEngine.evaluate() → ToolPolicyDecision
```

**Existing Checks (in order):**
1. Max tool calls exceeded → deny
2. Role not allowed → deny
3. Tool not in allowed_tool_names → deny
4. Tool in denied_tool_names → deny
5. Requires HITL confirmation → ask
6. Requires sandbox execution → sandbox
7. Default → allow

### 2.2 MCP Tool Naming Convention

MCP tools use prefixed names to avoid conflicts:
- Format: `mcp_{server_name}_{original_tool_name}`
- Examples:
  - `mcp_database_query_users`
  - `mcp_aws_s3_upload`
  - `mcp_custom_send_email`

### 2.3 Current Limitations

**Problem 1: Exact Match Only**
- Current policy checks use exact string matching
- Cannot match patterns like `mcp_database_*` or `mcp_aws_*`
- Must list every MCP tool individually (not scalable)

**Problem 2: No MCP-Specific Policies**
- Policy profiles don't distinguish MCP tools from built-in tools
- Cannot apply different policies to MCP tools by server

**Problem 3: No Default MCP Policy**
- No way to set a default policy for all MCP tools
- Each MCP tool must be explicitly configured

## 3. Design Solution

### 3.1 Wildcard Pattern Matching

**Approach: fnmatch-style patterns**

Support Unix shell-style wildcards in policy configurations:
- `*` matches any sequence of characters
- `?` matches any single character
- `[seq]` matches any character in seq
- `[!seq]` matches any character not in seq

**Examples:**
- `mcp_database_*` → matches all database MCP tools
- `mcp_aws_*` → matches all AWS MCP tools
- `mcp_*` → matches all MCP tools
- `mcp_database_query_*` → matches specific database query tools

**Implementation:**
```python
import fnmatch

def tool_matches_pattern(tool_name: str, pattern: str) -> bool:
    """Check if tool name matches a wildcard pattern.
    
    Args:
        tool_name: Tool name to check (e.g., "mcp_database_query_users")
        pattern: Pattern to match (e.g., "mcp_database_*")
    
    Returns:
        True if tool name matches pattern
    """
    return fnmatch.fnmatch(tool_name, pattern)
```

### 3.2 Extended Policy Profile Structure

**Add MCP-specific policy fields to HarnessPolicyProfile:**

```python
@dataclass(frozen=True, slots=True)
class HarnessPolicyProfile:
    name: str
    allowed_tool_names: tuple[str, ...] = field(default_factory=tuple)
    denied_tool_names: tuple[str, ...] = field(default_factory=tuple)
    max_tool_calls: int | None = None
    
    # NEW: MCP-specific policies
    mcp_default_policy: Literal["allow", "ask", "deny"] | None = None
    """Default policy for all MCP tools (if not matched by other rules)"""
    
    mcp_allowed_patterns: tuple[str, ...] = field(default_factory=tuple)
    """Wildcard patterns for allowed MCP tools (e.g., 'mcp_database_*')"""
    
    mcp_denied_patterns: tuple[str, ...] = field(default_factory=tuple)
    """Wildcard patterns for denied MCP tools (e.g., 'mcp_aws_*')"""
    
    mcp_ask_patterns: tuple[str, ...] = field(default_factory=tuple)
    """Wildcard patterns for MCP tools requiring HITL (e.g., 'mcp_*_write')"""
```

**Example Profile Configurations:**

```python
# wechat_safe: Deny dangerous tools, ask for MCP tools
HarnessPolicyProfile(
    name="wechat_safe",
    denied_tool_names=("shell_exec", "browser_navigate", ...),
    mcp_default_policy="ask",  # All MCP tools require confirmation
    mcp_denied_patterns=("mcp_aws_*", "mcp_*_delete"),  # Deny AWS and delete operations
)

# background_readonly: Only allow read operations
HarnessPolicyProfile(
    name="background_readonly",
    allowed_tool_names=("list_files", "read_file", ...),
    mcp_allowed_patterns=("mcp_*_read", "mcp_*_query"),  # Allow read/query MCP tools
    mcp_default_policy="deny",  # Deny all other MCP tools
)

# coding_full: Allow everything
HarnessPolicyProfile(
    name="coding_full",
    mcp_default_policy="allow",  # Allow all MCP tools
)
```

### 3.3 Enhanced Policy Evaluation Logic

**New evaluation order in `ToolPolicyEngine.evaluate()`:**

```
1. Max tool calls check (unchanged)
2. Role check (unchanged)
3. Exact match checks (unchanged):
   - allowed_tool_names
   - denied_tool_names
4. NEW: Pattern matching checks (for MCP tools only):
   - mcp_denied_patterns → deny
   - mcp_ask_patterns → ask
   - mcp_allowed_patterns → allow
5. NEW: MCP default policy check:
   - If tool is MCP and mcp_default_policy is set → apply default
6. HITL confirmation check (unchanged)
7. Sandbox check (unchanged)
8. Default allow (unchanged)
```

**Key Design Decisions:**

**Decision 1: Exact matches take precedence over patterns**
- Rationale: Allows fine-grained overrides
- Example: `mcp_database_admin` can be denied even if `mcp_database_*` is allowed

**Decision 2: Deny patterns checked before ask/allow patterns**
- Rationale: Security-first approach
- Example: `mcp_aws_*` denied even if `mcp_*` is allowed

**Decision 3: Pattern matching only for MCP tools**
- Rationale: Backward compatibility, built-in tools use exact matching
- Detection: Tool name starts with `mcp_`

**Decision 4: MCP default policy is last resort**
- Rationale: Explicit patterns take precedence
- Example: If no pattern matches, use `mcp_default_policy`

### 3.4 Implementation Functions

**Function 1: Check if tool is MCP tool**

```python
def is_mcp_tool(tool_name: str) -> bool:
    """Check if a tool is an MCP tool.
    
    Args:
        tool_name: Tool name to check
    
    Returns:
        True if tool name starts with 'mcp_'
    """
    return tool_name.startswith("mcp_")
```

**Function 2: Match tool against patterns**

```python
def matches_any_pattern(tool_name: str, patterns: frozenset[str]) -> bool:
    """Check if tool name matches any pattern in the set.
    
    Args:
        tool_name: Tool name to check
        patterns: Set of wildcard patterns
    
    Returns:
        True if tool name matches at least one pattern
    """
    import fnmatch
    return any(fnmatch.fnmatch(tool_name, pattern) for pattern in patterns)
```

**Function 3: Get effective MCP patterns from profile**

```python
def effective_mcp_denied_patterns(budget: RunBudget) -> frozenset[str]:
    """Get effective MCP denied patterns from profile.
    
    Args:
        budget: Run budget with policy profile
    
    Returns:
        Set of denied patterns
    """
    profile = get_harness_policy_profile(budget.policy_profile)
    if profile is None:
        return frozenset()
    return frozenset(profile.mcp_denied_patterns)

def effective_mcp_ask_patterns(budget: RunBudget) -> frozenset[str]:
    """Get effective MCP ask patterns from profile."""
    profile = get_harness_policy_profile(budget.policy_profile)
    if profile is None:
        return frozenset()
    return frozenset(profile.mcp_ask_patterns)

def effective_mcp_allowed_patterns(budget: RunBudget) -> frozenset[str]:
    """Get effective MCP allowed patterns from profile."""
    profile = get_harness_policy_profile(budget.policy_profile)
    if profile is None:
        return frozenset()
    return frozenset(profile.mcp_allowed_patterns)

def effective_mcp_default_policy(budget: RunBudget) -> str | None:
    """Get effective MCP default policy from profile."""
    profile = get_harness_policy_profile(budget.policy_profile)
    if profile is None:
        return None
    return profile.mcp_default_policy
```

**Function 4: Enhanced evaluate() method**

```python
class ToolPolicyEngine:
    def evaluate(self, context: ToolPolicyContext) -> ToolPolicyDecision:
        # ... existing checks (max_tool_calls, roles, exact matches) ...
        
        # NEW: MCP tool pattern matching
        if is_mcp_tool(context.tool_name):
            # Check denied patterns first (security-first)
            denied_patterns = effective_mcp_denied_patterns(context)
            if matches_any_pattern(context.tool_name, denied_patterns):
                return deny_tool_policy_decision(
                    tool_name=context.tool_name,
                    reason="mcp_pattern_denied",
                    policy_profile=context.policy_profile,
                    risk=normalize_tool_risk(context.tool_risk),
                    audit_metadata={
                        **audit_metadata,
                        "matched_pattern": "mcp_denied_patterns",
                    },
                )
            
            # Check ask patterns (HITL required)
            ask_patterns = effective_mcp_ask_patterns(context)
            if matches_any_pattern(context.tool_name, ask_patterns):
                return ask_tool_policy_decision(
                    tool_name=context.tool_name,
                    reason="mcp_pattern_requires_confirmation",
                    policy_profile=context.policy_profile,
                    risk=normalize_tool_risk(context.tool_risk),
                    requires_confirmation=True,
                    audit_metadata={
                        **audit_metadata,
                        "matched_pattern": "mcp_ask_patterns",
                    },
                )
            
            # Check allowed patterns
            allowed_patterns = effective_mcp_allowed_patterns(context)
            if allowed_patterns and matches_any_pattern(context.tool_name, allowed_patterns):
                # Continue to HITL/sandbox checks below
                pass
            elif allowed_patterns:
                # Has allowed patterns but tool doesn't match
                # Check if there's a default policy
                default_policy = effective_mcp_default_policy(context)
                if default_policy == "deny":
                    return deny_tool_policy_decision(
                        tool_name=context.tool_name,
                        reason="mcp_default_policy_deny",
                        policy_profile=context.policy_profile,
                        risk=normalize_tool_risk(context.tool_risk),
                        audit_metadata={**audit_metadata, "mcp_default_policy": "deny"},
                    )
                elif default_policy == "ask":
                    return ask_tool_policy_decision(
                        tool_name=context.tool_name,
                        reason="mcp_default_policy_ask",
                        policy_profile=context.policy_profile,
                        risk=normalize_tool_risk(context.tool_risk),
                        requires_confirmation=True,
                        audit_metadata={**audit_metadata, "mcp_default_policy": "ask"},
                    )
            else:
                # No allowed patterns, check default policy
                default_policy = effective_mcp_default_policy(context)
                if default_policy == "deny":
                    return deny_tool_policy_decision(
                        tool_name=context.tool_name,
                        reason="mcp_default_policy_deny",
                        policy_profile=context.policy_profile,
                        risk=normalize_tool_risk(context.tool_risk),
                        audit_metadata={**audit_metadata, "mcp_default_policy": "deny"},
                    )
                elif default_policy == "ask":
                    return ask_tool_policy_decision(
                        tool_name=context.tool_name,
                        reason="mcp_default_policy_ask",
                        policy_profile=context.policy_profile,
                        risk=normalize_tool_risk(context.tool_risk),
                        requires_confirmation=True,
                        audit_metadata={**audit_metadata, "mcp_default_policy": "ask"},
                    )
        
        # ... existing HITL, sandbox, and default allow checks ...
```

### 3.5 HITL Integration

**No changes needed to HITL flow!**

The existing HITL system already handles `ask` decisions from the policy engine. When `ToolPolicyEngine.evaluate()` returns an `ask` decision for an MCP tool:

1. Policy engine returns `ask_tool_policy_decision()`
2. Tool executor detects `decision="ask"`
3. HITL confirmation request sent to user
4. User approves/denies
5. Tool execution proceeds or aborts

**MCP tools integrate seamlessly with existing HITL infrastructure.**

### 3.6 Updated Policy Profiles

**wechat_safe profile:**
```python
HarnessPolicyProfile(
    name="wechat_safe",
    denied_tool_names=("shell_exec", "browser_navigate", "browser_click", "browser_type", "browser_back"),
    mcp_default_policy="ask",  # All MCP tools require confirmation by default
    mcp_denied_patterns=("mcp_*_shell", "mcp_*_exec", "mcp_*_delete"),  # Deny dangerous operations
)
```

**background_readonly profile:**
```python
HarnessPolicyProfile(
    name="background_readonly",
    allowed_tool_names=(
        "list_files", "search_files", "read_file",
        "web_search", "web_fetch",
        "skills_list", "skill_view", "skill_run",
        "search_sessions",
    ),
    mcp_allowed_patterns=("mcp_*_read", "mcp_*_query", "mcp_*_list", "mcp_*_search"),
    mcp_default_policy="deny",  # Deny all other MCP tools
)
```

**planner_readonly profile:**
```python
HarnessPolicyProfile(
    name="planner_readonly",
    allowed_tool_names=(
        "list_files", "search_files", "read_file",
        "web_search", "web_fetch",
        "skills_list", "skill_view",
        "search_sessions",
    ),
    mcp_allowed_patterns=("mcp_*_read", "mcp_*_query", "mcp_*_list"),
    mcp_default_policy="deny",
)
```

**coding_full profile:**
```python
HarnessPolicyProfile(
    name="coding_full",
    mcp_default_policy="allow",  # Allow all MCP tools
)
```

## 4. Data Flow

### 4.1 Policy Evaluation Flow for MCP Tools

```
┌─────────────────────────────────────────────────────────────┐
│ Tool Invocation: mcp_database_query_users                   │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ ToolPolicyEngine.evaluate(context)                          │
│  - tool_name: "mcp_database_query_users"                    │
│  - policy_profile: "wechat_safe"                            │
│  - platform: "wechat"                                       │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 1: Check max_tool_calls                                │
│ Result: PASS (not exceeded)                                 │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 2: Check allowed_roles                                 │
│ Result: PASS (role allowed)                                 │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 3: Check exact allowed_tool_names                      │
│ Result: SKIP (not in list)                                  │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 4: Check exact denied_tool_names                       │
│ Result: PASS (not in list)                                  │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 5: Is MCP tool? (starts with "mcp_")                   │
│ Result: YES                                                 │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 6: Check mcp_denied_patterns                           │
│ Patterns: ["mcp_*_shell", "mcp_*_exec", "mcp_*_delete"]    │
│ Result: PASS (no match)                                     │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 7: Check mcp_ask_patterns                              │
│ Patterns: []                                                │
│ Result: SKIP (no patterns)                                  │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 8: Check mcp_allowed_patterns                          │
│ Patterns: []                                                │
│ Result: SKIP (no patterns)                                  │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 9: Check mcp_default_policy                            │
│ Default: "ask"                                              │
│ Result: RETURN ask_tool_policy_decision()                   │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ ToolPolicyDecision                                          │
│  - decision: "ask"                                          │
│  - reason: "mcp_default_policy_ask"                         │
│  - requires_confirmation: True                              │
│  - safe_user_message: "This action needs your confirmation  │
│    before I run `mcp_database_query_users`."                │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ HITL Confirmation Request                                   │
│ User approves → Tool executes                               │
│ User denies → Tool aborted                                  │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 Pattern Matching Examples

**Example 1: Denied by pattern**
- Tool: `mcp_aws_s3_delete`
- Profile: `wechat_safe` with `mcp_denied_patterns=("mcp_*_delete",)`
- Result: **DENY** (matches `mcp_*_delete`)

**Example 2: Allowed by pattern**
- Tool: `mcp_database_read_users`
- Profile: `background_readonly` with `mcp_allowed_patterns=("mcp_*_read",)`
- Result: **ALLOW** (matches `mcp_*_read`)

**Example 3: Ask by default policy**
- Tool: `mcp_custom_send_email`
- Profile: `wechat_safe` with `mcp_default_policy="ask"`
- Result: **ASK** (no pattern match, uses default)

**Example 4: Exact match overrides pattern**
- Tool: `mcp_database_admin`
- Profile: `allowed_tool_names=()`, `denied_tool_names=("mcp_database_admin",)`, `mcp_allowed_patterns=("mcp_database_*",)`
- Result: **DENY** (exact match in denied_tool_names takes precedence)

## 5. Testing Strategy

### 5.1 Unit Tests

**Test file: `tests/test_mcp_tool_policy.py`**

Test cases:
1. `test_is_mcp_tool()` - Detect MCP tools correctly
2. `test_matches_any_pattern()` - Wildcard pattern matching
3. `test_mcp_denied_pattern_denies_tool()` - Denied patterns work
4. `test_mcp_ask_pattern_requires_confirmation()` - Ask patterns work
5. `test_mcp_allowed_pattern_allows_tool()` - Allowed patterns work
6. `test_mcp_default_policy_deny()` - Default deny works
7. `test_mcp_default_policy_ask()` - Default ask works
8. `test_mcp_default_policy_allow()` - Default allow works
9. `test_exact_match_overrides_pattern()` - Exact matches take precedence
10. `test_denied_pattern_overrides_allowed_pattern()` - Deny patterns checked first
11. `test_non_mcp_tool_unaffected()` - Built-in tools unchanged
12. `test_wechat_safe_profile_mcp_tools()` - wechat_safe profile works
13. `test_background_readonly_profile_mcp_tools()` - background_readonly profile works

### 5.2 Integration Tests

**Test file: `tests/test_mcp_tool_policy_integration.py`**

Test cases:
1. `test_mcp_tool_respects_wechat_safe_policy()` - End-to-end with wechat_safe
2. `test_mcp_tool_hitl_confirmation_flow()` - HITL integration works
3. `test_mcp_tool_denied_by_pattern()` - Denied tools blocked
4. `test_mcp_tool_allowed_by_pattern()` - Allowed tools execute

### 5.3 Eval Tests

**Eval file: `evals/cases/harness/mcp_tool_respects_policy.json`**

Test scenarios:
1. MCP tool denied by pattern → agent doesn't execute
2. MCP tool requires HITL by pattern → agent requests confirmation
3. MCP tool allowed by pattern → agent executes
4. MCP tool uses default policy → correct behavior

## 6. Backward Compatibility

**Zero breaking changes:**

1. **Existing policy profiles unchanged** - New fields are optional with default values
2. **Built-in tools unaffected** - Pattern matching only applies to MCP tools
3. **Existing policy checks preserved** - New checks added after existing ones
4. **HITL flow unchanged** - MCP tools use existing HITL infrastructure
5. **API compatibility** - No changes to `ToolPolicyEngine.evaluate()` signature

**Migration path:**
- Existing deployments work without changes
- New MCP-specific fields can be added incrementally
- Default behavior: MCP tools follow same rules as built-in tools

## 7. Security Considerations

### 7.1 Defense in Depth

**Layer 1: Pattern-based denial**
- Deny dangerous patterns (e.g., `mcp_*_delete`, `mcp_*_exec`)
- Checked before allow patterns

**Layer 2: Default policy**
- Conservative default (e.g., `ask` for wechat_safe)
- Requires explicit allow patterns for sensitive environments

**Layer 3: HITL confirmation**
- User approval required for medium/high risk operations
- Applies to MCP tools same as built-in tools

**Layer 4: Audit logging**
- All policy decisions logged with metadata
- Pattern matches recorded for security review

### 7.2 Threat Model

**Threat 1: Malicious MCP server**
- Mitigation: Policy profiles can deny entire server (e.g., `mcp_malicious_*`)
- Mitigation: Default policy can be set to `deny` or `ask`

**Threat 2: Privilege escalation**
- Mitigation: Role-based access control (existing)
- Mitigation: Pattern-based denial of dangerous operations

**Threat 3: Data exfiltration**
- Mitigation: Deny patterns for upload/send operations
- Mitigation: HITL confirmation for sensitive operations

## 8. Performance Considerations

**Pattern matching overhead:**
- `fnmatch.fnmatch()` is O(n) where n is pattern length
- Typical patterns are short (< 20 chars)
- Checked only for MCP tools (subset of all tools)
- Negligible impact on policy evaluation time

**Optimization opportunities:**
- Cache compiled patterns (future enhancement)
- Short-circuit on first match (already implemented)

## 9. Implementation Checklist

### Phase 1: Core Pattern Matching
- [ ] Add `is_mcp_tool()` function
- [ ] Add `matches_any_pattern()` function
- [ ] Add MCP fields to `HarnessPolicyProfile`
- [ ] Add `effective_mcp_*_patterns()` functions
- [ ] Unit tests for pattern matching

### Phase 2: Policy Engine Integration
- [ ] Extend `ToolPolicyEngine.evaluate()` with MCP checks
- [ ] Add new policy decision reasons (mcp_pattern_denied, etc.)
- [ ] Update audit metadata for MCP decisions
- [ ] Unit tests for policy evaluation

### Phase 3: Profile Updates
- [ ] Update `wechat_safe` profile with MCP policies
- [ ] Update `background_readonly` profile with MCP policies
- [ ] Update `planner_readonly` profile with MCP policies
- [ ] Update `coding_full` profile with MCP policies

### Phase 4: Testing
- [ ] Unit tests (13 tests)
- [ ] Integration tests (4 tests)
- [ ] Eval test (`mcp_tool_respects_policy.json`)
- [ ] Run existing harness evals (ensure 39/39 pass)

### Phase 5: Documentation
- [ ] Update PR-6c section in phase 6 design doc
- [ ] Create PR-6c summary document
- [ ] Add inline code comments

## 10. Success Criteria

- [ ] All unit tests pass (13/13)
- [ ] All integration tests pass (4/4)
- [ ] Eval test passes (`mcp_tool_respects_policy.json`)
- [ ] Existing harness evals still pass (39/39)
- [ ] MCP tools respect policy profiles
- [ ] Wildcard patterns work correctly
- [ ] HITL confirmation works for MCP tools
- [ ] Zero breaking changes to existing code
- [ ] Code review approved
- [ ] Documentation complete

## 11. Future Enhancements

### 11.1 Advanced Pattern Syntax
- Regular expressions for complex patterns
- Negative patterns (e.g., `!mcp_database_admin`)
- Pattern groups (e.g., `{read,query,list}`)

### 11.2 Dynamic Policy Updates
- Reload policy profiles without restart
- Per-session policy overrides
- User-specific policy customization

### 11.3 Policy Analytics
- Track which patterns match most frequently
- Identify unused patterns
- Suggest policy optimizations

### 11.4 Policy Testing Tools
- CLI tool to test policy decisions
- Policy simulator for debugging
- Policy coverage analysis
