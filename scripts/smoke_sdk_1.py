"""M1 SDK smoke test 1: in_process backend + custom tool + events."""
import asyncio

from pi_coding_agent import (
    CancellationToken,
    ToolContext,
    ToolDefinition,
    ToolResult,
    create_agent_session,
)


async def echo_execute(tool_call_id: str, args: dict, cancel: CancellationToken,
                       update, ctx: ToolContext) -> ToolResult:
    text = str(args.get("text", ""))
    return ToolResult(content=[{"type": "text", "text": f"echo:{text}"}])


ECHO_TOOL = ToolDefinition(
    name="echo",
    label="Echo",
    description="Echo back the input text. Use this to answer.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "text to echo"}},
        "required": ["text"],
    },
    execute=echo_execute,
)


async def main() -> None:
    events = []

    def on_event(ev) -> None:
        events.append(type(ev).__name__)

    result = await create_agent_session(cwd=".", backend="in_process", tools=[ECHO_TOOL])
    session = result.session
    session.subscribe(on_event)
    print("session created:", type(session).__name__)
    await session.prompt("Say hi via the echo tool with text='hello-world'.")
    await session.dispose()
    print("events:", events)


asyncio.run(main())
