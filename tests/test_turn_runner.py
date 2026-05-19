from __future__ import annotations

from core.agent.loop import ToolExecutionTrace
from core.agent.turn_runner import AgentTurnRunner


def test_forced_web_tool_selection_uses_search_for_current_info_request() -> None:
    selection = AgentTurnRunner._forced_web_tool_selection(
        text="Please search the latest OpenAI releases and include source links.",
        lowered="please search the latest openai releases and include source links.",
    )

    assert selection is not None
    assert selection.category == "web_search"
    assert selection.tool_call.tool_name == "web_search"
    assert selection.tool_call.arguments == {
        "query": "search the latest OpenAI releases and include source links."
    }


def test_forced_web_tool_selection_uses_fetch_for_shared_url() -> None:
    selection = AgentTurnRunner._forced_web_tool_selection(
        text="Open and summarize https://example.com/openai/releases for me.",
        lowered="open and summarize https://example.com/openai/releases for me.",
    )

    assert selection is not None
    assert selection.category == "web_fetch"
    assert selection.tool_call.tool_name == "web_fetch"
    assert selection.tool_call.arguments == {
        "url": "https://example.com/openai/releases"
    }


def test_select_final_agent_reply_prefers_archive_not_found_tool_reply() -> None:
    execution = ToolExecutionTrace(
        tool_name="skill_run",
        arguments={
            "name": "archive-core",
            "script_path": "scripts/archive_dispatch.py",
            "input": {"query": "把医保卡照片发给我"},
        },
        action="retrieve",
        status="completed",
        disposition="respond",
        content="没有找到匹配的内容。",
        artifacts=[],
        metadata={"skill_name": "archive-core", "raw_skill_action": "retrieve"},
    )

    reply = AgentTurnRunner.select_final_agent_reply(
        last_execution=execution,
        assistant_text="我检查了一下档案库，目前没有相关资料，你可以重新上传。",
    )

    assert reply == "没有找到匹配的内容。"


def test_select_final_agent_reply_keeps_assistant_text_for_successful_archive_reply() -> None:
    execution = ToolExecutionTrace(
        tool_name="skill_run",
        arguments={
            "name": "archive-core",
            "script_path": "scripts/archive_dispatch.py",
            "input": {"query": "我明天要带什么给王医生？"},
        },
        action="retrieve",
        status="completed",
        disposition="respond",
        content="`明天给王医生带过敏化验单` 的摘要是：明天给王医生带过敏化验单。",
        artifacts=[{"kind": "item", "ref": "item-1", "payload": {}}],
        metadata={"skill_name": "archive-core", "raw_skill_action": "retrieve"},
    )

    reply = AgentTurnRunner.select_final_agent_reply(
        last_execution=execution,
        assistant_text="你明天要带给王医生的是过敏化验单。",
    )

    assert reply == "你明天要带给王医生的是过敏化验单。"


def test_select_final_agent_reply_prefers_scheduled_task_tool_reply() -> None:
    execution = ToolExecutionTrace(
        tool_name="scheduled_tasks",
        arguments={"action": "create"},
        action="automation",
        status="completed",
        disposition="respond",
        content="好的，已设置提醒「喝水提醒」。\n将在 2026-05-19 10:56:00 (Asia/Shanghai) 提醒你。",
        artifacts=[],
        metadata={"scheduled_task_action": "create"},
    )

    reply = AgentTurnRunner.select_final_agent_reply(
        last_execution=execution,
        assistant_text="好的！5分钟后（约 02:56）提醒你喝水。",
    )

    assert reply == "好的，已设置提醒「喝水提醒」。\n将在 2026-05-19 10:56:00 (Asia/Shanghai) 提醒你。"


def test_scheduled_task_retry_detection_matches_reminder_requests() -> None:
    assert AgentTurnRunner._looks_like_scheduled_task_request(
        text="五分钟后提醒我喝水",
        lowered="五分钟后提醒我喝水",
    )
    assert AgentTurnRunner._looks_like_scheduled_task_request(
        text="Remind me in 5 minutes to drink water",
        lowered="remind me in 5 minutes to drink water",
    )
    assert not AgentTurnRunner._looks_like_scheduled_task_request(
        text="喝水有什么好处",
        lowered="喝水有什么好处",
    )
