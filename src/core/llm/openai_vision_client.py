from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx


class OpenAIVisionClient:
    """OpenAI-compatible vision client used for image-to-text descriptions."""

    DEFAULT_PROMPT = (
        "Describe this image for knowledge-base retrieval. "
        "Include: scene summary, key objects, text visible in image, "
        "layout/style, and concise searchable keywords."
    )

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        prompt: str | None = None,
        max_tokens: int = 900,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("`api_key` is required for OpenAIVisionClient.")
        if not model:
            raise ValueError("`model` is required for OpenAIVisionClient.")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.prompt = (prompt or self.DEFAULT_PROMPT).strip()
        self.max_tokens = max(128, int(max_tokens))
        self._client = http_client or httpx.Client(timeout=timeout)

    def describe_image(self, *, image_path: Path, mime_type: str) -> str:
        data_url = self._to_data_url(image_path=image_path, mime_type=mime_type)
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "max_tokens": self.max_tokens,
        }
        response = self._client.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        return self._extract_text(response.json())

    @staticmethod
    def _to_data_url(*, image_path: Path, mime_type: str) -> str:
        image_bytes = image_path.read_bytes()
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        choices = data.get("choices", [])
        if not choices:
            raise ValueError("Vision response did not contain choices.")
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            text = content.strip()
            if text:
                return text
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    part_text = str(part.get("text") or "").strip()
                    if part_text:
                        parts.append(part_text)
            merged = "\n".join(parts).strip()
            if merged:
                return merged
        raise ValueError("Vision response did not contain a text description.")
