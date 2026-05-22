from __future__ import annotations

import httpx


def build_gateway_model_error_reply(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPError):
        hint = (
            "模型 API 连接失败（常见于网络波动、系统代理或 SSL 握手异常）。"
            "可检查 `.env` 中 `CORA_OPENAI_HTTP_TRUST_ENV`（默认 false 直连）；"
            "若必须走代理再设为 true 并确认代理可用。"
        )
        return f"{hint}\n\n技术信息：{type(exc).__name__}: {exc}"
    return f"处理失败，请稍后重试。\n\n技术信息：{type(exc).__name__}: {exc}"


__all__ = ["build_gateway_model_error_reply"]
