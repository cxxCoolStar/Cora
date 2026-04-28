# Cora

`core` is the first Python agent runtime in this repository.

## Current Scope

This repository currently contains:

- a V1 architecture spec
- a minimal runtime skeleton for multi-turn chat
- a structured tool interface with a few built-in tools
- an OpenAI-compatible chat completions adapter
- an interactive CLI entrypoint
- test coverage for the basic runtime flows

## Quick Start

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
pytest
core-cli chat
```

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest
core-cli chat
```

## Notes

The CLI currently uses a local fallback model client for development. It supports:

- normal echo-style multi-turn replies
- simple tool calls using slash commands such as `/tool get_time`

If `CORA_OPENAI_API_KEY` and `CORA_MODEL` are configured, the CLI will use the real provider-backed client instead.

Example:

```powershell
$env:CORA_OPENAI_API_KEY="sk-..."
$env:CORA_MODEL="gpt-4.1-mini"
core-cli chat
```
