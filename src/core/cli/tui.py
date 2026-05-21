from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, TextIO

from core.clawbot.dependencies import build_clawbot_container
from core.clawbot.service import ClawBotService
from core.clawbot.service_runtime import AssistantTurnOutcome
from core.config import CoreSettings


InputFunc = Callable[[str], str]


@dataclass(slots=True)
class InteractiveChatShell:
    clawbot_service: ClawBotService
    session_id: str | None = None
    trace_enabled: bool = True
    input_func: InputFunc = input
    output: TextIO = field(default_factory=lambda: sys.stdout)

    def run(self) -> int:
        try:
            self._ensure_session()
        except Exception as exc:  # pragma: no cover - defensive shell bootstrap
            self._write_line(f"Failed to initialize shell session: {exc}")
            return 1

        self._render_banner()
        while True:
            try:
                raw = self.input_func(self._prompt())
            except EOFError:
                self._write_line("")
                self._write_line("Bye.")
                return 0
            except KeyboardInterrupt:
                self._write_line("")
                self._write_line("Bye.")
                return 0

            text = raw.strip()
            if not text:
                continue

            if text.startswith("/"):
                should_exit = self._handle_command(text)
                if should_exit:
                    self._write_line("Bye.")
                    return 0
                continue

            try:
                outcome = asyncio.run(
                    self.clawbot_service.reply_outcome(
                        session_id=self._session_id(),
                        text=text,
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive interactive loop
                self._write_line(f"Error: {exc}")
                continue
            self._render_outcome(outcome)

    def _ensure_session(self) -> None:
        if self.session_id:
            self.clawbot_service.get_session_debug(session_id=self.session_id)
            return
        self.session_id = self.clawbot_service.create_session().id

    def _prompt(self) -> str:
        return f"cora[{self._session_id()[:8]}]> "

    def _session_id(self) -> str:
        assert self.session_id is not None
        return self.session_id

    def _render_banner(self) -> None:
        tool_names = self.clawbot_service.list_tool_names()
        self._write_line("Cora interactive shell")
        self._write_line(f"Session: {self._session_id()}")
        self._write_line(f"Preset: {self.clawbot_service.toolset_preset}")
        self._write_line(f"Trace: {'on' if self.trace_enabled else 'off'}")
        self._write_line(f"Tools: {', '.join(tool_names) if tool_names else '(none)'}")
        if "shell_exec" not in tool_names:
            self._write_line("Note: shell_exec is not available in this preset. Set CORA_TOOLSET_PRESET=cli for the local coding shell.")
        self._write_line("Commands: /help /new /session /trace /items /tools /debug /exit")
        self._write_line("")

    def _handle_command(self, text: str) -> bool:
        command, _, remainder = text.partition(" ")
        verb = command.lower()
        arg = remainder.strip()

        if verb in {"/exit", "/quit"}:
            return True
        if verb == "/help":
            self._render_help()
            return False
        if verb == "/new":
            self.session_id = self.clawbot_service.create_session().id
            self._write_line(f"Started new session: {self._session_id()}")
            return False
        if verb == "/session":
            self._write_line(f"Current session: {self._session_id()}")
            return False
        if verb == "/trace":
            self._toggle_trace(arg)
            return False
        if verb == "/items":
            self._render_items()
            return False
        if verb == "/tools":
            self._write_line(f"Available tools: {', '.join(self.clawbot_service.list_tool_names())}")
            return False
        if verb == "/debug":
            self._render_debug()
            return False

        self._write_line(f"Unknown command: {command}. Use /help.")
        return False

    def _render_help(self) -> None:
        self._write_line("Slash commands:")
        self._write_line("/help    Show this help message")
        self._write_line("/new     Start a fresh session")
        self._write_line("/session Show the current session id")
        self._write_line("/trace   Toggle tool trace, or use /trace on|off")
        self._write_line("/items   List saved items for this session")
        self._write_line("/tools   List currently available tools")
        self._write_line("/debug   Show session counts and recent decisions")
        self._write_line("/exit    Quit the shell")

    def _toggle_trace(self, arg: str) -> None:
        normalized = arg.lower()
        if normalized == "on":
            self.trace_enabled = True
        elif normalized == "off":
            self.trace_enabled = False
        else:
            self.trace_enabled = not self.trace_enabled
        self._write_line(f"Trace {'enabled' if self.trace_enabled else 'disabled'}.")

    def _render_items(self) -> None:
        items = self.clawbot_service.list_items(session_id=self._session_id())
        if not items:
            self._write_line("No saved items in this session yet.")
            return
        self._write_line("Saved items:")
        for item in items:
            self._write_line(f"- {item.id}: {item.title} [{item.item_type}]")

    def _render_debug(self) -> None:
        debug = self.clawbot_service.get_session_debug(session_id=self._session_id())
        self._write_line(
            "Debug: "
            f"messages={len(debug.messages)} items={len(debug.items)} "
            f"topics={len(debug.topics)} decisions={len(debug.recent_decisions)}"
        )
        if debug.recent_decisions:
            self._write_line("Recent decisions:")
            for decision in debug.recent_decisions[-3:]:
                self._write_line(
                    f"- action={decision.action} confidence={decision.confidence} source={decision.source}"
                )

    def _render_outcome(self, outcome: AssistantTurnOutcome) -> None:
        reply = outcome.reply.strip() or "(no reply)"
        self._write_line("")
        self._write_line(f"Assistant: {reply}")
        self._write_line(
            f"Status: {outcome.status} | Disposition: {outcome.disposition} | "
            f"Action: {outcome.action} | Tool: {outcome.tool_name}"
        )
        if outcome.item_id:
            self._write_line(f"Item: {outcome.item_id}")
        if self.trace_enabled and outcome.tool_trace:
            self._write_line("Tool trace:")
            for index, entry in enumerate(outcome.tool_trace, start=1):
                tool_name = str(entry.get("tool_name") or "tool")
                status = str(entry.get("status") or "completed")
                action = str(entry.get("action") or "chat")
                args = json.dumps(entry.get("arguments") or {}, ensure_ascii=False, sort_keys=True)
                self._write_line(f"  {index}. {tool_name} [{status}] action={action}")
                self._write_line(f"     args: {args}")
                metadata = entry.get("metadata") or {}
                details = self._metadata_details(metadata)
                if details:
                    self._write_line(f"     meta: {details}")
        self._write_line("")

    @staticmethod
    def _metadata_details(metadata: dict[str, Any]) -> str:
        parts: list[str] = []
        if "exit_code" in metadata:
            parts.append(f"exit_code={metadata['exit_code']}")
        if metadata.get("timed_out"):
            parts.append("timed_out=true")
        if metadata.get("item_id"):
            parts.append(f"item_id={metadata['item_id']}")
        if metadata.get("skill_name"):
            parts.append(f"skill={metadata['skill_name']}")
        return ", ".join(parts)

    def _write_line(self, text: str) -> None:
        self.output.write(f"{text}\n")
        self.output.flush()


def launch_tui(*, session_id: str | None = None, trace: bool = True) -> int:
    settings = CoreSettings()
    updates: dict[str, str] = {}
    if "CORA_TOOLSET_PRESET" not in os.environ:
        updates["toolset_preset"] = "cora-cli"
    if "CORA_HARNESS_POLICY_PROFILE" not in os.environ:
        updates["harness_policy_profile"] = "coding_full"
    if updates:
        settings = settings.model_copy(update=updates)
    container = build_clawbot_container(settings=settings)
    container.initialize()
    shell = InteractiveChatShell(
        clawbot_service=container.clawbot_service,
        session_id=session_id,
        trace_enabled=trace,
    )
    return shell.run()
