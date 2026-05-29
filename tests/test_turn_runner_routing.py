from __future__ import annotations

from types import SimpleNamespace

from core.agent.loop import ToolExecutionTrace
from core.agent.skill_loader import SkillLoader
from core.agent.turn_policies import TurnDecisionPolicy
from core.agent.turn_runner import AgentTurnRunner


def test_forced_tool_selection_leaves_delivery_requests_for_retry_first() -> None:
    runner = object.__new__(AgentTurnRunner)

    selection = AgentTurnRunner.forced_tool_selection(
        runner,
        user_text="send me the saved photo from earlier.",
        raw_text="send me the saved photo from earlier.",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )

    assert selection is None


def test_fallback_tool_selection_uses_skill_runtime_metadata_for_delivery() -> None:
    runner = object.__new__(AgentTurnRunner)
    runner.skill_loader = SkillLoader()

    selection = AgentTurnRunner.fallback_tool_selection(
        runner,
        retry_category="deliver",
        user_text="send me the saved photo from earlier.",
        raw_text="send me the saved photo from earlier.",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )

    assert selection is not None
    assert selection.category == "deliver"
    assert selection.tool_call.tool_name == "archive_run"
    assert selection.tool_call.arguments["intent"] == "deliver"


def test_fallback_tool_selection_uses_skill_runtime_metadata_for_delete() -> None:
    runner = object.__new__(AgentTurnRunner)
    runner.skill_loader = SkillLoader()

    selection = AgentTurnRunner.fallback_tool_selection(
        runner,
        retry_category="delete",
        user_text="delete the saved photo from earlier.",
        raw_text="delete the saved photo from earlier.",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )

    assert selection is not None
    assert selection.category == "delete"
    assert selection.tool_call.tool_name == "skill_run"
    assert selection.tool_call.arguments["name"] == "archive-core"
    assert selection.tool_call.arguments["script_path"] == "scripts/archive_dispatch.py"
    assert selection.tool_call.arguments["input"]["intent"] == "delete"


def test_delivery_retry_detection_matches_send_back_requests() -> None:
    runner = object.__new__(AgentTurnRunner)

    category = AgentTurnRunner.tool_retry_category(
        runner,
        user_text="send me the saved photo from earlier.",
        raw_text="send me the saved photo from earlier.",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )

    assert category == "deliver"


def test_turn_decision_policy_returns_forced_tool_selection_as_a_structured_decision() -> None:
    runner = object.__new__(AgentTurnRunner)

    decision = TurnDecisionPolicy.from_runner(runner).initial_decision(
        user_text="read `README.md`",
        raw_text="read `README.md`",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )

    assert decision.retry is None
    assert decision.forced_tool_selection is not None
    assert decision.forced_tool_selection.category == "file_read"
    assert decision.forced_tool_selection.tool_call.tool_name == "read_file"
    assert decision.forced_tool_selection.tool_call.arguments == {"path": "README.md"}


def test_delete_retry_detection_prefers_skill_runtime_metadata() -> None:
    runner = object.__new__(AgentTurnRunner)
    runner.skill_loader = SkillLoader()

    category = AgentTurnRunner.tool_retry_category(
        runner,
        user_text="delete the saved photo from earlier.",
        raw_text="delete the saved photo from earlier.",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )

    assert category == "delete"


def test_user_memory_retry_detection_uses_native_tool_route_when_available() -> None:
    runner = object.__new__(AgentTurnRunner)
    runner.loop = SimpleNamespace(tool_specs=[SimpleNamespace(name="user_memory")])

    category = AgentTurnRunner.tool_retry_category(
        runner,
        user_text="remember this: I prefer dark mode.",
        raw_text="remember this: I prefer dark mode.",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )

    assert category == "user_memory"


def test_scheduled_task_retry_detection_uses_native_tool_route_when_available() -> None:
    runner = object.__new__(AgentTurnRunner)
    runner.loop = SimpleNamespace(tool_specs=[SimpleNamespace(name="scheduled_tasks")])

    category = AgentTurnRunner.tool_retry_category(
        runner,
        user_text="Remind me in 5 minutes to drink water.",
        raw_text="Remind me in 5 minutes to drink water.",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )

    assert category == "scheduled_task"


def test_session_search_retry_detection_uses_native_tool_route_when_available() -> None:
    runner = object.__new__(AgentTurnRunner)
    runner.loop = SimpleNamespace(tool_specs=[SimpleNamespace(name="search_sessions")])

    category = AgentTurnRunner.tool_retry_category(
        runner,
        user_text="What did I tell you before about the VPN profile?",
        raw_text="What did I tell you before about the VPN profile?",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )

    assert category == "session_search"


def test_native_tool_route_skips_retry_when_tool_is_unavailable() -> None:
    runner = object.__new__(AgentTurnRunner)
    runner.loop = SimpleNamespace(tool_specs=[SimpleNamespace(name="search_files")])

    scheduled_category = AgentTurnRunner.tool_retry_category(
        runner,
        user_text="Remind me in 5 minutes to drink water.",
        raw_text="Remind me in 5 minutes to drink water.",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )
    memory_category = AgentTurnRunner.tool_retry_category(
        runner,
        user_text="remember this: I prefer dark mode.",
        raw_text="remember this: I prefer dark mode.",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )
    session_search_category = AgentTurnRunner.tool_retry_category(
        runner,
        user_text="What did I tell you before about the VPN profile?",
        raw_text="What did I tell you before about the VPN profile?",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )

    assert scheduled_category is None
    assert memory_category is None
    assert session_search_category is None


