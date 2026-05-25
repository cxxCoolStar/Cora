"""Tests for retry policy and backoff strategy."""

from __future__ import annotations

import time

import pytest

from core.agent.retry_policy import (
    ErrorCategory,
    calculate_backoff_delay,
    classify_error,
    extract_retry_after,
    log_retry_event,
)
from core.schemas.plan import TaskResultSpec


# Error classification tests


def test_classify_error_for_timeout() -> None:
    """Timeout errors should be classified as retryable."""
    category, retryable = classify_error(error="Connection timeout")
    assert category == ErrorCategory.TIMEOUT
    assert retryable is True


def test_classify_error_for_rate_limit() -> None:
    """Rate limit errors should be classified as retryable."""
    category, retryable = classify_error(error="Rate limit exceeded", status_code=429)
    assert category == ErrorCategory.RATE_LIMIT
    assert retryable is True


def test_classify_error_for_transient_service_unavailable() -> None:
    """Service unavailable errors should be classified as retryable."""
    category, retryable = classify_error(error="Service temporarily unavailable", status_code=503)
    assert category == ErrorCategory.TRANSIENT
    assert retryable is True


def test_classify_error_for_permission_denied() -> None:
    """Permission denied errors should not be retryable."""
    category, retryable = classify_error(error="Permission denied")
    assert category == ErrorCategory.PERMISSION_DENIED
    assert retryable is False


def test_classify_error_for_invalid_arguments() -> None:
    """Invalid argument errors should not be retryable."""
    category, retryable = classify_error(error="Invalid argument: path is required")
    assert category == ErrorCategory.INVALID_ARGUMENTS
    assert retryable is False


def test_classify_error_for_user_rejection() -> None:
    """User rejection errors should not be retryable."""
    category, retryable = classify_error(error="User rejected the operation")
    assert category == ErrorCategory.USER_REJECTION
    assert retryable is False


def test_classify_error_for_safety_blocked() -> None:
    """Safety blocked errors should not be retryable."""
    category, retryable = classify_error(error="Operation safety blocked")
    assert category == ErrorCategory.SAFETY_BLOCKED
    assert retryable is False


def test_classify_error_for_unknown() -> None:
    """Unknown errors should not be retryable by default."""
    category, retryable = classify_error(error="Something went wrong")
    assert category == ErrorCategory.UNKNOWN
    assert retryable is False


def test_classify_error_with_none() -> None:
    """None error should be classified as unknown and not retryable."""
    category, retryable = classify_error(error=None)
    assert category == ErrorCategory.UNKNOWN
    assert retryable is False


def test_classify_error_with_exception_type() -> None:
    """Exception types should be classified correctly."""
    
    class TimeoutError(Exception):
        pass
    
    category, retryable = classify_error(error=TimeoutError("timeout"))
    assert category == ErrorCategory.TIMEOUT
    assert retryable is True


# Backoff delay tests


def test_calculate_backoff_delay_first_attempt() -> None:
    """First retry should have base delay."""
    delay = calculate_backoff_delay(attempt=0, base_delay=1.0, jitter=False)
    assert delay == 1.0


def test_calculate_backoff_delay_second_attempt() -> None:
    """Second retry should have 2x base delay."""
    delay = calculate_backoff_delay(attempt=1, base_delay=1.0, jitter=False)
    assert delay == 2.0


def test_calculate_backoff_delay_third_attempt() -> None:
    """Third retry should have 4x base delay."""
    delay = calculate_backoff_delay(attempt=2, base_delay=1.0, jitter=False)
    assert delay == 4.0


def test_calculate_backoff_delay_respects_max_delay() -> None:
    """Delay should not exceed max_delay."""
    delay = calculate_backoff_delay(attempt=10, base_delay=1.0, max_delay=30.0, jitter=False)
    assert delay == 30.0


def test_calculate_backoff_delay_with_jitter() -> None:
    """Delay with jitter should be slightly higher than base."""
    delay = calculate_backoff_delay(attempt=0, base_delay=1.0, jitter=True)
    assert 1.0 <= delay <= 1.5


def test_calculate_backoff_delay_negative_attempt() -> None:
    """Negative attempt should be treated as 0."""
    delay = calculate_backoff_delay(attempt=-1, base_delay=1.0, jitter=False)
    assert delay == 1.0


# Retry-After extraction tests


def test_extract_retry_after_with_seconds() -> None:
    """Retry-After header with seconds should be extracted."""
    headers = {"Retry-After": "120"}
    delay = extract_retry_after(headers)
    assert delay == 120.0


def test_extract_retry_after_case_insensitive() -> None:
    """Retry-After header should be case-insensitive."""
    headers = {"retry-after": "60"}
    delay = extract_retry_after(headers)
    assert delay == 60.0


def test_extract_retry_after_missing() -> None:
    """Missing Retry-After header should return None."""
    headers = {}
    delay = extract_retry_after(headers)
    assert delay is None


def test_extract_retry_after_invalid_format() -> None:
    """Invalid Retry-After format should return None."""
    headers = {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}
    delay = extract_retry_after(headers)
    assert delay is None


# Retry event logging tests


def test_log_retry_event() -> None:
    """Retry event should contain all required fields."""
    event = log_retry_event(
        task_id="task-1",
        retry_count=2,
        delay=4.0,
        error_category=ErrorCategory.TIMEOUT,
        error_message="Connection timeout",
    )
    
    assert event["event"] == "task.retry"
    assert event["task_id"] == "task-1"
    assert event["retry_count"] == 2
    assert event["delay_seconds"] == 4.0
    assert event["error_category"] == "timeout"
    assert event["error_message"] == "Connection timeout"
    assert "timestamp" in event


def test_log_retry_event_without_error_message() -> None:
    """Retry event should work without error message."""
    event = log_retry_event(
        task_id="task-2",
        retry_count=1,
        delay=2.0,
        error_category=ErrorCategory.RATE_LIMIT,
    )
    
    assert event["event"] == "task.retry"
    assert event["task_id"] == "task-2"
    assert event["error_message"] is None


# TaskResultSpec integration tests


def test_task_result_spec_with_retry_fields() -> None:
    """TaskResultSpec should support retry-related fields."""
    result = TaskResultSpec(
        task_id="task-1",
        run_id="run-123",
        status="failed",
        summary="Task failed after retries",
        retry_count=3,
        last_error="Connection timeout",
        error_category="timeout",
        retryable=True,
    )
    
    assert result.retry_count == 3
    assert result.last_error == "Connection timeout"
    assert result.error_category == "timeout"
    assert result.retryable is True


def test_task_result_spec_to_dict_includes_retry_fields() -> None:
    """TaskResultSpec.to_dict() should include retry fields."""
    result = TaskResultSpec(
        task_id="task-1",
        run_id="run-123",
        status="failed",
        summary="Task failed",
        retry_count=2,
        last_error="Rate limit exceeded",
        error_category="rate_limit",
        retryable=True,
    )
    
    data = result.to_dict()
    assert data["retry_count"] == 2
    assert data["last_error"] == "Rate limit exceeded"
    assert data["error_category"] == "rate_limit"
    assert data["retryable"] is True


def test_task_result_spec_default_retry_fields() -> None:
    """TaskResultSpec should have sensible defaults for retry fields."""
    result = TaskResultSpec(
        task_id="task-1",
        run_id="run-123",
    )
    
    assert result.retry_count == 0
    assert result.last_error is None
    assert result.error_category is None
    assert result.retryable is False
