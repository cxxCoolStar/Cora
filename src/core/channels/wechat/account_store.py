from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from time import gmtime, strftime


@dataclass(slots=True)
class WechatAccount:
    account_id: str
    token: str
    base_url: str
    user_id: str = ""


class WechatAccountStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def save(self, *, name: str, account: WechatAccount) -> Path:
        path = self.root_dir / f"{name}.json"
        payload = {
            "account_id": account.account_id,
            "token": account.token,
            "base_url": account.base_url,
            "user_id": account.user_id,
            "saved_at": strftime("%Y-%m-%dT%H:%M:%SZ", gmtime()),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    def load(self, *, name: str) -> WechatAccount | None:
        path = self.root_dir / f"{name}.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        account_id = str(payload.get("account_id") or "")
        token = str(payload.get("token") or "")
        if not account_id or not token:
            return None
        base_url = str(payload.get("base_url") or "https://ilinkai.weixin.qq.com")
        user_id = str(payload.get("user_id") or "")
        return WechatAccount(account_id=account_id, token=token, base_url=base_url, user_id=user_id)

