from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from core.evals.models import EvalRunResult


def run_result_to_dict(run_result: EvalRunResult) -> dict[str, Any]:
    return {
        "created_at": run_result.created_at,
        "project_root": run_result.project_root,
        "cases_dir": run_result.cases_dir,
        "total_cases": run_result.total_cases,
        "passed_cases": run_result.passed_cases,
        "failed_cases": run_result.failed_cases,
        "total_steps": run_result.total_steps,
        "passed_steps": run_result.passed_steps,
        "failed_steps": run_result.failed_steps,
        "report_path": run_result.report_path,
        "html_report_path": run_result.html_report_path,
        "case_results": [
            {
                "case_id": case.case_id,
                "description": case.description,
                "ok": case.ok,
                "tags": list(case.tags),
                "duration_seconds": case.duration_seconds,
                "source_path": case.source_path,
                "failure_category": case.failure_category,
                "error_message": case.error_message,
                "step_results": [
                    {
                        "index": step.index,
                        "label": step.label,
                        "ok": step.ok,
                        "tool_names": list(step.tool_names),
                        "failure_category": step.failure_category,
                        "failures": [failure.message for failure in step.failures],
                        "response": step.response,
                        "observed_state": (
                            {
                                "item_count": step.observed_state.item_count,
                                "deleted_item_count": step.observed_state.deleted_item_count,
                                "pending_exists": step.observed_state.pending_exists,
                                "pending_kind": step.observed_state.pending_kind,
                                "user_memory_text": step.observed_state.user_memory_text,
                            }
                            if step.observed_state is not None
                            else None
                        ),
                    }
                    for step in case.step_results
                ],
            }
            for case in run_result.case_results
        ],
    }


def run_result_to_text(run_result: EvalRunResult) -> str:
    lines = [
        f"Eval run at {run_result.created_at}",
        f"Cases: {run_result.passed_cases}/{run_result.total_cases} passed",
        f"Steps: {run_result.passed_steps}/{run_result.total_steps} passed",
    ]
    for case in run_result.case_results:
        status = "PASS" if case.ok else "FAIL"
        lines.append(f"[{status}] {case.case_id} ({case.duration_seconds:.2f}s)")
        if case.error_message:
            category = case.failure_category or "error"
            lines.append(f"  - case error ({category}): {case.error_message}")
        for step in case.step_results:
            if step.ok:
                continue
            tool_text = ", ".join(step.tool_names) if step.tool_names else "none"
            category = f" category={step.failure_category}" if step.failure_category else ""
            lines.append(f"  - step {step.index}: tools={tool_text}{category}")
            for failure in step.failures:
                lines.append(f"    * {failure.message}")
    if run_result.report_path:
        lines.append(f"Report: {run_result.report_path}")
    if run_result.html_report_path:
        lines.append(f"HTML Report: {run_result.html_report_path}")
    return "\n".join(lines)


