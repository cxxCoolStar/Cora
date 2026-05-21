from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Iterable

from fastapi import UploadFile

from core.agent.loop import LoopResult, ToolExecutionTrace
from core.schemas.tool import ToolCall


@dataclass(slots=True)
class ForcedToolSelection:
    tool_call: ToolCall
    category: str


@dataclass(slots=True)
class RetryDirective:
    category: str
    instruction: str


@dataclass(slots=True)
class TurnHeuristicDecision:
    forced_tool_selection: ForcedToolSelection | None = None
    retry: RetryDirective | None = None


@dataclass(slots=True)
class SkillIntentRoute:
    skill_name: str
    script_path: str
    intent: str
    required_fields: tuple[str, ...]
    phrases: tuple[str, ...]


@dataclass(slots=True)
class NativeToolRoute:
    tool_name: str
    category: str
    retry_instruction: str
    matcher: Callable[[str, str], bool]
    fallback_arguments_builder: Callable[[str], dict[str, Any] | None] | None = None


@dataclass(slots=True)
class ToolRoutingPolicy:
    tool_names_provider: Callable[[], set[str]] | None = None
    skills_provider: Callable[[], Iterable[Any]] | None = None
    media_kind_resolver: Callable[[UploadFile | None], str | None] | None = None

    @classmethod
    def from_runner(cls, runner: Any, *, tool_names: set[str] | None = None) -> ToolRoutingPolicy:
        def tool_names_provider() -> set[str]:
            if tool_names is not None:
                return set(tool_names)
            tool_specs = getattr(getattr(runner, "loop", None), "tool_specs", None)
            if not tool_specs:
                return set()
            return {
                str(spec.name).strip()
                for spec in tool_specs
                if str(getattr(spec, "name", "") or "").strip()
            }

        skill_loader = getattr(runner, "skill_loader", None)
        skills_provider = getattr(skill_loader, "list_skills", None)
        media_kind_resolver = getattr(runner, "media_kind_resolver", None)
        return cls(
            tool_names_provider=tool_names_provider,
            skills_provider=skills_provider if callable(skills_provider) else None,
            media_kind_resolver=media_kind_resolver if callable(media_kind_resolver) else None,
        )

    def forced_tool_selection(
        self,
        *,
        user_text: str,
        raw_text: str | None,
        upload: UploadFile | None,
        loop_result: LoopResult,
    ) -> ForcedToolSelection | None:
        if upload is not None:
            return None
        if loop_result.tool_trace or loop_result.exit_reason != "assistant_text":
            return None
        text = (raw_text or user_text or "").strip()
        if not text:
            return None
        lowered = text.lower()
        forced_file_selection = self._forced_file_tool_selection(text=text)
        if forced_file_selection is not None and self._tool_is_available(forced_file_selection.tool_call.tool_name):
            return forced_file_selection
        forced_web_selection = self._forced_web_tool_selection(text=text, lowered=lowered)
        if forced_web_selection is not None and self._tool_is_available(forced_web_selection.tool_call.tool_name):
            return forced_web_selection
        return None

    def fallback_tool_selection(
        self,
        *,
        retry_category: str,
        user_text: str,
        raw_text: str | None,
        upload: UploadFile | None,
        loop_result: LoopResult,
    ) -> ForcedToolSelection | None:
        if upload is not None:
            return None
        if loop_result.tool_trace or loop_result.exit_reason != "assistant_text":
            return None
        text = (raw_text or user_text or "").strip()
        if not text:
            return None
        skill_fallback = self._forced_skill_selection_for_intent(
            intent=retry_category,
            query=text,
            category=retry_category,
        )
        if skill_fallback is not None:
            return skill_fallback
        return self._forced_native_tool_selection_for_category(
            category=retry_category,
            text=text,
        )

    def tool_retry_category(
        self,
        *,
        user_text: str,
        raw_text: str | None,
        upload: UploadFile | None,
        loop_result: LoopResult,
    ) -> str | None:
        if loop_result.tool_trace or loop_result.exit_reason != "assistant_text":
            return None
        text = (raw_text or user_text or "").strip()
        if not text:
            return None
        lowered = text.lower()
        if self._matches_skill_intent_for_text(text=text, lowered=lowered, intent="deliver"):
            return "deliver"
        if self._matches_skill_intent_for_text(text=text, lowered=lowered, intent="delete"):
            return "delete"
        if self._looks_like_delivery_request(text=text, lowered=lowered):
            return "deliver"
        if self._looks_like_delete_request(text=text, lowered=lowered):
            return "delete"
        native_route = self._matching_native_tool_route(text=text, lowered=lowered)
        if native_route is not None:
            return native_route.category
        if upload is not None and self._resolve_media_kind(upload) == "image":
            return "save_file"
        return None

    def tool_retry_instruction(self, category: str) -> str:
        native_route = self._native_tool_route_for_category(category)
        if native_route is not None:
            return native_route.retry_instruction
        if category == "deliver":
            return (
                "Tool-use correction: the user is asking for a previously saved photo or file to be sent back over the current channel. "
                "Do not claim file delivery is unsupported. Inspect the relevant skill with skill_view if needed, then use the appropriate tool workflow. "
                "If the target is ambiguous, let the tool-backed workflow return a clarification instead of answering from chat."
            )
        if category == "delete":
            return (
                "Tool-use correction: the user is asking to delete saved content. "
                "Do not answer with plain chat. Inspect the relevant skill if needed and use the proper tool-backed delete workflow."
            )
        if category == "save_file":
            return (
                "Tool-use correction: the user uploaded a file or image that should be handled through a tool-backed workflow. "
                "Inspect the relevant skill if needed and use the proper save workflow."
            )
        return (
            "Tool-use correction: this request should be handled with tools instead of plain chat. "
            "Use the relevant skill workflow and tool support before answering."
        )

    def _tool_is_available(self, tool_name: str) -> bool:
        available = self.tool_names_provider() if self.tool_names_provider is not None else set()
        if not available:
            return True
        return str(tool_name or "").strip() in available

    def _native_tool_routes(self) -> list[NativeToolRoute]:
        return [
            NativeToolRoute(
                tool_name="user_memory",
                category="user_memory",
                retry_instruction=(
                    "Tool-use correction: the user is asking to remember, inspect, update, or forget durable personal information. "
                    "Use the user_memory tool instead of replying from chat."
                ),
                matcher=self._looks_like_user_memory_request,
            ),
            NativeToolRoute(
                tool_name="scheduled_tasks",
                category="scheduled_task",
                retry_instruction=(
                    "Tool-use correction: the user is asking for a reminder, alarm, follow-up, or scheduled task. "
                    "Do not answer from plain chat. Use the scheduled_tasks tool to create, inspect, update, or manage the reminder, "
                    "and keep the scheduled time from the tool-backed result."
                ),
                matcher=self._looks_like_scheduled_task_request,
            ),
            NativeToolRoute(
                tool_name="search_sessions",
                category="session_search",
                retry_instruction=(
                    "Tool-use correction: the user is asking about something from earlier conversations or previous sessions. "
                    "Do not guess from the current turn. Use the search_sessions tool to retrieve the relevant history first."
                ),
                matcher=self._looks_like_session_search_request,
                fallback_arguments_builder=self._search_sessions_fallback_arguments,
            ),
        ]

    def _matching_native_tool_route(
        self,
        *,
        text: str,
        lowered: str,
    ) -> NativeToolRoute | None:
        for route in self._native_tool_routes():
            if not self._tool_is_available(route.tool_name):
                continue
            if route.matcher(text=text, lowered=lowered):
                return route
        return None

    def _native_tool_route_for_category(self, category: str) -> NativeToolRoute | None:
        normalized_category = str(category or "").strip()
        if not normalized_category:
            return None
        for route in self._native_tool_routes():
            if route.category == normalized_category:
                return route
        return None

    def _forced_native_tool_selection_for_category(
        self,
        *,
        category: str,
        text: str,
    ) -> ForcedToolSelection | None:
        route = self._native_tool_route_for_category(category)
        if route is None:
            return None
        if not self._tool_is_available(route.tool_name):
            return None
        if route.fallback_arguments_builder is None:
            return None
        arguments = route.fallback_arguments_builder(text)
        if not arguments:
            return None
        return ForcedToolSelection(
            tool_call=ToolCall(
                tool_name=route.tool_name,
                arguments=arguments,
            ),
            category=route.category,
        )

    def _skill_intent_routes(self) -> list[SkillIntentRoute]:
        routes: list[SkillIntentRoute] = []
        if self.skills_provider is None:
            return routes
        for skill in self.skills_provider():
            runtime_metadata = getattr(skill, "runtime_metadata", None) or {}
            script_path = str(runtime_metadata.get("entrypoint") or "").strip()
            if not script_path:
                continue
            intent_phrases = runtime_metadata.get("intent_phrases") or {}
            if not isinstance(intent_phrases, dict):
                continue
            required_fields = tuple(
                str(field).strip()
                for field in (runtime_metadata.get("required_input_fields") or [])
                if str(field).strip()
            )
            for intent_name, candidates in intent_phrases.items():
                normalized_intent = str(intent_name or "").strip()
                if not normalized_intent or not isinstance(candidates, list):
                    continue
                phrases = tuple(
                    token.lower()
                    for candidate in candidates
                    if (token := str(candidate or "").strip())
                )
                if not phrases:
                    continue
                routes.append(
                    SkillIntentRoute(
                        skill_name=str(getattr(skill, "name", "") or "").strip(),
                        script_path=script_path,
                        intent=normalized_intent,
                        required_fields=required_fields,
                        phrases=phrases,
                    )
                )
        return routes

    def _find_skill_intent_route(self, *, intent: str) -> SkillIntentRoute | None:
        normalized_intent = str(intent or "").strip()
        if not normalized_intent:
            return None
        for route in self._skill_intent_routes():
            if route.intent == normalized_intent:
                return route
        return None

    def _matches_skill_intent_for_text(
        self,
        *,
        text: str,
        lowered: str,
        intent: str,
    ) -> bool:
        del text
        normalized_intent = str(intent or "").strip()
        if not normalized_intent:
            return False
        for route in self._skill_intent_routes():
            if route.intent != normalized_intent:
                continue
            if any(token and token in lowered for token in route.phrases):
                return True
        return False

    def _forced_skill_selection_for_intent(
        self,
        *,
        intent: str,
        query: str,
        category: str,
    ) -> ForcedToolSelection | None:
        if not self._tool_is_available("skill_run"):
            return None
        normalized_intent = str(intent or "").strip()
        if not normalized_intent:
            return None
        route = self._find_skill_intent_route(intent=normalized_intent)
        if route is None:
            return None
        input_payload: dict[str, Any] = {"query": query}
        if "intent" in route.required_fields:
            input_payload["intent"] = normalized_intent
        return ForcedToolSelection(
            tool_call=ToolCall(
                tool_name="skill_run",
                arguments={
                    "name": route.skill_name,
                    "script_path": route.script_path,
                    "input": input_payload,
                },
            ),
            category=category,
        )

    def _resolve_media_kind(self, upload: UploadFile | None) -> str | None:
        if self.media_kind_resolver is None:
            return None
        return self.media_kind_resolver(upload)

    @staticmethod
    def _looks_like_delivery_request(*, text: str, lowered: str) -> bool:
        delivery_verbs = ("send me", "send back", "deliver", "forward")
        file_nouns = ("photo", "image", "picture", "file", "attachment", "pdf", "jpg", "jpeg", "png")
        return any(token in lowered for token in delivery_verbs) and any(token in lowered for token in file_nouns)

    @staticmethod
    def _looks_like_delete_request(*, text: str, lowered: str) -> bool:
        delete_verbs = ("remove", "delete", "erase", "clear")
        target_nouns = ("item", "file", "photo", "image", "attachment", "record", "saved")
        return any(token in lowered for token in delete_verbs) and any(token in lowered for token in target_nouns)

    @staticmethod
    def _looks_like_user_memory_request(*, text: str, lowered: str) -> bool:
        memory_markers = (
            "remember this",
            "remember that",
            "forget this",
            "forget that",
            "show my memory",
            "user memory",
            "update my memory",
        )
        return any(token in lowered for token in memory_markers)

    @staticmethod
    def _looks_like_scheduled_task_request(*, text: str, lowered: str) -> bool:
        reminder_markers = (
            "remind me",
            "set a reminder",
            "set reminder",
            "set an alarm",
            "alarm me",
            "ping me",
        )
        cn_reminder_markers = (
            "\u63d0\u9192",
            "\u95f9\u949f",
            "\u5b9a\u65f6",
            "\u901a\u77e5\u6211",
        )
        time_markers = (
            " later",
            " tomorrow",
            " tonight",
            " next ",
            " every ",
            " daily",
            " weekly",
            " monthly",
            " at ",
            " in ",
            "am",
            "pm",
        )
        cn_time_markers = (
            "\u540e",
            "\u4e4b\u540e",
            "\u660e\u5929",
            "\u4eca\u665a",
            "\u4e0b\u5468",
            "\u4e0b\u4e2a\u6708",
            "\u6bcf\u5929",
            "\u6bcf\u5468",
            "\u6bcf\u6708",
            "\u5206\u949f",
            "\u5c0f\u65f6",
            "\u5929",
            "\u70b9",
        )
        english_reminder = any(token in lowered for token in reminder_markers)
        chinese_reminder = any(token in text for token in cn_reminder_markers)
        if not english_reminder and not chinese_reminder:
            return False
        if any(token in lowered for token in time_markers):
            return True
        if any(token in text for token in cn_time_markers):
            return True
        if re.search(r"\b\d+\s*(?:m|min|mins|minute|minutes|h|hr|hour|hours|day|days)\b", lowered):
            return True
        return bool(
            re.search(
                r"(?:\d+|[一二两三四五六七八九十百半几])\s*(?:秒|分钟|小时|天)(?:后)?",
                text,
            )
        )

    @staticmethod
    def _looks_like_session_search_request(*, text: str, lowered: str) -> bool:
        strong_explicit_markers = (
            "what did i tell you",
            "what did i say",
            "did i mention",
            "have we talked about",
            "search our chat",
            "search my chat",
            "search our conversation",
        )
        broad_history_markers = (
            "chat history",
            "conversation history",
            "earlier conversation",
            "previous conversation",
            "earlier chat",
            "previous chat",
            "earlier session",
            "previous session",
            "prior session",
            "from our earlier conversation",
            "from earlier conversations",
        )
        temporal_markers = ("earlier", "before", "last time", "previous", "prior", "history", "historical")
        conversation_markers = (
            "conversation",
            "chat",
            "session",
            "message",
            "messages",
            "talked",
            "discussed",
            "mentioned",
            "said",
            "told you",
        )
        request_markers = (
            "search",
            "find",
            "look up",
            "look for",
            "show me",
            "tell me",
            "can you",
            "could you",
            "would you",
            "please",
            "help me",
        )
        has_temporal_marker = any(token in lowered for token in temporal_markers)
        has_conversation_marker = any(token in lowered for token in conversation_markers)
        has_request_marker = any(token in lowered for token in request_markers) or "?" in text
        if any(token in lowered for token in strong_explicit_markers):
            return True
        if any(token in lowered for token in broad_history_markers):
            return has_request_marker
        return has_temporal_marker and has_conversation_marker and has_request_marker

    @classmethod
    def _search_sessions_fallback_arguments(cls, text: str) -> dict[str, Any] | None:
        query = cls._refined_session_search_query(text)
        if not query:
            return None
        return {"query": query}

    @classmethod
    def _refined_session_search_query(cls, text: str) -> str:
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return ""
        cleaned = re.sub(r"(?i)^(?:can|could|would)\s+you\s+(?:please\s+)?", "", cleaned).strip()
        cleaned = re.sub(r"(?i)^please\s+", "", cleaned).strip()

        quoted_segments = [
            segment.strip()
            for segment in re.findall(r"[`\"']([^`\"']{2,120})[`\"']", cleaned)
            if segment.strip()
        ]
        if quoted_segments:
            return " ".join(quoted_segments)

        capture_patterns = (
            r"^\s*what\s+did\s+i\s+(?:tell\s+you|say)(?:\s+(?:before|earlier|last\s+time))?(?:\s+about)?\s+(?P<query>.+)$",
            r"^\s*(?:did\s+i\s+mention|have\s+we\s+(?:talked|chatted|spoken|discussed))(?:\s+anything)?(?:\s+about)?\s+(?P<query>.+)$",
            r"^\s*(?:search|find|look(?:\s+up|\s+for)?|show)\s+(?:(?:our|my)\s+)?(?:chat|conversation|history|messages|session)(?:\s+history)?(?:\s+for|\s+about)?\s+(?P<query>.+)$",
        )
        for pattern in capture_patterns:
            match = re.match(pattern, cleaned, flags=re.IGNORECASE)
            if match:
                candidate = cls._cleanup_session_search_candidate(match.group("query"))
                if candidate:
                    keyword_query = cls._keyword_focused_session_search_query(candidate)
                    return keyword_query or candidate

        candidate = cls._cleanup_session_search_candidate(cleaned)
        keyword_query = cls._keyword_focused_session_search_query(candidate)
        return keyword_query or candidate or cleaned

    @staticmethod
    def _cleanup_session_search_candidate(text: str) -> str:
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return ""
        replacements = (
            (r"(?i)^(?:about|regarding|re|on)\s+", ""),
            (r"(?i)^(?:the|a|an)\s+(?=\S+\s+\S+)", ""),
            (r"(?i)\s+(?:before|earlier|previously|last\s+time)\s*$", ""),
            (r"(?i)\s+(?:from|in)\s+(?:our|my)\s+(?:earlier|previous|prior)\s+(?:conversation|chat|session|history)s?\s*$", ""),
        )
        for pattern, replacement in replacements:
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE).strip()
        cleaned = cleaned.strip(" \t\r\n`\"'.,!?;:()[]{}")
        return " ".join(cleaned.split())

    @classmethod
    def _keyword_focused_session_search_query(cls, text: str) -> str:
        cleaned = cls._cleanup_session_search_candidate(text)
        if not cleaned:
            return ""
        if not re.search(r"[A-Za-z0-9]", cleaned):
            return cleaned

        raw_tokens = re.findall(r"[A-Za-z0-9]+(?:[-.][A-Za-z0-9]+)*", cleaned)
        if not raw_tokens:
            return cleaned
        stopwords = {
            "a",
            "about",
            "an",
            "and",
            "any",
            "anything",
            "are",
            "around",
            "before",
            "can",
            "chat",
            "check",
            "conversation",
            "could",
            "details",
            "did",
            "discuss",
            "discussed",
            "earlier",
            "find",
            "for",
            "from",
            "have",
            "history",
            "i",
            "if",
            "in",
            "look",
            "mentioned",
            "message",
            "messages",
            "my",
            "of",
            "our",
            "please",
            "previous",
            "prior",
            "pull",
            "remind",
            "said",
            "search",
            "session",
            "show",
            "something",
            "talk",
            "talked",
            "tell",
            "the",
            "up",
            "we",
            "what",
            "whatever",
            "you",
        }
        filtered_tokens: list[str] = []
        for token in raw_tokens:
            lowered_token = token.lower()
            if lowered_token in stopwords:
                continue
            if len(lowered_token) == 1 and not token.isdigit() and token.upper() != token:
                continue
            filtered_tokens.append(token)

        if not filtered_tokens:
            return cleaned
        deduped_tokens: list[str] = []
        seen_tokens: set[str] = set()
        for token in filtered_tokens:
            key = token.lower()
            if key in seen_tokens:
                continue
            seen_tokens.add(key)
            deduped_tokens.append(token)

        keyword_query = " ".join(deduped_tokens[:8]).strip()
        if not keyword_query:
            return cleaned
        if len(deduped_tokens) < len(raw_tokens) or len(deduped_tokens) <= 6:
            return keyword_query
        return cleaned

    @classmethod
    def _forced_file_tool_selection(cls, *, text: str) -> ForcedToolSelection | None:
        search_match = re.match(
            r"(?is)^\s*(?:please\s+)?(?:find|search(?:\s+for)?|look\s+for|locate|grep)\s+`(?P<query>[^`]+)`(?:\s+(?:in|under|within)\s+`(?P<path>[^`]+)`)?\s*[.?!]?\s*$",
            text,
        )
        if search_match:
            query = search_match.group("query").strip()
            path = (search_match.group("path") or ".").strip() or "."
            if query:
                return ForcedToolSelection(
                    tool_call=ToolCall(
                        tool_name="search_files",
                        arguments={"query": query, "path": path},
                    ),
                    category="file_search",
                )

        read_match = re.match(
            r"(?is)^\s*(?:please\s+)?(?:read|show|open|cat|display)\s+(?:the\s+file\s+)?`(?P<path>[^`]+)`\s*[.?!]?\s*$",
            text,
        )
        if read_match:
            path = read_match.group("path").strip()
            if path:
                return ForcedToolSelection(
                    tool_call=ToolCall(
                        tool_name="read_file",
                        arguments={"path": path},
                    ),
                    category="file_read",
                )

        write_match = re.match(
            r"(?is)^\s*(?:please\s+)?(?P<mode>write|append)\s+`(?P<content>[^`]+)`\s+(?:to|into|in)\s+`(?P<path>[^`]+)`\s*[.?!]?\s*$",
            text,
        )
        if write_match:
            path = write_match.group("path").strip()
            content = cls._normalized_forced_write_content(write_match.group("content"))
            append_mode = write_match.group("mode").strip().lower() == "append"
            if path and content:
                arguments = {"path": path, "content": content}
                if append_mode:
                    arguments["append"] = True
                return ForcedToolSelection(
                    tool_call=ToolCall(
                        tool_name="write_file",
                        arguments=arguments,
                    ),
                    category="file_write",
                )
        return None

    @staticmethod
    def _normalized_forced_write_content(content: str) -> str:
        normalized = content.replace("\r\n", "\n")
        if not normalized.endswith("\n"):
            normalized += "\n"
        return normalized

    @classmethod
    def _forced_web_tool_selection(cls, *, text: str, lowered: str) -> ForcedToolSelection | None:
        url_match = re.search(r"https?://[^\s`<>\"')\]]+", text, flags=re.IGNORECASE)
        if url_match:
            url = url_match.group(0).rstrip(".,!?;:")
            return ForcedToolSelection(
                tool_call=ToolCall(
                    tool_name="web_fetch",
                    arguments={"url": url},
                ),
                category="web_fetch",
            )
        if not cls._looks_like_current_info_request(text=text, lowered=lowered):
            return None
        query = cls._normalized_web_search_query(text)
        if not query:
            return None
        return ForcedToolSelection(
            tool_call=ToolCall(
                tool_name="web_search",
                arguments={"query": query},
            ),
            category="web_search",
        )

    @staticmethod
    def _looks_like_current_info_request(*, text: str, lowered: str) -> bool:
        local_file_markers = (
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".md",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".txt",
            "src/",
            "tests/",
            "docs/",
            "scripts/",
            "core/",
        )
        if any(marker in lowered for marker in local_file_markers):
            return False
        current_markers = ("latest", "recent", "current", "today", "news", "what's new")
        search_markers = ("search", "look up", "find out", "check")
        source_markers = ("source", "sources", "link", "links", "url", "urls")
        has_current = any(marker in lowered for marker in current_markers)
        has_search = any(marker in lowered for marker in search_markers)
        has_sources = any(marker in lowered for marker in source_markers)
        return has_current and (has_search or has_sources)

    @staticmethod
    def _normalized_web_search_query(text: str) -> str:
        normalized = " ".join(str(text or "").split())
        normalized = re.sub(r"(?is)^(please|could you|can you|would you|help me)\s+", "", normalized)
        return normalized.strip()


