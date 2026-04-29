from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from core.clawbot.dependencies import get_container_from_request
from core.retrieval.service import RetrievalDebugResult

router = APIRouter()


@router.post("/debug/retrieval", response_model=dict[str, Any])
def debug_retrieval(
    request: dict[str, Any],
    container=Depends(get_container_from_request),
) -> dict[str, Any]:
    """Debug endpoint to trace retrieval pipeline for a query."""
    query = request.get("query", "").strip()
    session_id = request.get("session_id", "debug")

    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    result = container.retrieval_service.search_debug(
        session_id=session_id,
        query=query,
    )

    # Convert dataclass to dict for JSON response
    return {
        "original_query": result.original_query,
        "rewritten_keywords": result.rewritten_keywords,
        "rewrite_reasoning": result.rewrite_reasoning,
        "tokens_used_for_search": result.tokens_used_for_search,
        "items_scored": [
            {
                "item_id": item.item_id,
                "title": item.title,
                "score": item.score,
                "matched_text_preview": item.matched_text_preview,
            }
            for item in result.items_scored
        ],
        "selected_item": {
            "id": result.selected_item.id,
            "title": result.selected_item.title,
            "summary": result.selected_item.summary,
        } if result.selected_item else None,
        "selected_chunk": {
            "id": result.selected_chunk.id,
            "content": result.selected_chunk.content,
        } if result.selected_chunk else None,
        "final_score": result.final_score,
        "error": result.error,
    }