def write_report(path: Path, run_result: EvalRunResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run_result_to_dict(run_result), ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = html_report_path(path)
    html_path.write_text(run_result_to_html(run_result), encoding="utf-8")


def html_report_path(path: Path) -> Path:
    return path.with_suffix(".html")


def run_result_to_html(run_result: EvalRunResult) -> str:
    pass_rate = _format_rate(run_result.passed_cases, run_result.total_cases)
    step_rate = _format_rate(run_result.passed_steps, run_result.total_steps)
    case_cards = "\n".join(_render_case_html(case) for case in run_result.case_results)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cora Eval Report</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #0f172a;
      --panel: #111827;
      --panel-2: #1f2937;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --pass: #16a34a;
      --fail: #dc2626;
      --warn: #d97706;
      --border: #334155;
      --chip: #0b1220;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, "Segoe UI", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }}
    .wrap {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px;
    }}
    h1, h2, h3, p {{ margin: 0; }}
    .hero {{
      display: grid;
      gap: 16px;
      margin-bottom: 24px;
    }}
    .meta {{
      color: var(--muted);
      font-size: 14px;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-bottom: 24px;
    }}
    .stat, .case {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
    }}
    .stat {{
      padding: 16px;
    }}
    .stat .label {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }}
    .stat .value {{
      font-size: 28px;
      font-weight: 700;
    }}
    .cases {{
      display: grid;
      gap: 16px;
    }}
    .case {{
      overflow: hidden;
    }}
    .case-head {{
      padding: 16px;
      border-bottom: 1px solid var(--border);
      display: grid;
      gap: 10px;
    }}
    .row {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      background: var(--chip);
      border: 1px solid var(--border);
    }}
    .pass {{ color: #86efac; border-color: #166534; background: #052e16; }}
    .fail {{ color: #fca5a5; border-color: #7f1d1d; background: #450a0a; }}
    .warn {{ color: #fdba74; border-color: #78350f; background: #431407; }}
    .tags {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .tag {{
      font-size: 12px;
      color: var(--muted);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 2px 8px;
    }}
    .case-body {{
      padding: 16px;
      display: grid;
      gap: 12px;
    }}
    .error {{
      white-space: pre-wrap;
      color: #fecaca;
      background: rgba(127, 29, 29, 0.25);
      border: 1px solid #7f1d1d;
      border-radius: 8px;
      padding: 12px;
      font-size: 13px;
    }}
    details {{
      background: var(--panel-2);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
    }}
    summary {{
      cursor: pointer;
      font-weight: 600;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      margin: 12px 0 0;
      font-size: 12px;
      color: #cbd5e1;
    }}
    ul {{
      margin: 8px 0 0;
      padding-left: 18px;
    }}
    .small {{
      font-size: 13px;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>Cora Eval Report</h1>
      <p class="meta">Created at: {_h(run_result.created_at)}</p>
      <p class="meta">Project: {_h(run_result.project_root)}</p>
      <p class="meta">Cases: {_h(run_result.cases_dir)}</p>
    </section>
    <section class="stats">
      <div class="stat"><div class="label">Case Pass Rate</div><div class="value">{run_result.passed_cases}/{run_result.total_cases}</div><div class="small">{pass_rate}</div></div>
      <div class="stat"><div class="label">Step Pass Rate</div><div class="value">{run_result.passed_steps}/{run_result.total_steps}</div><div class="small">{step_rate}</div></div>
      <div class="stat"><div class="label">Failed Cases</div><div class="value">{run_result.failed_cases}</div><div class="small">Assertion and infrastructure failures are shown below.</div></div>
      <div class="stat"><div class="label">Reports</div><div class="value">2</div><div class="small">JSON and HTML were generated together.</div></div>
    </section>
    <section class="cases">
      {case_cards}
    </section>
  </div>
</body>
</html>
"""


def _render_case_html(case: Any) -> str:
    status_class = "pass" if case.ok else "fail"
    status_text = "PASS" if case.ok else "FAIL"
    tags_html = "".join(f'<span class="tag">{_h(tag)}</span>' for tag in case.tags) or '<span class="tag">untagged</span>'
    error_html = ""
    if case.error_message:
        category = case.failure_category or "error"
        error_html = (
            f'<div class="error"><strong>{_h(category)}</strong>\n{_h(case.error_message)}</div>'
        )
    step_blocks = "\n".join(_render_step_html(step) for step in case.step_results) or "<p class=\"small\">No step results.</p>"
    return f"""
    <article class="case">
      <div class="case-head">
        <div class="row">
          <span class="badge {status_class}">{status_text}</span>
          <h2>{_h(case.case_id)}</h2>
          <span class="small">{case.duration_seconds:.2f}s</span>
        </div>
        <p>{_h(case.description)}</p>
        <div class="tags">{tags_html}</div>
        <p class="small">{_h(case.source_path)}</p>
      </div>
      <div class="case-body">
        {error_html}
        {step_blocks}
      </div>
    </article>
    """


def _render_step_html(step: Any) -> str:
    status_class = "pass" if step.ok else "fail"
    status_text = "PASS" if step.ok else "FAIL"
    tool_names = ", ".join(step.tool_names) if step.tool_names else "none"
    failure_list = ""
    if step.failures:
        failure_list = "<ul>" + "".join(f"<li>{_h(message)}</li>" for message in step.failures) + "</ul>"
    category_html = ""
    if step.failure_category:
        chip_class = "warn" if step.failure_category == "infrastructure_failure" else "fail"
        category_html = f'<span class="badge {chip_class}">{_h(step.failure_category)}</span>'
    response_json = json.dumps(step.response, ensure_ascii=False, indent=2) if step.response else "{}"
    observed_state_json = (
        json.dumps(
            {
                "item_count": step.observed_state.item_count,
                "deleted_item_count": step.observed_state.deleted_item_count,
                "pending_exists": step.observed_state.pending_exists,
                "pending_kind": step.observed_state.pending_kind,
                "user_memory_text": step.observed_state.user_memory_text,
            },
            ensure_ascii=False,
            indent=2,
        )
        if step.observed_state is not None
        else "{}"
    )
    return f"""
    <details>
      <summary>
        <span class="badge {status_class}">{status_text}</span>
        Step {step.index}: {_h(step.label)}
        {category_html}
      </summary>
      <p class="small">Tools: {_h(tool_names)}</p>
      {failure_list}
      <pre>{_h(observed_state_json)}</pre>
      <pre>{_h(response_json)}</pre>
    </details>
    """


def _format_rate(passed: int, total: int) -> str:
    if total <= 0:
        return "0%"
    return f"{(passed / total) * 100:.1f}%"


def _h(value: Any) -> str:
    return html.escape(str(value))