class ToolReplyPolicy:
    @staticmethod
    def select_final_agent_reply(*, last_execution: ToolExecutionTrace, assistant_text: str | None) -> str:
        final_reply = (assistant_text or "").strip()
        if not final_reply:
            natural_reply = ToolReplyPolicy._natural_tool_reply(last_execution=last_execution)
            if natural_reply:
                return natural_reply
            return last_execution.content
        if ToolReplyPolicy._should_prefer_tool_reply(last_execution=last_execution):
            return last_execution.content
        tool_name = last_execution.tool_name
        tool_arguments = last_execution.arguments
        if tool_name == "skill_run":
            skill_input = tool_arguments.get("input") or {}
            if str(skill_input.get("intent") or "").strip() == "deliver" and last_execution.action != "retrieve":
                return last_execution.content
        return final_reply

    @staticmethod
    def _should_prefer_tool_reply(*, last_execution: ToolExecutionTrace) -> bool:
        content = (last_execution.content or "").strip()
        if last_execution.tool_name == "scheduled_tasks":
            return bool(content)
        if not content:
            return False
        if last_execution.disposition == "clarify":
            return True
        if last_execution.status == "failed":
            return True
        if last_execution.tool_name == "skill_run" and last_execution.action == "retrieve" and not last_execution.artifacts:
            return True
        return False

    @classmethod
    def _natural_tool_reply(cls, *, last_execution: ToolExecutionTrace) -> str | None:
        if last_execution.tool_name != "search_sessions":
            return None
        if last_execution.disposition == "clarify" or last_execution.status == "failed":
            return None
        metadata = dict(last_execution.metadata or {})
        hits = metadata.get("hits") if isinstance(metadata.get("hits"), list) else []
        query = ToolRoutingPolicy._cleanup_session_search_candidate(str(metadata.get("query") or ""))
        if not hits:
            if query:
                return (
                    "I looked through your current and earlier conversation history but "
                    f"couldn't find anything relevant about {query}."
                )
            return "I looked through your current and earlier conversation history but couldn't find a relevant match."

        primary_hit = hits[0] if isinstance(hits[0], dict) else {}
        snippet = cls._session_search_hit_snippet(primary_hit)
        if not snippet:
            return None
        hit_count = len(hits)
        if hit_count > 1:
            return f"I found a few relevant earlier matches. The clearest one says: {snippet}"
        source = str(primary_hit.get("source") or "").strip().lower()
        role = str(primary_hit.get("role") or "").strip().lower()
        if source == "message" and role == "user":
            return f"You mentioned earlier: {snippet}"
        if source == "message" and role == "assistant":
            return f"In an earlier reply, I said: {snippet}"
        return f"I found an earlier note: {snippet}"

    @staticmethod
    def _session_search_hit_snippet(hit: dict[str, Any]) -> str:
        excerpt = " ".join(str(hit.get("excerpt") or "").split())
        if not excerpt:
            return ""
        if str(hit.get("source") or "").strip().lower() == "summary":
            first_segment = excerpt.split(" | ", 1)[0].strip()
            excerpt = re.sub(r"^[A-Za-z][A-Za-z ]{1,40}:\s*", "", first_segment).strip() or first_segment
        excerpt = excerpt.strip("` ")
        if excerpt and excerpt[-1].isalnum():
            excerpt += "."
        return excerpt


