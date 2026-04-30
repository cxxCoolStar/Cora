# Cora

`ClawBot` is the first real product scenario in this repository.

## Current Scope

This repository currently contains:

- the ClawBot V1 product and technical design docs
- the main WeChat inbound/outbound runtime based on the iLink polling chain
- text, link, and file ingestion
- item and chunk persistence in SQLite
- topic organization, retrieval, and follow-up workflows
- a local FastAPI debug UI for inspection and development
- test coverage for the current ClawBot and WeChat flows

## Main Path: WeChat Runtime

The primary runtime is the WeChat chain, not the local `serve` flow.

Typical startup flow:

1. Create and activate a Python 3.11+ virtual environment.
2. Install dependencies.
3. Configure `.env`.
4. Log in to WeChat and persist the account token locally.
5. Start the WeChat poller.

### Windows PowerShell

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest
python -m core.cli.main wechat-login
python -m core.cli.main wechat-poll
```

### macOS / Linux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pytest
python -m core.cli.main wechat-login
python -m core.cli.main wechat-poll
```

## Required Configuration

`CoreSettings` loads configuration from `.env` with the `CORA_` prefix.

At minimum, the WeChat main path expects these settings:

- `CORA_MODEL_PROVIDER`
- `CORA_MODEL`
- `CORA_OPENAI_API_KEY`
- `CORA_OPENAI_BASE_URL`
- `CORA_WECHAT_ENABLED=true`
- `CORA_WECHAT_BASE_URL`

Optional but commonly used:

- `CORA_WECHAT_TOKEN`
- `CORA_WECHAT_ACCOUNT_NAME`
- `CORA_AUXILIARY_VISION_PROVIDER`
- `CORA_AUXILIARY_VISION_MODEL`
- `CORA_AUXILIARY_VISION_API_KEY`
- `CORA_AUXILIARY_VISION_BASE_URL`

If `CORA_WECHAT_TOKEN` is not set, run `python -m core.cli.main wechat-login` first. That command fetches a QR login and saves the account token under `.cora/wechat/accounts/`.

## Local Debug UI

The local FastAPI app still exists, but it is now a secondary debug and inspection path rather than the main product chain.

Start it with:

```powershell
python -m core.cli.main serve
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

- The WeChat poller entrypoint is implemented in [src/core/cli/main.py](C:/Users/CoolStar/ai_project/Cora/src/core/cli/main.py).
- The FastAPI app is implemented in [src/core/api/app.py](C:/Users/CoolStar/ai_project/Cora/src/core/api/app.py).
- Shared runtime wiring lives in [src/core/clawbot/dependencies.py](C:/Users/CoolStar/ai_project/Cora/src/core/clawbot/dependencies.py).
