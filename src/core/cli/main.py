from __future__ import annotations

import typer
import uvicorn

from core.agent.config import CoreSettings

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


if __name__ == "__main__":
    app()
