"""Manual test for replay functionality."""

from core.agent.plan_replay import (
    PlanReplay,
    TaskReplay,
    OperationReplay,
    generate_replay_report,
)

# Create a sample replay
replay = PlanReplay(
    plan_id="plan-test-123",
    goal="Test replay functionality",
    status="completed",
    total_time_seconds=10.5,
    total_retries=2,
    tasks=[
        TaskReplay(
            task_id="task-1",
            title="Search files",
            status="completed",
            retry_count=0,
            start_time=0.0,
            end_time=5.0,
            duration_seconds=5.0,
            operations=[
                OperationReplay(
                    timestamp=0.0,
                    tool_name="search_files",
                    arguments={"path": "src"},
                    status="completed",
                    duration_seconds=5.0,
                )
            ],
        ),
        TaskReplay(
            task_id="task-2",
            title="Write file",
            status="completed",
            retry_count=2,
            start_time=5.0,
            end_time=10.5,
            duration_seconds=5.5,
            operations=[
                OperationReplay(
                    timestamp=5.0,
                    tool_name="write_file",
                    arguments={"path": "output.txt"},
                    status="failed",
                    duration_seconds=1.0,
                    error_message="Permission denied",
                ),
                OperationReplay(
                    timestamp=7.0,
                    tool_name="write_file",
                    arguments={"path": "output.txt"},
                    status="completed",
                    duration_seconds=3.5,
                ),
            ],
            error_category="permission_denied",
            last_error="Permission denied",
        ),
    ],
)

# Test Markdown format
print("=" * 80)
print("MARKDOWN FORMAT:")
print("=" * 80)
markdown_report = generate_replay_report(replay=replay, format="markdown")
print(markdown_report)

# Test JSON format
print("\n" + "=" * 80)
print("JSON FORMAT:")
print("=" * 80)
json_report = generate_replay_report(replay=replay, format="json")
print(json_report)
