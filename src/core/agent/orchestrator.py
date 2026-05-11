from __future__ import annotations

from dataclasses import dataclass

from core.agent.loop import AgentLoop, LoopResult
from core.agent.prompt_builder import AgentPromptBuilder
from core.agent.runtime_state import ConversationRuntimeState
from core.agent.skill_loader import SkillLoader
from core.schemas.message import Message


@dataclass(slots=True)
class OrchestratorInput:
    session_id: str
    user_text: str
    runtime: ConversationRuntimeState
    upload_name: str | None = None
    delivery_available: bool = False
    history: list[Message] | None = None


class AgentOrchestrator:
    def __init__(
        self,
        *,
        loop: AgentLoop,
        prompt_builder: AgentPromptBuilder | None = None,
        skill_loader: SkillLoader | None = None,
    ) -> None:
        self.loop = loop
        self.prompt_builder = prompt_builder or AgentPromptBuilder()
        self.skill_loader = skill_loader or SkillLoader()

    async def handle_turn(self, turn: OrchestratorInput) -> LoopResult:
        skills = self.skill_loader.list_skills()
        messages = self.prompt_builder.build_messages(
            session_id=turn.session_id,
            user_text=turn.user_text,
            runtime=turn.runtime,
            skills=skills,
            history=turn.history or [],
            upload_name=turn.upload_name,
            delivery_available=turn.delivery_available,
        )
        return await self.loop.run(
            session_id=turn.session_id,
            initial_messages=messages,
            runtime=turn.runtime,
        )
