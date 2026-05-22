@echo off
setlocal
cd /d "%~dp0.."
set CORA_RUN_LIVE_EVALS=1
python -B -m core.cli.main eval-run --case-type harness --report-path .cora/evals/live-planner-latest.json
exit /b %ERRORLEVEL%
