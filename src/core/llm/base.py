from __future__ import annotations

from abc import ABC, abstractmethod

from core.schemas.message import Message
from core.schemas.model import ModelResponse
from core.schemas.tool import ToolSpec


class ModelClient(ABC):
    @abstractmethod
    def generate(self, *, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        raise NotImplementedError
