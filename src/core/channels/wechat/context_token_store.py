from __future__ import annotations

import json
from pathlib import Path


class WechatContextTokenStore:
    """Disk-backed context token cache keyed by peer user id."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._cache: dict[str, str] = {}
        self._loaded = False

    def get(self, user_id: str) -> str | None:
        self._ensure_loaded()
        return self._cache.get(user_id)

    def set(self, user_id: str, token: str) -> None:
        if not user_id or not token:
            return
        self._ensure_loaded()
        self._cache[user_id] = token
        self._persist()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        for user_id, token in payload.items():
            if isinstance(user_id, str) and isinstance(token, str) and user_id and token:
                self._cache[user_id] = token

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

