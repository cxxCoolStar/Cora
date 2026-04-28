from __future__ import annotations

from enum import StrEnum


class Channel(StrEnum):
    CHAT = "chat"
    TOOL = "tool"
    SYSTEM = "system"
    MEMORY = "memory"
    EVENT = "event"


class EventType(StrEnum):
    TURN_STARTED = "turn_started"
    MODEL_CALLED = "model_called"
    TOOL_REQUESTED = "tool_requested"
    TOOL_COMPLETED = "tool_completed"
    TURN_COMPLETED = "turn_completed"
