from __future__ import annotations

import time
from typing import Any

import httpx


TRANSIENT_HTTP_ERRORS = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.RemoteProtocolError,
    httpx.NetworkError,
    httpx.TimeoutException,
)


def build_httpx_client(*, timeout: float, trust_env: bool = False) -> httpx.Client:
    return httpx.Client(timeout=timeout, trust_env=trust_env)


def post_json_with_retries(
    client: httpx.Client,
    *,
    url: str,
    headers: dict[str, str],
    json_payload: dict[str, Any],
    max_attempts: int = 3,
    retry_backoff_seconds: float = 0.75,
) -> httpx.Response:
    last_error: Exception | None = None
    attempts = max(1, int(max_attempts))
    for attempt in range(attempts):
        try:
            response = client.post(url, headers=headers, json=json_payload)
            response.raise_for_status()
            return response
        except TRANSIENT_HTTP_ERRORS as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(retry_backoff_seconds * (attempt + 1))
        except httpx.HTTPStatusError:
            raise
    assert last_error is not None
    raise last_error


__all__ = [
    "TRANSIENT_HTTP_ERRORS",
    "build_httpx_client",
    "post_json_with_retries",
]
