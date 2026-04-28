from __future__ import annotations

import typer

from core.agent.agent import CoreAgent
from core.agent.config import CoreSettings
from core.llm.dev_client import DevelopmentModelClient
from core.llm.openai_client import OpenAIChatModelClient
from core.tools.builtin.calculator import CalculatorTool
from core.tools.builtin.echo import EchoTool
from core.tools.builtin.get_time import GetTimeTool
from core.tools.registry import ToolRegistry

app = typer.Typer(help="CLI for the core agent.")


@app.callback()
def main() -> None:
    """Core agent command group."""
    return None


def build_agent() -> CoreAgent:
    settings = CoreSettings()
    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(GetTimeTool())
    registry.register(CalculatorTool())
    if settings.model_provider == "openai" or (settings.openai_api_key and settings.model):
        model_client = OpenAIChatModelClient(
            api_key=settings.openai_api_key or "",
            model=settings.model or "",
            base_url=settings.openai_base_url,
        )
    else:
        model_client = DevelopmentModelClient()
    return CoreAgent(
        settings=settings,
        model_client=model_client,
        tool_registry=registry,
    )


@app.command("chat")
def chat() -> None:
    """Start an interactive chat session."""
    agent = build_agent()
    session_id = agent.start_session()
    typer.echo(f"Started session: {session_id}")
    typer.echo("Type 'exit' to quit. Use '/tool TOOL_NAME {json}' for local tool calls.")
    while True:
        user_input = typer.prompt("you")
        if user_input.strip().lower() in {"exit", "quit"}:
            typer.echo("bye")
            raise typer.Exit()
        result = agent.run_turn(session_id=session_id, user_input=user_input)
        typer.echo(f"core: {result.response_text}")


if __name__ == "__main__":
    app()
