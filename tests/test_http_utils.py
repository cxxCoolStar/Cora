from __future__ import annotations

import httpx

from core.llm.http_utils import post_json_with_retries


def test_post_json_with_retries_recovers_from_transient_error() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    response = post_json_with_retries(
        client,
        url="https://example.com/v1/chat/completions",
        headers={"Authorization": "Bearer test"},
        json_payload={"model": "gpt-test"},
        max_attempts=3,
        retry_backoff_seconds=0.0,
    )
    assert response.json() == {"ok": True}
    assert attempts["count"] == 3
