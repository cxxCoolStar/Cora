from __future__ import annotations

_REGISTERED: set[str] = set()


def mark_registered(name: str) -> None:
    normalized = str(name or "").strip()
    if normalized:
        _REGISTERED.add(normalized)


def is_registered(name: str) -> bool:
    return str(name or "").strip() in _REGISTERED
