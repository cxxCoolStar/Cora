from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.schemas.tool import ToolResult, ToolSpec


class Tool(ABC):
    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        raise NotImplementedError

    @abstractmethod
    def invoke(self, arguments: dict[str, Any]) -> ToolResult:
        raise NotImplementedError
