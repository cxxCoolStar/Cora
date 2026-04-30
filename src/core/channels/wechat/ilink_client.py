from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import logging
import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile
import base64
import hashlib
import mimetypes
import secrets
import struct
from urllib.parse import quote

import httpx

from core.channels.wechat.context_token_store import WechatContextTokenStore
from core.channels.wechat.types import WechatInboundEvent

# Item type constants
ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_VOICE = 3
ITEM_FILE = 4
ITEM_VIDEO = 5

# Media type constants for upload
MEDIA_IMAGE = 1
MEDIA_VIDEO = 2
MEDIA_FILE = 3
MEDIA_VOICE = 4

# API Endpoints
EP_GET_UPLOAD_URL = "ilink/bot/getuploadurl"

# AES-128-ECB utilities for file encryption
try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    _CRYPTO_AVAILABLE = True
except Exception:  # pragma: no cover
    default_backend = None  # type: ignore[misc,assignment]
    Cipher = None  # type: ignore[misc,assignment]
    algorithms = None  # type: ignore[misc,assignment]
    modes = None  # type: ignore[misc,assignment]
    _CRYPTO_AVAILABLE = False


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    """Pad data using PKCS#7 padding."""
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _aes128_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt data using AES-128-ECB."""
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography library required for AES encryption")
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(_pkcs7_pad(plaintext)) + encryptor.finalize()


def _aes_padded_size(size: int) -> int:
    """Calculate the AES padded size for a given plaintext size."""
    return ((size + 1 + 15) // 16) * 16

logger = logging.getLogger(__name__)

WEIXIN_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"

# --- AES-128-ECB utilities for WeChat media decryption ---
try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    _CRYPTO_AVAILABLE = True
except Exception:  # pragma: no cover
    default_backend = None  # type: ignore[misc,assignment]
    Cipher = None  # type: ignore[misc,assignment]
    algorithms = None  # type: ignore[misc,assignment]
    modes = None  # type: ignore[misc,assignment]
    _CRYPTO_AVAILABLE = False


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _aes128_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography library required for AES decryption")
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    if not padded:
        return padded
    pad_len = padded[-1]
    if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
        return padded[:-pad_len]
    return padded


def _parse_aes_key(aes_key_b64: str) -> bytes:
    decoded = base64.b64decode(aes_key_b64)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        text = decoded.decode("ascii", errors="ignore")
        if text and all(ch in "0123456789abcdefABCDEF" for ch in text):
            return bytes.fromhex(text)
    raise ValueError(f"unexpected aes_key format ({len(decoded)} decoded bytes)")

@dataclass(slots=True)
class WechatIlinkConfig:
    token: str
    base_url: str = "https://ilinkai.weixin.qq.com"
    app_id: str = "bot"
    poll_timeout_seconds: int = 35
    context_tokens_path: Path | None = None
    download_dir: Path | None = None
    cdn_base_url: str = WEIXIN_CDN_BASE_URL


class WechatIlinkClient:
    """Minimal iLink API client inspired by Hermes Weixin adapter."""

    def __init__(self, config: WechatIlinkConfig) -> None:
        self.config = config
        self._http = httpx.AsyncClient(timeout=40.0)
        self._sync_buf = ""
        self._token_store = (
            WechatContextTokenStore(config.context_tokens_path)
            if config.context_tokens_path is not None
            else None
        )
        if config.download_dir is not None:
            config.download_dir.mkdir(parents=True, exist_ok=True)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_updates(self) -> list[WechatInboundEvent]:
        payload = {
            "base_info": {"channel_version": "2.2.0"},
            "get_updates_buf": self._sync_buf,
            "timeout": self.config.poll_timeout_seconds * 1000,
        }
        data = await self._post_json("ilink/bot/getupdates", payload)
        new_sync_buf = str(data.get("get_updates_buf") or "")
        if new_sync_buf:
            self._sync_buf = new_sync_buf
        updates = data.get("msgs") or data.get("updates") or []
        events: list[WechatInboundEvent] = []
        for update in updates:
            event = await self._parse_update(update)
            if event is not None:
                if self._token_store is not None and event.context_token:
                    self._token_store.set(event.user_id, event.context_token)
                events.append(event)
            else:
                logger.info("wechat skipped non-text update keys=%s", list(update.keys())[:10] if isinstance(update, dict) else type(update))
        if not updates:
            logger.debug("wechat getupdates returned no messages")
        return events

    async def send_text(self, *, peer_user_id: str, text: str, context_token: str | None = None) -> dict[str, Any]:
        resolved_context_token = context_token
        if not resolved_context_token and self._token_store is not None:
            resolved_context_token = self._token_store.get(peer_user_id)
        message: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": peer_user_id,
            "client_id": str(uuid.uuid4()),
            "message_type": 2,
            "message_state": 2,
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        }
        if resolved_context_token:
            message["context_token"] = resolved_context_token
        payload = {
            "base_info": {"channel_version": "2.2.0"},
            "msg": message,
        }
        return await self._post_json("ilink/bot/sendmessage", payload)

    async def _post_json(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.config.base_url.rstrip('/')}/{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self.config.token}",
            "iLink-App-Id": self.config.app_id,
        }
        resp = await self._http.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            if data.get("errcode") not in (None, 0):
                raise RuntimeError(f"iLink error {data.get('errcode')}: {data.get('errmsg')}")
            if data.get("ret") not in (None, 0):
                raise RuntimeError(f"iLink ret {data.get('ret')}: {data.get('errmsg')}")
        return data if isinstance(data, dict) else {}

    async def _parse_update(self, update: dict[str, Any]) -> WechatInboundEvent | None:
        event_id = str(update.get("message_id") or update.get("update_id") or update.get("msg_id") or "")
        if not event_id:
            return None
        context_token = update.get("context_token")
        from_user = str(update.get("from_user_id") or update.get("user_id") or "")
        if not from_user:
            return None

        text = ""
        item_list = update.get("item_list") or []
        if isinstance(item_list, list):
            for item in item_list:
                if not isinstance(item, dict):
                    continue
                if int(item.get("type") or 0) == 1:
                    text_item = item.get("text_item") or {}
                    text = str(text_item.get("text") or "").strip()
                    if text:
                        break
        msg = update.get("msg")
        if not text and isinstance(msg, dict):
            text = str(msg.get("text") or "").strip()
        if not text:
            text = str(update.get("text") or "").strip()
        file_name: str | None = None
        file_path: str | None = None
        file_mime: str | None = None
        if isinstance(item_list, list):
            for item in item_list:
                if not isinstance(item, dict):
                    continue
                item_type = int(item.get("type") or 0)
                # Handle image (type=2)
                if item_type == ITEM_IMAGE:
                    image_path = await self._download_image(item)
                    if image_path:
                        file_path = image_path
                        file_name = "wechat_image.jpg"
                        file_mime = "image/jpeg"
                        break
                # Handle file (type=4)
                elif item_type == ITEM_FILE:
                    file_item = item.get("file_item") or {}
                    file_name = str(file_item.get("file_name") or "").strip() or "wechat_upload.bin"
                    downloaded = await self._download_file(item)
                    if downloaded is not None:
                        file_path = downloaded
                        file_mime = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
                    break

        if not text.strip() and not file_path:
            return None

        return WechatInboundEvent(
            event_id=event_id,
            user_id=from_user,
            text=text.strip() or None,
            context_token=str(context_token) if context_token else None,
            file_name=file_name,
            file_path=file_path,
            file_mime=file_mime,
            raw_payload=update,
        )

    async def _download_file(self, item: dict[str, Any]) -> str | None:
        """Download WeChat file, handling AES decryption if needed."""
        file_item = item.get("file_item") or {}
        file_name = str(file_item.get("file_name") or "").strip() or "wechat_upload.bin"
        media = file_item.get("media") or {}

        full_url = str(media.get("full_url") or "").strip()
        encrypt_query_param = str(media.get("encrypt_query_param") or "").strip()

        aes_key_b64: str | None = None
        aeskey_hex = file_item.get("aeskey")
        if aeskey_hex:
            try:
                aes_key_b64 = base64.b64encode(bytes.fromhex(str(aeskey_hex))).decode("ascii")
            except Exception:
                pass
        if not aes_key_b64:
            media_aes_key = str(media.get("aes_key") or "").strip()
            aes_key_b64 = media_aes_key or None

        try:
            data = await self._download_media_bytes(
                full_url=full_url,
                encrypt_query_param=encrypt_query_param or None,
            )
            if data is None:
                logger.warning("wechat file has no download URL")
                return None

            if aes_key_b64 and _CRYPTO_AVAILABLE:
                try:
                    key = _parse_aes_key(aes_key_b64)
                    data = _aes128_ecb_decrypt(data, key)
                except Exception as exc:
                    logger.warning("wechat file decryption failed: %s", exc)
            elif aes_key_b64 and not _CRYPTO_AVAILABLE:
                logger.warning("wechat file encrypted but cryptography library not available")

            return self._write_downloaded_bytes(data=data, file_name=file_name)
        except Exception as exc:
            logger.warning("wechat file download failed: %s", exc)
            return None

    async def _download_image(self, item: dict[str, Any]) -> str | None:
        """Download WeChat image, handling AES decryption if needed."""
        image_item = item.get("image_item") or {}
        media = image_item.get("media") or {}

        full_url = media.get("full_url")
        encrypt_query_param = media.get("encrypt_query_param")

        # Get AES key (if encryption is used)
        aes_key_b64: str | None = None
        # Some images have aeskey in hex format directly
        aeskey_hex = image_item.get("aeskey")
        if aeskey_hex:
            try:
                aes_key_b64 = base64.b64encode(bytes.fromhex(str(aeskey_hex))).decode("ascii")
            except Exception:
                pass
        if not aes_key_b64:
            aes_key_b64 = media.get("aes_key")

        try:
            data = await self._download_media_bytes(
                full_url=str(full_url or "").strip(),
                encrypt_query_param=str(encrypt_query_param or "").strip() or None,
            )
            if data is None:
                logger.warning("wechat image has no download URL")
                return None

            # Decrypt if AES key is provided
            if aes_key_b64 and _CRYPTO_AVAILABLE:
                try:
                    key = _parse_aes_key(aes_key_b64)
                    data = _aes128_ecb_decrypt(data, key)
                except Exception as exc:
                    logger.warning("wechat image decryption failed: %s", exc)
                    # Continue with raw data on decryption failure
            elif aes_key_b64 and not _CRYPTO_AVAILABLE:
                logger.warning("wechat image encrypted but cryptography library not available")

            # Save to file
            return self._write_downloaded_bytes(data=data, file_name="wechat_image.jpg")

        except Exception as exc:
            logger.warning("wechat image download failed: %s", exc)
            return None

    async def _download_media_bytes(
        self,
        *,
        full_url: str,
        encrypt_query_param: str | None,
    ) -> bytes | None:
        if encrypt_query_param:
            cdn_url = f"{self.config.cdn_base_url.rstrip('/')}/download?encrypted_query_param={encrypt_query_param}"
            resp = await self._http.get(cdn_url)
            resp.raise_for_status()
            return resp.content
        if full_url:
            resp = await self._http.get(full_url)
            resp.raise_for_status()
            return resp.content
        return None

    def _write_downloaded_bytes(self, *, data: bytes, file_name: str) -> str:
        suffix = Path(file_name).suffix
        if self.config.download_dir is not None:
            target = self.config.download_dir / f"{uuid.uuid4().hex}_{Path(file_name).name}"
            target.write_bytes(data)
            return str(target)
        with NamedTemporaryFile(delete=False, suffix=suffix or ".bin") as tmp:
            tmp.write(data)
            return tmp.name

    # ===== File Sending Methods =====

    async def send_file(
        self,
        *,
        peer_user_id: str,
        file_path: str,
        caption: str = "",
        context_token: str | None = None,
    ) -> dict[str, Any]:
        """Send a file to WeChat user.

        Args:
            peer_user_id: Target user ID
            file_path: Local file path to send
            caption: Optional text caption
            context_token: Optional context token for session

        Returns:
            API response dict
        """
        if not _CRYPTO_AVAILABLE:
            raise RuntimeError("cryptography library is required for sending files")

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        plaintext = path.read_bytes()
        media_type, item_builder = self._outbound_media_builder(file_path)
        filekey = secrets.token_hex(16)
        aes_key = secrets.token_bytes(16)
        rawsize = len(plaintext)
        rawfilemd5 = hashlib.md5(plaintext).hexdigest()

        # Get upload URL from iLink
        upload_response = await self._get_upload_url(
            to_user_id=peer_user_id,
            media_type=media_type,
            filekey=filekey,
            rawsize=rawsize,
            rawfilemd5=rawfilemd5,
            filesize=_aes_padded_size(rawsize),
            aeskey_hex=aes_key.hex(),
        )

        upload_param = str(upload_response.get("upload_param") or "")
        upload_full_url = str(upload_response.get("upload_full_url") or "")

        # Encrypt file content
        ciphertext = _aes128_ecb_encrypt(plaintext, aes_key)

        # Determine upload URL
        if upload_full_url:
            upload_url = upload_full_url
        elif upload_param:
            upload_url = self._cdn_upload_url(upload_param, filekey)
        else:
            raise RuntimeError(f"getuploadurl returned neither upload_param nor upload_full_url")

        # Upload encrypted content to CDN
        encrypted_query_param = await self._upload_ciphertext(ciphertext, upload_url)

        # Resolve context token
        resolved_context_token = context_token
        if not resolved_context_token and self._token_store is not None:
            resolved_context_token = self._token_store.get(peer_user_id)

        # Send caption if provided
        if caption:
            await self.send_text(
                peer_user_id=peer_user_id,
                text=caption,
                context_token=resolved_context_token,
            )

        # Build media item
        aes_key_for_api = base64.b64encode(aes_key.hex().encode("ascii")).decode("ascii")
        item_kwargs = {
            "encrypt_query_param": encrypted_query_param,
            "aes_key_for_api": aes_key_for_api,
            "ciphertext_size": len(ciphertext),
            "plaintext_size": rawsize,
            "filename": path.name,
            "rawfilemd5": rawfilemd5,
        }
        media_item = item_builder(**item_kwargs)

        # Send media message
        client_id = str(uuid.uuid4())
        message: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": peer_user_id,
            "client_id": client_id,
            "message_type": 2,  # MSG_TYPE_BOT
            "message_state": 2,  # MSG_STATE_FINISH
            "item_list": [media_item],
        }
        if resolved_context_token:
            message["context_token"] = resolved_context_token

        payload = {
            "base_info": {"channel_version": "2.2.0"},
            "msg": message,
        }

        return await self._post_json("ilink/bot/sendmessage", payload)

    async def send_image(
        self,
        *,
        peer_user_id: str,
        image_path: str,
        caption: str = "",
        context_token: str | None = None,
    ) -> dict[str, Any]:
        """Send an image file to WeChat user."""
        return await self.send_file(
            peer_user_id=peer_user_id,
            file_path=image_path,
            caption=caption,
            context_token=context_token,
        )

    async def send_document(
        self,
        *,
        peer_user_id: str,
        file_path: str,
        caption: str = "",
        context_token: str | None = None,
    ) -> dict[str, Any]:
        """Send a document file to WeChat user."""
        return await self.send_file(
            peer_user_id=peer_user_id,
            file_path=file_path,
            caption=caption,
            context_token=context_token,
        )

    async def send_video(
        self,
        *,
        peer_user_id: str,
        video_path: str,
        caption: str = "",
        context_token: str | None = None,
    ) -> dict[str, Any]:
        """Send a video file to WeChat user."""
        return await self.send_file(
            peer_user_id=peer_user_id,
            file_path=video_path,
            caption=caption,
            context_token=context_token,
        )

    # ===== Private Helper Methods =====

    async def _get_upload_url(
        self,
        *,
        to_user_id: str,
        media_type: int,
        filekey: str,
        rawsize: int,
        rawfilemd5: str,
        filesize: int,
        aeskey_hex: str,
    ) -> dict[str, Any]:
        """Get upload URL from iLink API."""
        payload = {
            "base_info": {"channel_version": "2.2.0"},
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawfilemd5,
            "filesize": filesize,
            "no_need_thumb": True,
            "aeskey": aeskey_hex,
        }
        return await self._post_json(EP_GET_UPLOAD_URL, payload)

    async def _upload_ciphertext(self, ciphertext: bytes, upload_url: str) -> str:
        """Upload encrypted media to the CDN."""
        resp = await self._http.post(
            upload_url,
            content=ciphertext,
            headers={"Content-Type": "application/octet-stream"},
            timeout=120.0,
        )
        resp.raise_for_status()
        encrypted_param = resp.headers.get("x-encrypted-param")
        if encrypted_param:
            return encrypted_param
        raise RuntimeError(f"CDN upload missing x-encrypted-param header: {resp.text[:200]}")

    def _cdn_upload_url(self, upload_param: str, filekey: str) -> str:
        """Construct CDN upload URL."""
        cdn_base = self.config.cdn_base_url or WEIXIN_CDN_BASE_URL
        return (
            f"{cdn_base.rstrip('/')}/upload"
            f"?encrypted_query_param={quote(upload_param, safe='')}"
            f"&filekey={quote(filekey, safe='')}"
        )

    def _outbound_media_builder(self, path: str) -> tuple[int, Any]:
        """Determine media type and item builder for a file path."""
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"

        if mime.startswith("image/"):
            return MEDIA_IMAGE, lambda **kw: {
                "type": ITEM_IMAGE,
                "image_item": {
                    "media": {
                        "encrypt_query_param": kw["encrypt_query_param"],
                        "aes_key": kw["aes_key_for_api"],
                        "encrypt_type": 1,
                    },
                    "mid_size": kw["ciphertext_size"],
                },
            }

        if mime.startswith("video/"):
            return MEDIA_VIDEO, lambda **kw: {
                "type": ITEM_VIDEO,
                "video_item": {
                    "media": {
                        "encrypt_query_param": kw["encrypt_query_param"],
                        "aes_key": kw["aes_key_for_api"],
                        "encrypt_type": 1,
                    },
                    "video_size": kw["ciphertext_size"],
                    "play_length": kw.get("play_length", 0),
                    "video_md5": kw.get("rawfilemd5", ""),
                },
            }

        # Default to file type
        return MEDIA_FILE, lambda **kw: {
            "type": ITEM_FILE,
            "file_item": {
                "media": {
                    "encrypt_query_param": kw["encrypt_query_param"],
                    "aes_key": kw["aes_key_for_api"],
                    "encrypt_type": 1,
                },
                "file_name": kw["filename"],
                "len": str(kw["plaintext_size"]),
            },
        }
