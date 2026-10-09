"""M1 SDK smoke test 2: node backend + mock OpenAI-compatible provider + custom tool.

Integration path: HarnessConfig for model/env/tools-allowlist, then merge custom
ToolDefinitions at services level (the config path only carries tool *names*).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mock_openai_server import MockOpenAI  # noqa: E402

from pi_coding_agent import (  # noqa: E402
    HarnessConfig,
    NodeBridgeBackend,
    ToolDefinition,
    ToolResult,
    create_agent_session_from_config,
)
from pi_coding_agent.node_bridge import default_bundled_worker_path  # noqa: E402
from pi_coding_agent.services import (  # noqa: E402
    create_agent_session_from_services,
    create_agent_session_services,
)
from pi_coding_agent.harness_config import _prepare_runtime_inputs  # noqa: E402
from pi_coding_agent import CancellationToken, ToolContext  # noqa: E402


async def echo_execute(tool_call_id: str, args: dict, cancel: CancellationToken,
                       update, ctx: ToolContext) -> ToolResult:
    print(f"  [tool executed] echo({args.get('text')!r})")
    return ToolResult(content=[{"type": "text", "text": f"echo:{args.get('text')}"}])


ECHO_TOOL = ToolDefinition(
    name="echo", label="Echo",
    description="Echo back the input text.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    execute=echo_execute,
)


async def main() -> None:
    mock = MockOpenAI()
    mock.start()

    config = HarnessConfig.from_dict({
        "run": {"cwd": ".", "backend": "node"},
        "model": {
            "provider": "mocktest",
            "id": "mock-model",
            "api": "openai-completions",
            "base_url": f"http://127.0.0.1:{mock.port}/v1",
            "api_key": "sk-mock",
            "context_window": 32000,
            "max_tokens": 1024,
        },
        "tools": {"allow": ["echo"]},
    })
    backend = NodeBridgeBackend(runtime="node", worker_path=default_bundled_worker_path())
    prepared = _prepare_runtime_inputs(config, cwd_override=None, backend_override=backend)
    services = await create_agent_session_services(
        cwd=prepared.cwd,
        agent_dir=prepared.agent_dir,
        auth_storage=prepared.auth_storage,
        model_registry=prepared.model_registry,
        settings_manager=prepared.settings_manager,
        resource_loader=prepared.resource_loader,
        mcp=prepared.mcp,
    )
    merged_tools = list(prepared.tools or []) + [ECHO_TOOL]
    result = await create_agent_session_from_services(
        services=services,
        session_manager=prepared.session_manager,
        model=prepared.model,
        thinking_level=prepared.thinking_level,
        tools=merged_tools,
        no_tools=prepared.no_tools,
        scoped_models=prepared.scoped_models,
        provider_options=prepared.provider_options,
        tool_choice=prepared.tool_choice,
        parallel_tool_calls=prepared.parallel_tool_calls,
        backend=prepared.backend,
        owns_backend=prepared.owns_backend,
    )
    session = result.session

    final_texts = []

    def on_event(ev) -> None:
        if type(ev).__name__ == "MessageUpdateEvent":
            delta = getattr(getattr(ev, "assistant_message_event", None), "delta", None)
            if delta:
                final_texts.append(delta)

    session.subscribe(on_event)
    try:
        await session.prompt("Call the echo tool with text='ping', then finish.")
    except Exception as exc:
        await session.dispose()
        print(f"prompt failed: {exc!r}; mock saw {len(mock.requests)} LLM requests")
        raise
    await session.dispose()
    text = "".join(final_texts)
    print("assistant final text:", text)
    print("mock saw", len(mock.requests), "LLM calls")
    assert "MOCK_DONE" in text, "expected mock final text"
    assert len(mock.requests) >= 2, "expected tool loop (>=2 LLM calls)"
    print("SMOKE2 PASS")


asyncio.run(main())