@router.get("/debug/retrieval", response_class=HTMLResponse)
def debug_retrieval_page(
    request: Request,
    container=Depends(get_container_from_request),
) -> str:
    """HTML page for debugging retrieval pipeline."""
    html_content = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Retrieval Debug - ClawBot</title>
    <style>
        :root {
            --bg: #f7f4ec;
            --panel: #fffdf8;
            --ink: #1f2937;
            --muted: #6b7280;
            --line: #ddd6c8;
            --accent: #1d4ed8;
            --success: #059669;
            --error: #dc2626;
            --warning: #d97706;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: "Segoe UI", sans-serif;
            color: var(--ink);
            background: linear-gradient(180deg, #f9f5ea 0%, var(--bg) 100%);
        }
        .shell {
            max-width: 1200px;
            margin: 24px auto;
            padding: 0 16px;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
        }
        .header h1 { margin: 0; font-size: 28px; }
        .header a {
            color: white;
            background: var(--accent);
            text-decoration: none;
            padding: 10px 18px;
            border-radius: 999px;
            font-size: 14px;
        }
        .panel {
            background: rgba(255, 253, 248, 0.95);
            border: 1px solid var(--line);
            border-radius: 18px;
            box-shadow: 0 12px 32px rgba(15, 23, 42, 0.05);
            padding: 24px;
            margin-bottom: 20px;
        }
        .input-group {
            display: flex;
            gap: 12px;
            margin-bottom: 20px;
        }
        input[type="text"] {
            flex: 1;
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 14px 16px;
            font: inherit;
            font-size: 16px;
        }
        button {
            border: none;
            border-radius: 12px;
            background: var(--accent);
            color: white;
            font: inherit;
            padding: 14px 24px;
            cursor: pointer;
            font-weight: 500;
        }
        button:disabled {
            opacity: 0.6;
            cursor: not-allowed;
        }
        .section {
            margin-bottom: 24px;
        }
        .section h3 {
            margin: 0 0 12px;
            font-size: 16px;
            color: var(--muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 999px;
            font-size: 13px;
            font-weight: 500;
            margin-right: 8px;
            margin-bottom: 8px;
        }
        .badge.keyword {
            background: #eef4ff;
            color: var(--accent);
        }
        .badge.token {
            background: #ecfdf5;
            color: var(--success);
        }
        .card {
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 12px;
            background: white;
        }
        .card.selected {
            border-color: var(--accent);
            background: #eef4ff;
        }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }
        .card-title {
            font-weight: 600;
            font-size: 15px;
        }
        .card-score {
            font-family: monospace;
            font-size: 14px;
            padding: 4px 10px;
            border-radius: 6px;
            background: #f3f4f6;
        }
        .card-score.high {
            background: #d1fae5;
            color: var(--success);
        }
        .card-preview {
            font-size: 13px;
            color: var(--muted);
            font-family: monospace;
            white-space: pre-wrap;
            word-break: break-word;
            max-height: 120px;
            overflow-y: auto;
        }
        .error {
            background: #fef2f2;
            border: 1px solid #fecaca;
            border-radius: 12px;
            padding: 16px;
            color: var(--error);
        }
        .reasoning {
            background: #fffbeb;
            border-left: 4px solid var(--warning);
            padding: 12px 16px;
            font-size: 14px;
            color: var(--ink);
            border-radius: 0 8px 8px 0;
        }
        .hidden { display: none; }
        #results { margin-top: 24px; }
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .stat {
            background: white;
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 16px;
            text-align: center;
        }
        .stat-value {
            font-size: 32px;
            font-weight: 700;
            color: var(--accent);
        }
        .stat-label {
            font-size: 13px;
            color: var(--muted);
            margin-top: 4px;
        }
    </style>
</head>
<body>
    <div class="shell">
        <div class="header">
            <h1>Retrieval Debug</h1>
            <a href="/debug">Back to Debug Explorer</a>
        </div>

        <div class="panel">
            <div class="input-group">
                <input type="text" id="queryInput" placeholder="输入要测试的查询，例如：帮我找一下linux服务器的信息" />
                <button id="runBtn" onclick="runDebug()">运行调试</button>
            </div>
        </div>

        <div id="results" class="hidden">
            <!-- Stats -->
            <div class="stats">
                <div class="stat">
                    <div class="stat-value" id="totalItems">-</div>
                    <div class="stat-label">总文档数</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="matchedItems">-</div>
                    <div class="stat-label">匹配文档</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="finalScore">-</div>
                    <div class="stat-label">最终得分</div>
                </div>
            </div>

            <!-- Step 1: Original Query -->
            <div class="panel section">
                <h3>Step 1: 原始查询</h3>
                <div id="originalQuery" class="card"></div>
            </div>

            <!-- Step 2: Query Rewriting -->
            <div class="panel section">
                <h3>Step 2: Query Rewriter (LLM)</h3>
                <div id="rewriteSection">
                    <p>提取的关键词：</p>
                    <div id="keywords"></div>
                    <p style="margin-top: 12px;">推理过程：</p>
                    <div id="reasoning" class="reasoning"></div>
                </div>
            </div>

            <!-- Step 3: Search Tokens -->
            <div class="panel section">
                <h3>Step 3: 用于搜索的 Tokens</h3>
                <div id="tokens"></div>
            </div>

            <!-- Step 4: Items Scored -->
            <div class="panel section">
                <h3>Step 4: 所有文档评分</h3>
                <div id="itemsScored"></div>
            </div>

            <!-- Step 5: Selected Result -->
            <div class="panel section">
                <h3>Step 5: 选中结果</h3>
                <div id="selectedResult"></div>
            </div>

            <!-- Error (if any) -->
            <div id="errorSection" class="panel hidden">
                <h3>Error</h3>
                <div id="error" class="error"></div>
            </div>
        </div>
    </div>

    <script>
        async function runDebug() {
            const query = document.getElementById("queryInput").value.trim();
            if (!query) return;

            const btn = document.getElementById("runBtn");
            btn.disabled = true;
            btn.textContent = "运行中...";

            try {
                const response = await fetch("/debug/retrieval", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ query }),
                });

                const data = await response.json();
                displayResults(data);
            } catch (error) {
                alert("请求失败: " + error.message);
            } finally {
                btn.disabled = false;
                btn.textContent = "运行调试";
            }
        }

        function displayResults(data) {
            document.getElementById("results").classList.remove("hidden");

            // Stats
            document.getElementById("totalItems").textContent = data.items_scored.length;
            document.getElementById("matchedItems").textContent = data.items_scored.filter(i => i.score > 0).length;
            document.getElementById("finalScore").textContent = data.final_score ?? "-";

            // Step 1
            document.getElementById("originalQuery").textContent = data.original_query;

            // Step 2
            const keywordsDiv = document.getElementById("keywords");
            keywordsDiv.innerHTML = (data.rewritten_keywords || [])
                .map(k => `<span class="badge keyword">${escapeHtml(k)}</span>`)
                .join("");
            document.getElementById("reasoning").textContent = data.rewrite_reasoning || "无";

            // Step 3
            const tokensDiv = document.getElementById("tokens");
            tokensDiv.innerHTML = (data.tokens_used_for_search || [])
                .map(t => `<span class="badge token">${escapeHtml(t)}</span>`)
                .join("");

            // Step 4
            const itemsDiv = document.getElementById("itemsScored");
            itemsDiv.innerHTML = data.items_scored.map(item => `
                <div class="card ${item.item_id === data.selected_item?.id ? 'selected' : ''}">
                    <div class="card-header">
                        <span class="card-title">${escapeHtml(item.title)}</span>
                        <span class="card-score ${item.score > 0 ? 'high' : ''}">Score: ${item.score}</span>
                    </div>
                    <div class="card-preview">${escapeHtml(item.matched_text_preview)}</div>
                </div>
            `).join("");

            // Step 5
            const selectedDiv = document.getElementById("selectedResult");
            if (data.selected_item) {
                selectedDiv.innerHTML = `
                    <div class="card selected">
                        <div class="card-header">
                            <span class="card-title">${escapeHtml(data.selected_item.title)}</span>
                            <span class="card-score high">Final Score: ${data.final_score}</span>
                        </div>
                        <div style="margin-top: 8px; font-size: 14px;">
                            <strong>摘要:</strong> ${escapeHtml(data.selected_item.summary || "(无)")}
                        </div>
                        ${data.selected_chunk ? `
                            <div style="margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line);">
                                <strong>匹配 Chunk:</strong>
                                <div class="card-preview" style="margin-top: 8px;">${escapeHtml(data.selected_chunk.content)}</div>
                            </div>
                        ` : ''}
                    </div>
                `;
            } else {
                selectedDiv.innerHTML = '<div class="card">未选择任何结果</div>';
            }

            // Error
            const errorSection = document.getElementById("errorSection");
            if (data.error) {
                errorSection.classList.remove("hidden");
                document.getElementById("error").textContent = data.error;
            } else {
                errorSection.classList.add("hidden");
            }
        }

        function escapeHtml(text) {
            const div = document.createElement("div");
            div.textContent = text;
            return div.innerHTML;
        }

        // Enter key to submit
        document.getElementById("queryInput").addEventListener("keypress", function(e) {
            if (e.key === "Enter") runDebug();
        });
    </script>
</body>
</html>
'''
    return html_content
