from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

ItemSavedHook = Callable[..., None]

_ITEM_SAVED_HOOKS: dict[str, ItemSavedHook] = {}


def register_item_saved_hook(name: str, hook: ItemSavedHook) -> None:
    normalized = str(name or "").strip()
    if not normalized:
        raise ValueError("item_saved hook name is required")
    _ITEM_SAVED_HOOKS[normalized] = hook


def fire_item_saved_hooks(*, item: Any, parsed: Any | None = None) -> None:
    for name, hook in list(_ITEM_SAVED_HOOKS.items()):
        try:
            hook(item=item, parsed=parsed)
        except Exception:
            logger.exception("item_saved hook failed hook=%s item_id=%s", name, getattr(item, "id", ""))
