"""Retry policy and backoff strategy for plan execution."""

from __future__ import annotations

import random
import time
from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    """Error classification for retry decisions."""
    
    TRANSIENT = "transient"  # Temporary error, retryable
    RATE_LIMIT = "rate_limit"  # Rate limit, retryable with backoff
    TIMEOUT = "timeout"  # Timeout, retryable
    PERMISSION_DENIED = "permission_denied"  # Permission denied, not retryable
    INVALID_ARGUMENTS = "invalid_arguments"  # Invalid arguments, not retryable
    USER_REJECTION = "user_rejection"  # User rejected, not retryable
    SAFETY_BLOCKED = "safety_blocked"  # Safety blocked, not retryable
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"  # Infrastructure failure, retryable
    UNKNOWN = "unknown"  # Unknown error, not retryable


def classify_error(
    *,
    error: Exception | str | None,
    tool_name: str | None = None,
    status_code: int | None = None,
) -> tuple[ErrorCategory, bool]:
    """
    Classify an error and determine if it's retryable.
    
    Args:
        error: The error to classify (Exception, error message, or None)
        tool_name: Optional tool name that caused the error
        status_code: Optional HTTP status code
    
    Returns:
        Tuple of (error_category, retryable)
    """
    if error is None:
        return (ErrorCategory.UNKNOWN, False)
    
    error_str = str(error).lower()
    normalized_tool = str(tool_name or "").strip().lower()

    if normalized_tool.startswith("mcp_"):
        if "mcp server" in error_str and "not connected" in error_str:
            return (ErrorCategory.TRANSIENT, True)
        if "mcp tool execution" in error_str or ("mcp tool" in error_str and "failed" in error_str):
            return (ErrorCategory.INFRASTRUCTURE_FAILURE, True)
        if "connection" in error_str and ("mcp" in error_str or "server" in error_str):
            return (ErrorCategory.TRANSIENT, True)
    
    # Check HTTP status codes first
    if status_code is not None:
        if status_code == 429:
            return (ErrorCategory.RATE_LIMIT, True)
        if status_code in (502, 503, 504):
            return (ErrorCategory.TRANSIENT, True)
        if status_code in (401, 403):
            return (ErrorCategory.PERMISSION_DENIED, False)
        if status_code == 400:
            return (ErrorCategory.INVALID_ARGUMENTS, False)
    
    # Check for network/transport errors
    if isinstance(error, Exception):
        error_type = type(error).__name__.lower()
        if any(keyword in error_type for keyword in ["timeout", "readtimeout", "connecttimeout"]):
            return (ErrorCategory.TIMEOUT, True)
        if any(keyword in error_type for keyword in ["connecterror", "connectionerror", "networkerror"]):
            return (ErrorCategory.TRANSIENT, True)
    
    # Check error message patterns
    if "timeout" in error_str:
        return (ErrorCategory.TIMEOUT, True)
    if "rate limit" in error_str or "too many requests" in error_str:
        return (ErrorCategory.RATE_LIMIT, True)
    if "permission denied" in error_str or "access denied" in error_str or "forbidden" in error_str or "not allowed" in error_str:
        return (ErrorCategory.PERMISSION_DENIED, False)
    if "invalid argument" in error_str or "validation error" in error_str or "bad request" in error_str:
        return (ErrorCategory.INVALID_ARGUMENTS, False)
    if "user rejected" in error_str or "user denied" in error_str:
        return (ErrorCategory.USER_REJECTION, False)
    if "safety" in error_str and "blocked" in error_str:
        return (ErrorCategory.SAFETY_BLOCKED, False)
    if "service unavailable" in error_str or "temporarily unavailable" in error_str:
        return (ErrorCategory.TRANSIENT, True)
    if "infrastructure" in error_str or "internal server error" in error_str:
        return (ErrorCategory.INFRASTRUCTURE_FAILURE, True)
    
    # Default: unknown, not retryable
    return (ErrorCategory.UNKNOWN, False)


def calculate_backoff_delay(
    *,
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
) -> float:
    """
    Calculate exponential backoff delay.
    
    Args:
        attempt: Current retry attempt (0-indexed)
        base_delay: Base delay in seconds (default: 1.0)
        max_delay: Maximum delay in seconds (default: 30.0)
        jitter: Whether to add random jitter (default: True)
    
    Returns:
        Delay time in seconds
    """
    if attempt < 0:
        attempt = 0
    
    # Exponential backoff: base_delay * (2 ^ attempt)
    delay = base_delay * (2 ** attempt)
    
    # Cap at max_delay
    delay = min(delay, max_delay)
    
    # Add jitter to avoid thundering herd
    if jitter:
        delay += random.uniform(0, 0.5)
    
    return delay


def extract_retry_after(response_headers: dict[str, str]) -> float | None:
    """
    Extract Retry-After value from HTTP response headers.
    
    Args:
        response_headers: HTTP response headers
    
    Returns:
        Delay time in seconds, or None if not present
    """
    retry_after = response_headers.get("Retry-After") or response_headers.get("retry-after")
    if not retry_after:
        return None
    
    try:
        # Retry-After can be a number of seconds
        return float(retry_after)
    except ValueError:
        # Retry-After can also be an HTTP date, but we'll skip parsing for now
        # and fall back to exponential backoff
        return None


def log_retry_event(
    *,
    task_id: str,
    retry_count: int,
    delay: float,
    error_category: ErrorCategory,
    error_message: str | None = None,
) -> dict[str, Any]:
    """
    Create a retry event for observability.
    
    Args:
        task_id: Task ID being retried
        retry_count: Current retry count
        delay: Delay before retry in seconds
        error_category: Error category
        error_message: Optional error message
    
    Returns:
        Retry event dictionary
    """
    return {
        "event": "task.retry",
        "task_id": task_id,
        "retry_count": retry_count,
        "delay_seconds": delay,
        "error_category": error_category.value,
        "error_message": error_message,
        "timestamp": time.time(),
    }


__all__ = [
    "ErrorCategory",
    "classify_error",
    "calculate_backoff_delay",
    "extract_retry_after",
    "log_retry_event",
]
