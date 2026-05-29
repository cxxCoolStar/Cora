from __future__ import annotations

from typing import Any


class EvalWechatIlinkStub:
    """Records outbound WeChat texts during harness runs (progress + final replies)."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, *, peer_user_id: str, text: str, context_token: str | None = None) -> dict[str, Any]:
        del peer_user_id, context_token
        self.sent.append(text)
        return {"ret": 0, "errcode": 0}

    def drain_sent(self) -> list[str]:
        messages = list(self.sent)
        self.sent.clear()
        return messages


__all__ = ["EvalWechatIlinkStub"]
