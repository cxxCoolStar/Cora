from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable


def ilink_send_success(result: Any) -> bool:
    """Treat empty or ret-less iLink payloads as success when no error code is present."""
    if result is None:
        return True
    if not isinstance(result, dict):
        return bool(result)
    ret = result.get("ret")
    errcode = result.get("errcode")
    if ret not in {None, 0}:
        return False
    if errcode not in {None, 0}:
        return False
    return True


def wechat_delivery_caption(title_or_name: str) -> str:
    raw = str(title_or_name or "").strip()
    lowered = raw.lower()
    stem = Path(raw).stem.lower()
    if stem == "wechat_image" or lowered.startswith("wechat_image."):
        return ""
    return raw


SendFileFn = Callable[..., Awaitable[Any] | Any]


class ChannelDelivery:
    def __init__(
        self,
        *,
        storage_dir: str | Path,
        can_send_files_to_user: Callable[[], bool],
        resolve_external_user_id: Callable[[str], str | None],
        send_file: SendFileFn,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self.can_send_files_to_user = can_send_files_to_user
        self.resolve_external_user_id = resolve_external_user_id
        self.send_file = send_file
        self._sent_for_message: set[str] = set()

    def resolve_deliverable_path(self, file_path: str) -> str:
        if not file_path:
            return ""
        candidate = Path(file_path)
        if candidate.is_file():
            return str(candidate)
        by_name = self.storage_dir / candidate.name
        if by_name.is_file():
            return str(by_name)
        wechat_inbox = self.storage_dir / "wechat_inbox" / candidate.name
        if wechat_inbox.is_file():
            return str(wechat_inbox)
        for match in self.storage_dir.rglob(candidate.name):
            if match.is_file():
                return str(match)
        return ""

    async def deliver_file(
        self,
        *,
        session_id: str,
        source_message_id: str,
        file_path: str,
        title: str | None = None,
    ) -> dict[str, str]:
        dedupe_key = f"{session_id}:{source_message_id}"
        if dedupe_key in self._sent_for_message:
            return {"reply": "已经发送，请查收。", "action": "retrieve", "status": "completed"}

        resolved_path = self.resolve_deliverable_path(file_path)
        if not resolved_path:
            return {
                "reply": "没有可发送的原始文件路径。",
                "action": "chat",
                "status": "failed",
            }
        display_title = str(title or Path(resolved_path).name)
        if not self.can_send_files_to_user():
            return {
                "reply": f"已定位到 `{display_title or '该资料'}` 的原始文件，但当前会话没有可用的发送通道。",
                "action": "chat",
                "status": "failed",
            }
        external_user_id = self.resolve_external_user_id(session_id)
        if not external_user_id:
            return {
                "reply": "当前会话没有可用的用户映射，暂时无法发送文件。",
                "action": "chat",
                "status": "failed",
            }
        caption = wechat_delivery_caption(display_title)
        try:
            result = self.send_file(
                user_id=external_user_id,
                file_path=resolved_path,
                caption=caption,
            )
            if hasattr(result, "__await__"):
                result = await result
        except Exception as exc:  # pragma: no cover - defensive
            return {"reply": str(exc), "action": "chat", "status": "failed"}
        if not ilink_send_success(result):
            return {"reply": str(result), "action": "chat", "status": "failed"}
        self._sent_for_message.add(dedupe_key)
        return {"reply": "已经发送，请查收。", "action": "retrieve", "status": "completed"}


__all__ = ["ChannelDelivery", "ilink_send_success", "wechat_delivery_caption"]
