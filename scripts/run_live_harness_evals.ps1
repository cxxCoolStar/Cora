$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$env:CORA_RUN_LIVE_EVALS = "1"
$env:CORA_EVAL_LIVE_ONLY = "1"

python -B -m core.cli.main eval-run --case-type harness --report-path .cora/evals/live-harness-latest.json
