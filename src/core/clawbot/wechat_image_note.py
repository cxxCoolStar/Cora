from __future__ import annotations

from typing import Any, Literal

from fastapi import UploadFile

WECHAT_IMAGE_NOTE_QUESTION = (
    "收到图片 📷 请用一句话说明（例如场合、地点、人物），方便以后查找。"
    "不需要说明就回复「直接保存」。"
)

WECHAT_IMAGE_BATCH_NOTE = (
    "又收到 1 张图片，已加入待保存列表。"
    "请补充说明，或回复「直接保存」。"
)

WECHAT_IMAGE_SAVED_REPLY = "照片已保存 ✅"
WECHAT_IMAGE_SAVED_WITHOUT_NOTE_REPLY = "照片已保存 ✅（未加说明，以后可能不好检索）。"
WECHAT_IMAGE_SAVED_WITH_TOPIC_TEMPLATE = "照片已保存 ✅ 已归档到「{topic}」话题，方便日后查找。"

PENDING_UPLOAD_SAVE = "upload_save"

_DIRECT_SAVE_PHRASES = frozenset(
    {
        "直接保存",
        "直接存",
        "跳过",
        "跳过说明",
        "不用说明",
        "不用写说明",
        "不用描述",
    }
)
_CANCEL_PHRASES = frozenset({"取消", "不用", "算了", "不要了", "别保存", "不保存"})


def is_wechat_metadata(metadata: dict[str, Any] | None) -> bool:
    meta = dict(metadata or {})
    channel = str(meta.get("channel") or meta.get("platform") or "").strip().lower()
    return channel == "wechat"


def is_image_only_turn(*, text: str | None, upload: UploadFile | None) -> bool:
    return bool(upload and (upload.filename or "").strip()) and not str(text or "").strip()


def is_image_with_note_turn(
    *,
    text: str | None,
    upload: UploadFile | None,
    media_kind: str | None,
) -> bool:
    return bool(str(text or "").strip() and upload and media_kind == "image")


def normalize_image_note_text(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    while cleaned.startswith("="):
        cleaned = cleaned[1:].lstrip()
    return cleaned.strip()


def classify_image_note_follow_up(text: str) -> tuple[Literal["cancel", "direct_save", "note"], str | None]:
    cleaned = normalize_image_note_text(text)
    if not cleaned:
        return "note", None
    compact = cleaned.strip("。！!？? ")
    if compact in _CANCEL_PHRASES or any(
        compact == phrase or compact.startswith(f"{phrase}了") for phrase in _CANCEL_PHRASES
    ):
        if len(compact) <= 12:
            return "cancel", None
    if compact in _DIRECT_SAVE_PHRASES or any(
        compact == phrase or compact.startswith(phrase) for phrase in _DIRECT_SAVE_PHRASES
    ):
        if len(compact) <= 16:
            return "direct_save", None
    return "note", cleaned


def build_upload_save_pending_payload(
    *,
    entry: dict[str, str | None],
    media_kind: str = "image",
) -> dict[str, Any]:
    upload_path = str(entry.get("upload_path") or "").strip()
    upload_filename = str(entry.get("upload_filename") or "").strip() or "upload.bin"
    source_event_id = str(entry.get("source_event_id") or "").strip() or None
    normalized_entry = {
        "upload_path": upload_path,
        "upload_filename": upload_filename,
        "source_event_id": source_event_id,
    }
    return {
        "type": PENDING_UPLOAD_SAVE,
        "pending_input_type": "upload",
        "media_kind": media_kind,
        "upload_path": upload_path,
        "upload_filename": upload_filename,
        "source_event_id": source_event_id,
        "upload_entries": [normalized_entry],
    }


def topic_name_from_artifacts(artifacts: list[Any] | None) -> str | None:
    for artifact in list(artifacts or []):
        if not isinstance(artifact, dict) or artifact.get("kind") != "item":
            continue
        payload = artifact.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        topic_name = str(payload.get("topic_name") or "").strip()
        if topic_name:
            return topic_name
    return None


def capture_reply_for_wechat(*, had_user_note: bool, topic_name: str | None = None) -> str:
    if topic_name:
        return WECHAT_IMAGE_SAVED_WITH_TOPIC_TEMPLATE.format(topic=topic_name.strip())
    if had_user_note:
        return WECHAT_IMAGE_SAVED_REPLY
    return WECHAT_IMAGE_SAVED_WITHOUT_NOTE_REPLY
