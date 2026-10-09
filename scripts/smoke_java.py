"""Smoke: P1 (scan/locate) machinery against the Java Spring project (jmeter-demo).

Uses the scripted mock LLM registering Java controller endpoints, so this
validates the language-agnostic pipeline path, not real LLM quality.
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from mock_openai_server import MockOpenAI  # noqa: E402

from api_test_agent.config import AgentConfig  # noqa: E402
from api_test_agent.pipeline import Pipeline  # noqa: E402

JAVA_ENDPOINTS = [
    {"method": "GET", "path": "/ping", "module": "COMMON", "module_name": "公共",
     "summary": "基准接口", "auth_required": False, "params": [],
     "reason": "全量扫描", "confidence": "高", "source_file": "DemoController.java",
     "evidence": '@GetMapping("/ping")'},
    {"method": "GET", "path": "/user/{id}", "module": "USER", "module_name": "用户",
     "summary": "单条查询用户", "auth_required": True,
     "params": [{"name": "id", "location": "path", "type": "long", "required": True}],
     "reason": "全量扫描", "confidence": "高", "source_file": "DemoController.java",
     "evidence": '@GetMapping("/user/{id}")'},
    {"method": "GET", "path": "/users", "module": "USER", "module_name": "用户",
     "summary": "批量查询用户", "auth_required": True,
     "params": [{"name": "size", "location": "query", "type": "int", "required": False}],
     "reason": "全量扫描", "confidence": "高", "source_file": "DemoController.java",
     "evidence": '@GetMapping("/users")'},
    {"method": "POST", "path": "/order", "module": "ORDER", "module_name": "订单",
     "summary": "创建订单", "auth_required": True,
     "params": [{"name": "userId", "location": "body", "type": "long", "required": True},
                {"name": "product", "location": "body", "type": "string", "required": False},
                {"name": "quantity", "location": "body", "type": "int", "required": False}],
     "reason": "全量扫描", "confidence": "高", "source_file": "DemoController.java",
     "evidence": '@PostMapping("/order")'},
    {"method": "POST", "path": "/login", "module": "AUTH", "module_name": "认证",
     "summary": "登录获取JWT", "auth_required": False, "params": [],
     "reason": "全量扫描", "confidence": "高", "source_file": "AuthController.java",
     "evidence": '@PostMapping("/login")'}]

CONVENTION = ("统一信封 {\"code\":0,\"msg\":\"ok\",\"data\":...};业务失败也是 HTTP 200 + 业务码"
              "(GlobalExceptionHandler 处理 BizException,如 4040 用户不存在、4001 userId 非法);"
              "鉴权失败为 401,由 JwtInterceptor 拦截。")


def script(n, body):
    text = ""
    for message in reversed(body.get("messages", [])):
        if message.get("role") == "user":
            content = message.get("content")
            text = content if isinstance(content, str) else str(content)
            break
    if "扫描与定位" not in text:
        return ({"role": "assistant", "content": f"UNKNOWN: {text[:80]}"}, "stop")
    if n == 1:
        return ({"role": "assistant", "tool_calls": [
            {"id": "j1", "type": "function", "index": 0,
             "function": {"name": "record_endpoints",
                          "arguments": json.dumps({"endpoints": JAVA_ENDPOINTS}, ensure_ascii=False)}},
            {"id": "j2", "type": "function", "index": 1,
             "function": {"name": "record_response_convention",
                          "arguments": json.dumps({"convention": CONVENTION}, ensure_ascii=False)}},
        ]}, None)
    if n == 2:
        return ({"role": "assistant", "tool_calls": [
            {"id": "j3", "type": "function", "index": 0,
             "function": {"name": "finish_phase",
                          "arguments": json.dumps({"summary": "java smoke: 5 接口"})}}]}, None)
    return ({"role": "assistant", "content": "done"}, "stop")


async def main() -> int:
    mock = MockOpenAI(port=18401, script=script)
    mock.start()
    output_dir = ROOT / "output" / "smoke_java"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    cfg = AgentConfig.load(None, {
        "project_dir": str(ROOT / "jmeter-demo"),
        "output_dir": str(output_dir),
        "project_code": "JDEMO",
        "base_url": "http://127.0.0.1:9999",
        "non_interactive": True,
    })
    cfg.llm.model_id = "mock-model"
    cfg.llm.base_url = f"http://127.0.0.1:{mock.port}/v1"
    cfg.llm.api_key = "sk-mock"

    pipe = Pipeline(cfg, print)
    code = await pipe.run()
    assert code == 0
    assert pipe.state.phase == "GATE1_REVIEW", pipe.state.phase
    assert len(pipe.state.endpoints) == 5
    assert "GlobalExceptionHandler" in pipe.state.response_convention
    review = Path(cfg.output_dir) / "01_接口清单与需求映射.md"
    assert review.exists()
    print(f"\nJAVA SMOKE PASS: {len(pipe.state.endpoints)} 个接口,评审文件 {review.name} 已生成")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
