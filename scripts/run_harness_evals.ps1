$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

python -B -m core.cli.main eval-run --case-type harness --report-path .cora/evals/harness-latest.json
