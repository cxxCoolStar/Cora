from __future__ import annotations

import logging
from pathlib import Path
import typer
import uvicorn

from core.channels.wechat.account_store import WechatAccountStore
from core.channels.wechat.ilink_client import WechatIlinkClient, WechatIlinkConfig
from core.channels.wechat.login import WechatQrLoginClient
from core.channels.wechat.poller import WechatPoller
from core.channels.wechat.service import WechatGatewayService
from core.clawbot.dependencies import get_clawbot_container
from core.cli.tui import launch_tui
from core.config import CoreSettings
from core.evals import EvalRunner
from core.storage.repositories import ChannelEventRepository, ChannelSessionMapRepository

app = typer.Typer(help="CLI and interactive shell for ClawBot.", no_args_is_help=False)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """ClawBot command group."""
    if ctx.invoked_subcommand is None:
        exit_code = launch_tui()
        if exit_code:
            raise typer.Exit(code=exit_code)
    return None


@app.command("tui")
def tui(
    session_id: str | None = typer.Option(None, "--session", help="Resume an existing session id."),
    trace: bool = typer.Option(True, "--trace/--no-trace", help="Show tool trace after each turn."),
) -> None:
    """Start the local interactive chat shell."""
    exit_code = launch_tui(session_id=session_id, trace=trace)
    if exit_code:
        raise typer.Exit(code=exit_code)


@app.command("serve")
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = True,
) -> None:
    """Run the local ClawBot web server."""
    _ = CoreSettings()
    uvicorn.run("core.api.app:app", host=host, port=port, reload=reload)


def _build_wechat_runtime(
    *,
    settings: CoreSettings,
    container=None,
):
    """Build the runtime objects needed by the WeChat poller."""
    account_store = WechatAccountStore(settings.wechat_accounts_dir)
    persisted = account_store.load(name=settings.wechat_account_name)
    token = settings.wechat_token or (persisted.token if persisted else None)
    base_url = settings.wechat_base_url
    if persisted and not settings.wechat_token:
        base_url = persisted.base_url
    if not token:
        raise typer.BadParameter(
            "No wechat token found. Run `python -m core.cli.main wechat-login` first, "
            "or set CORA_WECHAT_TOKEN in .env."
        )

    active_container = container or get_clawbot_container()
    active_container.initialize()

    session_map_repository = ChannelSessionMapRepository(active_container.database)
    client = WechatIlinkClient(
        WechatIlinkConfig(
            token=token,
            base_url=base_url,
            poll_timeout_seconds=settings.wechat_poll_timeout_seconds,
            context_tokens_path=settings.wechat_accounts_dir / f"{settings.wechat_account_name}.context-tokens.json",
            download_dir=settings.files_storage_dir / "wechat_inbox",
        )
    )
    gateway = WechatGatewayService(
        clawbot_service=active_container.clawbot_service,
        event_repository=ChannelEventRepository(active_container.database),
        session_map_repository=session_map_repository,
        ilink_client=client,
        session_idle_minutes=settings.wechat_session_idle_minutes,
        session_daily_reset_hour=settings.wechat_session_daily_reset_hour,
        session_timezone=settings.wechat_session_timezone,
        enable_manual_reset=settings.wechat_session_enable_manual_reset,
    )
    active_container.configure_gateway(gateway, session_map_repository)
    poller = WechatPoller(client=client, gateway_service=gateway)
    return active_container, client, gateway, poller, base_url


@app.command("wechat-poll")
def wechat_poll() -> None:
    """Start WeChat iLink long-poll worker."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = CoreSettings()
    if not settings.wechat_enabled:
        raise typer.BadParameter("Set CORA_WECHAT_ENABLED=true in .env first.")
    _, client, _, poller, base_url = _build_wechat_runtime(settings=settings)
    typer.echo(
        f"Starting wechat poller (account={settings.wechat_account_name}, base_url={base_url})"
    )
    try:
        import asyncio

        asyncio.run(poller.run_forever())
    finally:
        try:
            import asyncio

            asyncio.run(client.aclose())
        except RuntimeError:
            pass


@app.command("wechat-login")
def wechat_login(
    timeout_seconds: int = 480,
    bot_type: str = "3",
) -> None:
    """Fetch WeChat QR and persist account token locally."""
    settings = CoreSettings()
    account_store = WechatAccountStore(settings.wechat_accounts_dir)
    client = WechatQrLoginClient(base_url=settings.wechat_base_url)
    try:
        import asyncio

        result = asyncio.run(client.login(timeout_seconds=timeout_seconds, bot_type=bot_type))
        path = account_store.save(name=settings.wechat_account_name, account=result.account)
        typer.echo(f"\n已保存微信账号配置: {path}")
        typer.echo("下一步执行: python -m core.cli.main wechat-poll")
    finally:
        try:
            import asyncio

            asyncio.run(client.aclose())
        except RuntimeError:
            pass


@app.command("eval-run")
def eval_run(
    cases_dir: Path = Path("evals/cases"),
    report_path: Path = Path(".cora/evals/latest.json"),
    case_type: str | None = None,
) -> None:
    """Run the local eval suite against the current Cora runtime."""
    runner = EvalRunner(
        project_root=Path.cwd(),
        cases_dir=cases_dir,
        report_path=report_path,
        case_type=case_type,
    )
    result = runner.run()
    typer.echo(result.to_text())
    if result.failed_cases:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
