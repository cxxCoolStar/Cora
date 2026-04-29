# Cora

`ClawBot` is the first real product scenario in this repository.

## Current Scope

This repository currently contains:

- ClawBot V1 product and technical design docs
- a local FastAPI app for simulating chat-style input before WeChat integration
- text, link, and basic file ingestion
- item/chunk persistence in SQLite
- a debug explorer for inspecting saved data
- an OpenAI-compatible client for LLM-assisted intent routing
- test coverage for the current ClawBot flows

## Quick Start

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
pytest
core-cli serve
```

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest
core-cli serve
```

Then open:

```text
http://127.0.0.1:8000
```

The debug explorer is available at:

```text
http://127.0.0.1:8000/debug
```

## Notes

The current implementation focuses on:

- natural chat-like submission
- intent routing
- cautious clarification when intent is unclear
- structured storage for later retrieval

If `CORA_OPENAI_API_KEY` and `CORA_MODEL` are configured, ClawBot will use the real provider-backed client for LLM-assisted intent classification on ambiguous turns.

Example:

```powershell
$env:CORA_OPENAI_API_KEY="sk-..."
$env:CORA_MODEL="gpt-4.1-mini"
core-cli serve
```
