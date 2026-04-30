from __future__ import annotations

import logging
import typer
import uvicorn

from core.channels.wechat.account_store import WechatAccountStore
from core.channels.wechat.ilink_client import WechatIlinkClient, WechatIlinkConfig
from core.channels.wechat.login import WechatQrLoginClient
from core.channels.wechat.poller import WechatPoller
from core.channels.wechat.service import WechatGatewayService
from core.clawbot.dependencies import get_clawbot_container
from core.config import CoreSettings
from core.storage.repositories import ChannelEventRepository, ChannelSessionMapRepository

app = typer.Typer(help="CLI for ClawBot.")


@app.callback()
def main() -> None:
    """ClawBot command group."""
    return None


@app.command("serve")
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = True,
) -> None:
    """Run the local ClawBot web server."""
    _ = CoreSettings()
    uvicorn.run("core.api.app:app", host=host, port=port, reload=reload)


@app.command("wechat-poll")
def wechat_poll() -> None:
    """Start WeChat iLink long-poll worker."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    settings = CoreSettings()
    if not settings.wechat_enabled:
        raise typer.BadParameter("Set CORA_WECHAT_ENABLED=true in .env first.")
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
    typer.echo(
        f"Starting wechat poller (account={settings.wechat_account_name}, base_url={base_url})"
    )

    container = get_clawbot_container()
    container.initialize()
    gateway = WechatGatewayService(
        clawbot_service=container.clawbot_service,
        event_repository=ChannelEventRepository(container.database),
        session_map_repository=ChannelSessionMapRepository(container.database),
    )
    client = WechatIlinkClient(
        WechatIlinkConfig(
            token=token,
            base_url=base_url,
            poll_timeout_seconds=settings.wechat_poll_timeout_seconds,
            context_tokens_path=settings.wechat_accounts_dir / f"{settings.wechat_account_name}.context-tokens.json",
        )
    )
    poller = WechatPoller(client=client, gateway_service=gateway)
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


if __name__ == "__main__":
    app()