@dataclass(slots=True)
class TurnDecisionPolicy:
    routing_policy: ToolRoutingPolicy
    reply_policy: type[ToolReplyPolicy] = ToolReplyPolicy

    @classmethod
    def from_runner(cls, runner: Any, *, tool_names: set[str] | None = None) -> TurnDecisionPolicy:
        return cls(
            routing_policy=ToolRoutingPolicy.from_runner(runner, tool_names=tool_names),
        )

    def initial_decision(
        self,
        *,
        user_text: str,
        raw_text: str | None,
        upload: UploadFile | None,
        loop_result: LoopResult,
    ) -> TurnHeuristicDecision:
        forced_tool_selection = self.routing_policy.forced_tool_selection(
            user_text=user_text,
            raw_text=raw_text,
            upload=upload,
            loop_result=loop_result,
        )
        if forced_tool_selection is not None:
            return TurnHeuristicDecision(forced_tool_selection=forced_tool_selection)

        retry_category = self.routing_policy.tool_retry_category(
            user_text=user_text,
            raw_text=raw_text,
            upload=upload,
            loop_result=loop_result,
        )
        if retry_category is None:
            return TurnHeuristicDecision()
        return TurnHeuristicDecision(
            retry=RetryDirective(
                category=retry_category,
                instruction=self.routing_policy.tool_retry_instruction(retry_category),
            )
        )

    def fallback_tool_selection(
        self,
        *,
        retry_category: str,
        user_text: str,
        raw_text: str | None,
        upload: UploadFile | None,
        loop_result: LoopResult,
    ) -> ForcedToolSelection | None:
        return self.routing_policy.fallback_tool_selection(
            retry_category=retry_category,
            user_text=user_text,
            raw_text=raw_text,
            upload=upload,
            loop_result=loop_result,
        )

    def select_final_agent_reply(
        self,
        *,
        last_execution: ToolExecutionTrace,
        assistant_text: str | None,
    ) -> str:
        return self.reply_policy.select_final_agent_reply(
            last_execution=last_execution,
            assistant_text=assistant_text,
        )


__all__ = [
    "ForcedToolSelection",
    "NativeToolRoute",
    "RetryDirective",
    "SkillIntentRoute",
    "TurnDecisionPolicy",
    "TurnHeuristicDecision",
    "ToolReplyPolicy",
    "ToolRoutingPolicy",
]
