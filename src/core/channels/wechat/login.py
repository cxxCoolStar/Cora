from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from core.channels.wechat.account_store import WechatAccount


ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"


@dataclass(slots=True)
class WechatLoginResult:
    account: WechatAccount


class WechatQrLoginClient:
    def __init__(self, *, base_url: str = ILINK_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=40.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def login(self, *, bot_type: str = "3", timeout_seconds: int = 480) -> WechatLoginResult:
        qr_resp = await self._get_json(f"ilink/bot/get_bot_qrcode?bot_type={bot_type}")
        qrcode_value = str(qr_resp.get("qrcode") or "")
        qrcode_url = str(qr_resp.get("qrcode_img_content") or "")
        if not qrcode_value:
            raise RuntimeError("QR response missing qrcode")

        print("\n请使用微信扫描以下二维码：")
        if qrcode_url:
            print(qrcode_url)
        else:
            print(qrcode_value)

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        current_base_url = self.base_url
        refresh_count = 0

        while asyncio.get_running_loop().time() < deadline:
            status_resp = await self._get_json(
                f"ilink/bot/get_qrcode_status?qrcode={qrcode_value}",
                base_url=current_base_url,
            )
            status = str(status_resp.get("status") or "wait")
            if status == "wait":
                print(".", end="", flush=True)
            elif status == "scaned":
                print("\n已扫码，请在微信里确认...")
            elif status == "scaned_but_redirect":
                redirect_host = str(status_resp.get("redirect_host") or "")
                if redirect_host:
                    current_base_url = f"https://{redirect_host}"
            elif status == "expired":
                refresh_count += 1
                if refresh_count > 3:
                    raise RuntimeError("QR code expired too many times")
                print(f"\n二维码已过期，正在刷新... ({refresh_count}/3)")
                qr_resp = await self._get_json(f"ilink/bot/get_bot_qrcode?bot_type={bot_type}")
                qrcode_value = str(qr_resp.get("qrcode") or "")
                qrcode_url = str(qr_resp.get("qrcode_img_content") or "")
                if qrcode_url:
                    print(qrcode_url)
            elif status == "confirmed":
                account_id = str(status_resp.get("ilink_bot_id") or "")
                token = str(status_resp.get("bot_token") or "")
                base_url = str(status_resp.get("baseurl") or self.base_url)
                user_id = str(status_resp.get("ilink_user_id") or "")
                if not account_id or not token:
                    raise RuntimeError("QR confirmed but account/token missing")
                print(f"\n微信连接成功，account_id={account_id}")
                return WechatLoginResult(
                    account=WechatAccount(account_id=account_id, token=token, base_url=base_url, user_id=user_id)
                )
            await asyncio.sleep(1)
        raise RuntimeError("wechat qr login timeout")

    async def _get_json(self, endpoint: str, *, base_url: str | None = None) -> dict[str, Any]:
        target_base = (base_url or self.base_url).rstrip("/")
        url = f"{target_base}/{endpoint}"
        headers = {"iLink-App-Id": "bot", "iLink-App-ClientVersion": str((2 << 16) | (2 << 8) | 0)}
        resp = await self._http.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("errcode") not in (None, 0):
            raise RuntimeError(f"iLink error {data.get('errcode')}: {data.get('errmsg')}")
        return data if isinstance(data, dict) else {}