def test_turn_decision_policy_returns_retry_directive_for_native_tool_retries() -> None:
    runner = object.__new__(AgentTurnRunner)
    runner.loop = SimpleNamespace(tool_specs=[SimpleNamespace(name="search_sessions")])

    decision = TurnDecisionPolicy.from_runner(runner).initial_decision(
        user_text="What did I tell you before about the VPN profile?",
        raw_text="What did I tell you before about the VPN profile?",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )

    assert decision.forced_tool_selection is None
    assert decision.retry is not None
    assert decision.retry.category == "session_search"
    assert "search_sessions tool" in decision.retry.instruction


def test_fallback_tool_selection_uses_search_sessions_for_session_search() -> None:
    runner = object.__new__(AgentTurnRunner)
    runner.loop = SimpleNamespace(tool_specs=[SimpleNamespace(name="search_sessions")])

    selection = AgentTurnRunner.fallback_tool_selection(
        runner,
        retry_category="session_search",
        user_text="What did I tell you before about the VPN profile?",
        raw_text="What did I tell you before about the VPN profile?",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )

    assert selection is not None
    assert selection.category == "session_search"
    assert selection.tool_call.tool_name == "search_sessions"
    assert selection.tool_call.arguments == {"query": "VPN profile"}


def test_refined_session_search_query_extracts_keywords_from_long_request() -> None:
    query = AgentTurnRunner._refined_session_search_query(
        "Can you pull up whatever we said earlier about the backup retention period and the recovery window?"
    )

    assert query == "backup retention period recovery window"


def test_refined_session_search_query_keeps_named_terms_and_codes() -> None:
    query = AgentTurnRunner._refined_session_search_query(
        "Search my chat for the Q3 budget review for Project Atlas and the invoice cutoff."
    )

    assert query == "Q3 budget review Project Atlas invoice cutoff"


def test_session_search_retry_detection_ignores_non_request_status_text() -> None:
    runner = object.__new__(AgentTurnRunner)
    runner.loop = SimpleNamespace(tool_specs=[SimpleNamespace(name="search_sessions")])

    category = AgentTurnRunner.tool_retry_category(
        runner,
        user_text="The previous session timed out unexpectedly.",
        raw_text="The previous session timed out unexpectedly.",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )

    assert category is None


def test_select_final_agent_reply_humanizes_session_search_reply_without_assistant_text() -> None:
    execution = ToolExecutionTrace(
        tool_name="search_sessions",
        arguments={"query": "VPN profile"},
        action="retrieve",
        status="completed",
        disposition="respond",
        content="Conversation history matches for `VPN profile` across 2 session(s): ...",
        artifacts=[],
        metadata={
            "query": "VPN profile",
            "hit_count": 2,
            "hits": [
                {
                    "session_id": "session-1",
                    "source": "summary",
                    "role": None,
                    "score": 42,
                    "created_at": None,
                    "excerpt": "Resolved requests: VPN profile corp-vpn uses the tokyo gateway. | Recent decisions: Keep the office VPN settings handy for later recall.",
                },
                {
                    "session_id": "session-1",
                    "source": "message",
                    "role": "user",
                    "score": 21,
                    "created_at": None,
                    "excerpt": "Please remember the office VPN profile is named corp-vpn and uses the tokyo gateway.",
                },
            ],
        },
    )

    reply = AgentTurnRunner.select_final_agent_reply(
        last_execution=execution,
        assistant_text=None,
    )

    assert reply == "I found a few relevant earlier matches. The clearest one says: VPN profile corp-vpn uses the tokyo gateway."


def test_fallback_tool_selection_skips_skill_routing_when_skill_run_is_unavailable() -> None:
    runner = object.__new__(AgentTurnRunner)
    runner.skill_loader = SkillLoader()
    runner.loop = SimpleNamespace(tool_specs=[SimpleNamespace(name="scheduled_tasks")])

    selection = AgentTurnRunner.fallback_tool_selection(
        runner,
        retry_category="deliver",
        user_text="send me the saved photo from earlier.",
        raw_text="send me the saved photo from earlier.",
        upload=None,
        loop_result=SimpleNamespace(tool_trace=[], exit_reason="assistant_text"),
    )

    assert selection is None


def test_select_final_agent_reply_prefers_generic_skill_retrieve_reply_without_artifacts() -> None:
    execution = ToolExecutionTrace(
        tool_name="skill_run",
        arguments={
            "name": "research-core",
            "script_path": "scripts/research_dispatch.py",
            "input": {"intent": "retrieve", "query": "latest export"},
        },
        action="retrieve",
        status="completed",
        disposition="respond",
        content="No matching export was found.",
        artifacts=[],
        metadata={"skill_name": "research-core", "raw_skill_action": "retrieve"},
    )

    reply = AgentTurnRunner.select_final_agent_reply(
        last_execution=execution,
        assistant_text="I checked, but I could not find the export you meant.",
    )

    assert reply == "No matching export was found."
