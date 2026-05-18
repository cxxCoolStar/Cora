from __future__ import annotations

from core.agent.loop import ToolExecutionTrace
from core.agent.turn_runner import AgentTurnRunner


def test_select_final_agent_reply_prefers_archive_not_found_tool_reply() -> None:
    execution = ToolExecutionTrace(
        tool_name="skill_run",
        arguments={"name": "archive-core", "script_path": "scripts/archive_dispatch.py", "input": {"query": "把医保卡照片发给我"}},
        action="retrieve",
        status="completed",
        disposition="respond",
        content="没有找到匹配的内容。",
        artifacts=[],
        metadata={"skill_name": "archive-core", "raw_skill_action": "retrieve"},
    )

    reply = AgentTurnRunner.select_final_agent_reply(
        last_execution=execution,
        assistant_text="我检查了一下档案库，目前没有任何相关资料，您可以重新上传。",
    )

    assert reply == "没有找到匹配的内容。"


def test_select_final_agent_reply_keeps_assistant_text_for_successful_archive_reply() -> None:
    execution = ToolExecutionTrace(
        tool_name="skill_run",
        arguments={"name": "archive-core", "script_path": "scripts/archive_dispatch.py", "input": {"query": "我明天要带什么给王医生？"}},
        action="retrieve",
        status="completed",
        disposition="respond",
        content="`帮我保存：明天给王医生带过敏化验单。` 的摘要是：明天给王医生带过敏化验单。",
        artifacts=[{"kind": "item", "ref": "item-1", "payload": {}}],
        metadata={"skill_name": "archive-core", "raw_skill_action": "retrieve"},
    )

    reply = AgentTurnRunner.select_final_agent_reply(
        last_execution=execution,
        assistant_text="你明天要带给王医生的是过敏化验单。",
    )

    assert reply == "你明天要带给王医生的是过敏化验单。"
