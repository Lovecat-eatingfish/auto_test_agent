"""Thin wrapper around pi SDK: one LLM session per pipeline phase.

Integration notes (validated by scripts/smoke_sdk_2.py):
- model/env/tool-allowlist via HarnessConfig;
- custom ToolDefinitions are merged at services level (config only carries names);
- worker must be the self-contained bundle (default worker needs npm `zod`);
- JS runtime pinned to node (bun optional).
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from pi_coding_agent import HarnessConfig, NodeBridgeBackend, ToolDefinition, ToolResult
from pi_coding_agent.tools import TextContentBlock
from pi_coding_agent.harness_config import _prepare_runtime_inputs
from pi_coding_agent.node_bridge import default_bundled_worker_path
from pi_coding_agent.services import (create_agent_session_from_services,
                                      create_agent_session_services)


def text_tool_result(text: str) -> ToolResult:
    # content 必须是 TextContentBlock 对象:用 dict 会在结果回传时报
    # "'dict' object has no attribute 'type'"(mock e2e 测不出,真实模型必踩)
    return ToolResult(content=[TextContentBlock(text=text)])


def make_tool(name: str, description: str, parameters: dict, execute) -> ToolDefinition:
    """execute(tool_call_id, args, cancel, update, ctx) -> ToolResult"""
    import os

    debug_log = os.environ.get("ATA_TOOL_DEBUG")

    async def wrapped(tool_call_id, args, cancel, update, ctx):
        if debug_log:
            with open(debug_log, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} TOOL {name} args={_short_args(args)}\n")
        result = await execute(tool_call_id, args, cancel, update, ctx)
        if debug_log:
            text = "".join(getattr(b, "text", "") for b in (result.content or []))
            with open(debug_log, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} -> {name}: {text[:300]}\n")
        return result

    return ToolDefinition(name=name, label=name, description=description,
                          parameters=parameters, execute=wrapped)


@dataclass
class PhaseRunResult:
    final_text: str = ""
    tool_calls: int = 0
    ok: bool = True
    error: str = ""
    tool_names: list = field(default_factory=list)

    @property
    def phase_summary(self) -> str:
        return self.final_text.strip()[-2000:]


@dataclass
class PhaseAgent:
    """Runs one phase as a pi session. Not reusable across phases (create per phase)."""

    project_dir: str
    provider: str
    model_id: str
    base_url: str
    api_key: str
    context_window: int = 128000
    max_tokens: int = 32000
    allowed_builtin: list[str] = field(default_factory=lambda: ["read", "grep", "find", "ls"])
    timeout_seconds: int = 1200
    verbose: bool = False
    extra_body: dict = field(default_factory=dict)
    on_progress: object = None  # callable(str)
    compact_above_chars: int = 60000  # 会话历史超过该字符数则在下一轮追问前压缩
    compact_instructions: str = (
        "压缩本次会话历史。必须保留:阶段目标与全部硬性约束(必须调用的工具、批量大小限制、"
        "编号/格式规则)、已完成成果清单(已保存的用例与模块计数、已修改的文件路径)、当前剩余工作。"
        "可丢弃:中间工具输出原文、文件内容摘录、已处理过的报错详情。不要向用户解释压缩,直接继续执行。")

    async def run(self, prompt: str, tools: list[ToolDefinition],
                  continue_check=None, max_continuations: int = 3) -> PhaseRunResult:
        config = HarnessConfig.from_dict({
            "run": {"cwd": str(self.project_dir), "backend": "node"},
            "model": {
                "provider": self.provider or "openai-compatible",
                "id": self.model_id,
                "api": "openai-completions",
                "base_url": self.base_url,
                "api_key": self.api_key,
                "context_window": self.context_window,
                "max_tokens": self.max_tokens,
            },
            "tools": {"allow": list(self.allowed_builtin)},
            "provider_options": {"temperature": 0.2, "tool_choice": "auto",
                                 "parallel_tool_calls": False, **self.extra_body},
        })
        backend = NodeBridgeBackend(runtime="node", worker_path=default_bundled_worker_path())
        prepared = _prepare_runtime_inputs(config, cwd_override=None, backend_override=backend)
        services = await create_agent_session_services(
            cwd=prepared.cwd, agent_dir=prepared.agent_dir, auth_storage=prepared.auth_storage,
            model_registry=prepared.model_registry, settings_manager=prepared.settings_manager,
            resource_loader=prepared.resource_loader, mcp=prepared.mcp,
        )
        merged = list(prepared.tools or []) + list(tools)
        result = await create_agent_session_from_services(
            services=services, session_manager=prepared.session_manager,
            model=prepared.model, thinking_level=prepared.thinking_level,
            tools=merged, no_tools=prepared.no_tools, scoped_models=prepared.scoped_models,
            provider_options=prepared.provider_options, tool_choice=prepared.tool_choice,
            parallel_tool_calls=prepared.parallel_tool_calls,
            backend=prepared.backend, owns_backend=prepared.owns_backend,
        )
        session = result.session

        state = {"text": "", "tool_calls": 0, "tools": [], "events": {}}

        def on_event(ev) -> None:
            name = type(ev).__name__
            state["events"][name] = state["events"].get(name, 0) + 1
            if name == "MessageUpdateEvent":
                delta = getattr(getattr(ev, "assistant_message_event", None), "delta", None)
                if delta:
                    state["text"] += delta
            elif name == "ToolCallEvent":
                # 该事件在当前 SDK 版本不触发;计数以 ToolExecutionStartEvent 为准,避免双计
                tool_name = getattr(ev, "name", "") or ""
                args = getattr(ev, "arguments", None) or getattr(ev, "args", None) or {}
                self._progress(f"  [tool] {tool_name} { _short_args(args) }")
            elif name == "ToolExecutionStartEvent":
                # ToolCallEvent 在部分 SDK 版本不触发,以实际执行事件为准记录
                tool_name = getattr(ev, "tool_name", "") or getattr(ev, "name", "") or ""
                state["tools"].append(tool_name)
                state["tool_calls"] += 1
                self._progress(f"  [tool-start] {tool_name}")

        session.subscribe(on_event)

        async def _one_round(text: str) -> None:
            state["text"] = ""

            async def _meter() -> None:
                last = -1
                while True:
                    await asyncio.sleep(30)
                    size = len(state["text"])
                    if size != last:
                        self._progress(f"  [stream] 已输出 {size} 字符,工具调用 {state['tool_calls']} 次")
                        last = size

            meter = asyncio.create_task(_meter())
            try:
                await asyncio.wait_for(session.prompt(text), timeout=self.timeout_seconds)
            finally:
                meter.cancel()

        try:
            await _one_round(prompt)
            # 防惰性续跑:会话结束但完成标记未满足时,把差距作为追问发回同一会话
            continuations = 0
            while continue_check is not None and continuations < max_continuations:
                nudge = continue_check()
                if not nudge:
                    break
                await self._maybe_compact(session)
                continuations += 1
                self._progress(f"  [nudge #{continuations}] {nudge[:120]}")
                await _one_round(nudge)
        except asyncio.TimeoutError:
            text = state["text"]
            sample = text[:200].replace("\n", " ")
            return PhaseRunResult(final_text=text, tool_calls=state["tool_calls"],
                                  tool_names=state["tools"], ok=False,
                                  error=(f"阶段超时({self.timeout_seconds}s);"
                                         f"期间输出 {len(text)} 字符、工具调用 {state['tool_calls']} 次;"
                                         f"事件统计 {state['events']};"
                                         f"输出样本: {sample}"))
        except Exception as exc:
            # 会话级错误(流中断等):阶段工具的成果已实时落盘,交由上层按完成标记判断
            return PhaseRunResult(final_text=state["text"], tool_calls=state["tool_calls"],
                                  tool_names=state["tools"], ok=False,
                                  error=f"会话异常结束: {type(exc).__name__}: {exc}")
        finally:
            try:
                # dispose 偶发卡在等 node worker 退出(实测),超时兜底;残留 worker 由 CLI 硬退出回收
                await asyncio.wait_for(session.dispose(), timeout=20)
            except (Exception, asyncio.TimeoutError):
                pass
        self._progress(f"  [events] {state['events']}")
        return PhaseRunResult(final_text=state["text"], tool_calls=state["tool_calls"],
                              tool_names=state["tools"])

    async def _maybe_compact(self, session) -> None:
        """历史超限时压缩会话(请求体随历史每轮整体重发,压缩可截断二次增长)。"""
        if getattr(self, "_compaction_broken", False):
            return
        size = self._history_chars(session)
        if size <= self.compact_above_chars:
            return
        self._progress(f"  [compact] 会话历史约 {size} 字符,压缩以控制输入 token")
        try:
            await session.compact(self.compact_instructions)
        except Exception as exc:
            # 压缩失败不影响阶段推进,本会话内不再重试
            self._compaction_broken = True
            self._progress(f"  [compact] 压缩失败({type(exc).__name__}: {exc}),本会话不再尝试")
            return
        self._progress(f"  [compact] 完成,压缩后约 {self._history_chars(session)} 字符")

    def _history_chars(self, session) -> int:
        total = 0
        for message in getattr(getattr(session, "state", None), "messages", None) or []:
            content = getattr(message, "content", "")
            total += len(content) if isinstance(content, str) else len(str(content))
        return total

    def _progress(self, message: str) -> None:
        if self.on_progress:
            self.on_progress(message)
        elif self.verbose:
            print(message)


def _short_args(args) -> str:
    try:
        if isinstance(args, str):
            args = json.loads(args)
        if isinstance(args, dict):
            parts = []
            for key, value in list(args.items())[:3]:
                text = json.dumps(value, ensure_ascii=False)
                parts.append(f"{key}={text[:60]}{'…' if len(text) > 60 else ''}")
            return " ".join(parts)
    except Exception:
        pass
    return ""


def check_llm_ready(cfg) -> tuple[bool, str]:
    if not cfg.llm.is_configured():
        return False, (
            "LLM 未配置:需要 base_url / api_key / model_id。"
            "可通过 agent.yaml [llm] 段或环境变量 ATA_LLM_BASE_URL / ATA_LLM_API_KEY / ATA_LLM_MODEL 配置。")
    return True, ""
