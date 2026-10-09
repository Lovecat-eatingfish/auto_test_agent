"""E2E: full pipeline driven by a scripted mock LLM against the FastAPI demo app.

Validates the whole machinery (SDK sessions, tools, gates in non-interactive
mode, docgen, deterministic codegen, pytest self-test, allure) without a real
LLM API key. The mock "model" plays the role each phase's LLM would play.
"""
import asyncio
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from mock_openai_server import MockOpenAI  # noqa: E402

from api_test_agent.config import AgentConfig  # noqa: E402
from api_test_agent.pipeline import Pipeline  # noqa: E402

RESPONSE_CONVENTION = ("统一信封 {\"code\":0,\"message\":\"ok\",\"data\":...};code=0 表示成功;"
                       "参数校验失败 HTTP 400(code=400);鉴权失败 HTTP 401(code=401);"
                       "资源不存在 HTTP 404(code=404);重复注册 HTTP 409(code=409);"
                       "业务冲突 HTTP 400。登录接口 POST /api/login 返回 data.token。")

DEMO_ENDPOINTS = [
    {"method": "POST", "path": "/api/login", "module": "AUTH", "module_name": "认证",
     "summary": "用户登录", "auth_required": False,
     "params": [{"name": "username", "location": "body", "type": "string", "required": True},
                {"name": "password", "location": "body", "type": "string", "required": True}],
     "related_requirements": "全量", "reason": "登录入口,测试账号 admin/Admin12345",
     "confidence": "高", "source_file": "demo_app/main.py",
     "evidence": '@app.post("/api/login", summary="用户登录")'},
    {"method": "POST", "path": "/api/register", "module": "USER", "module_name": "用户",
     "summary": "用户注册", "auth_required": False,
     "params": [{"name": "username", "location": "body", "type": "string", "required": True},
                {"name": "password", "location": "body", "type": "string", "required": True},
                {"name": "email", "location": "body", "type": "string", "required": True},
                {"name": "age", "location": "body", "type": "integer", "required": False}],
     "related_requirements": "全量", "reason": "核心注册流程", "confidence": "高",
     "source_file": "demo_app/main.py",
     "evidence": '@app.post("/api/register", summary="用户注册")'},
    {"method": "GET", "path": "/api/users", "module": "USER", "module_name": "用户",
     "summary": "分页查询用户列表", "auth_required": True,
     "params": [{"name": "page", "location": "query", "type": "integer", "required": False},
                {"name": "page_size", "location": "query", "type": "integer", "required": False}],
     "related_requirements": "全量", "reason": "用户查询", "confidence": "高",
     "source_file": "demo_app/main.py",
     "evidence": '@app.get("/api/users", summary="分页查询用户列表")'},
    {"method": "GET", "path": "/api/users/{user_id}", "module": "USER", "module_name": "用户",
     "summary": "查询单个用户", "auth_required": True,
     "params": [{"name": "user_id", "location": "path", "type": "integer", "required": True}],
     "related_requirements": "全量", "reason": "用户详情", "confidence": "高",
     "source_file": "demo_app/main.py",
     "evidence": '@app.get("/api/users/{user_id}", summary="查询单个用户")'},
    {"method": "GET", "path": "/api/products", "module": "PRODUCT", "module_name": "商品",
     "summary": "分页+多条件查询商品", "auth_required": False,
     "params": [{"name": "category", "location": "query", "type": "string", "required": False},
                {"name": "min_price", "location": "query", "type": "number", "required": False},
                {"name": "max_price", "location": "query", "type": "number", "required": False}],
     "related_requirements": "全量", "reason": "商品查询", "confidence": "高",
     "source_file": "demo_app/main.py",
     "evidence": '@app.get("/api/products", summary="分页+多条件查询商品列表")'},
    {"method": "POST", "path": "/api/products", "module": "PRODUCT", "module_name": "商品",
     "summary": "创建商品", "auth_required": True,
     "params": [{"name": "name", "location": "body", "type": "string", "required": True},
                {"name": "price", "location": "body", "type": "number", "required": True},
                {"name": "category", "location": "body", "type": "string", "required": True}],
     "related_requirements": "全量", "reason": "商品写入", "confidence": "高",
     "source_file": "demo_app/main.py",
     "evidence": '@app.post("/api/products", summary="创建商品")'},
    {"method": "GET", "path": "/api/products/{product_id}", "module": "PRODUCT", "module_name": "商品",
     "summary": "查询单个商品", "auth_required": False,
     "params": [{"name": "product_id", "location": "path", "type": "integer", "required": True}],
     "related_requirements": "全量", "reason": "商品详情", "confidence": "高",
     "source_file": "demo_app/main.py",
     "evidence": '@app.get("/api/products/{product_id}", summary="查询单个商品")'},
]


def tool_call(cid, name, args, idx=0) -> dict:
    return {"id": cid, "type": "function", "index": idx,
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def _module_of(text: str) -> str:
    # 兼容旧版(“本轮只负责模块 X”)与新版批次提示词(“只负责模块 X 的下一小批用例”)
    match = re.search(r"只负责模块 (\w+)", text)
    if match:
        return match.group(1)
    for candidate in ("USER", "PRODUCT", "AUTH"):
        if f"模块 {candidate}" in text:
            return candidate
    return "AUTH"


def _all_text(body) -> str:
    parts = []
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return "\n".join(parts)


def _prompt_text(body) -> str:
    for message in reversed(body.get("messages", [])):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


class PhaseScript:
    def __init__(self):
        self.counters: dict[str, int] = {}

    def reset(self):
        self.counters.clear()

    def __call__(self, n_total, body):
        text = _all_text(body)
        if "扫描与定位" in text:
            return self._p1(self._bump("p1"))
        if "用例设计" in text:
            return self._p2(self._bump("p2:" + _module_of(text)), body)
        if "自测修复" in text:
            return self._p4(self._bump("p4"))
        return ({"role": "assistant", "content": f"MOCK_UNKNOWN_PHASE: {text[:120]}"}, "stop")

    def _bump(self, phase) -> int:
        self.counters[phase] = self.counters.get(phase, 0) + 1
        return self.counters[phase]

    def _p1(self, n):
        if n == 1:
            return ({"role": "assistant", "tool_calls": [
                tool_call("c1", "record_endpoints", {"endpoints": DEMO_ENDPOINTS}, idx=0),
                tool_call("c2", "record_response_convention", {"convention": RESPONSE_CONVENTION}, idx=1),
            ]}, None)
        if n == 2:
            return ({"role": "assistant", "tool_calls": [
                tool_call("c3", "finish_phase", {"summary": "demo: 7 个接口,3 个模块"})]}, None)
        return ({"role": "assistant", "content": "P1 mock done"}, "stop")

    def _p2(self, n, body=None):
        text = _all_text(body or {}) if body else ""
        module = _module_of(text)
        with open("D:/tmp/mock_p2_trace.log", "a", encoding="utf-8") as f:
            f.write(f"n={n} module={module} textlen={len(text)} "
                    f"AUTH={'AUTH' in text} USER={'USER' in text} PRODUCT={'PRODUCT' in text}\n")
        cases = {"AUTH": AUTH_CASES, "USER": USER_CASES, "PRODUCT": PRODUCT_CASES}[module]
        if n == 1:
            return ({"role": "assistant", "tool_calls": [
                tool_call("c1", "save_module_cases", {"module": module, "cases": cases}, idx=0),
            ]}, None)
        if n == 2:
            return ({"role": "assistant", "tool_calls": [
                tool_call("c4", "finish_phase", {"summary": f"{module} 设计完成"})]}, None)
        return ({"role": "assistant", "content": "P2 mock done"}, "stop")

    def _p4(self, n):
        if n == 1:
            return ({"role": "assistant", "tool_calls": [
                tool_call("c1", "finish_phase", {"summary": "全部通过,无需修复"})]}, None)
        return ({"role": "assistant", "content": "P4 mock done"}, "stop")


def case(cid, title, priority, method, design, precondition, steps, data, expected,
         request, expected_spec, scenario=None):
    case_dict = {"case_id": cid, "module": cid.split("-")[1], "title": title,
                 "priority": priority, "design_method": design,
                 "precondition": precondition, "steps": steps, "test_data": data,
                 "expected": expected, "request": request, "expected_spec": expected_spec}
    if scenario:
        case_dict["scenario_steps"] = scenario
        case_dict.pop("request")
    return case_dict


AUTH_CASES = [
    case("DEMO-AUTH-001", "登录成功返回token", "P0", "POST", "等价类",
         "已存在用户 admin/Admin12345", "1. POST /api/login,携带正确账号密码",
         '{"username":"admin","password":"Admin12345"}',
         "HTTP 200,code=0,data.token 非空,data.username=admin",
         {"method": "POST", "path": "/api/login",
          "json": {"username": "admin", "password": "Admin12345"}, "auth": False},
         {"status_code": 200, "code": 0,
          "json_paths": {"data.username": "admin"}, "not_json_paths": []}),
    case("DEMO-AUTH-002", "密码错误登录失败", "P1", "POST", "等价类",
         "已存在用户 admin", "1. POST /api/login,携带错误密码",
         '{"username":"admin","password":"Wrong12345"}',
         "HTTP 401,code=401,message 含 用户名或密码错误",
         {"method": "POST", "path": "/api/login",
          "json": {"username": "admin", "password": "Wrong12345"}, "auth": False},
         {"status_code": 401, "code": 401, "contains": ["用户名或密码错误"]}),
]

USER_CASES = [
    case("DEMO-USER-001", "注册成功返回用户信息", "P0", "POST", "等价类",
         "用户名 u_ata_001 未被注册", "1. POST /api/register,携带合法参数",
         '{"username":"u_ata_001","password":"Passw0rd8","email":"u1@test.com","age":20}',
         "HTTP 200,code=0,data.username=u_ata_001",
         {"method": "POST", "path": "/api/register",
          "json": {"username": "u_ata_001", "password": "Passw0rd8",
                   "email": "u1@test.com", "age": 20}, "auth": False},
         {"status_code": 200, "code": 0, "json_paths": {"data.username": "u_ata_001"}}),
    case("DEMO-USER-002", "重复注册返回409", "P1", "POST", "等价类",
         "用户名 admin 已存在", "1. POST /api/register,使用已存在的 admin",
         '{"username":"admin",...}', "HTTP 409,code=409,message 含 用户名已存在",
         {"method": "POST", "path": "/api/register",
          "json": {"username": "admin", "password": "Passw0rd8",
                   "email": "a@test.com"}, "auth": False},
         {"status_code": 409, "code": 409, "contains": ["用户名已存在"]}),
    case("DEMO-USER-003", "注册缺少必填密码", "P1", "POST", "等价类",
         "无", "1. POST /api/register,不传 password",
         '{"username":"u_ata_002","email":"u2@test.com"}',
         "HTTP 400,code=400,message 含 参数校验失败",
         {"method": "POST", "path": "/api/register",
          "json": {"username": "u_ata_002", "email": "u2@test.com"}, "auth": False},
         {"status_code": 400, "code": 400, "contains": ["参数校验失败"]}),
    case("DEMO-USER-004", "注册用户名过短(边界值)", "P2", "POST", "边界值",
         "无", "1. POST /api/register,username 长度2(低于最小3)",
         '{"username":"ab",...}', "HTTP 400,code=400",
         {"method": "POST", "path": "/api/register",
          "json": {"username": "ab", "password": "Passw0rd8",
                   "email": "u3@test.com"}, "auth": False},
         {"status_code": 400, "code": 400}),
    case("DEMO-USER-005", "年龄超上限(边界值)", "P2", "POST", "边界值",
         "无", "1. POST /api/register,age=121(超过上限120)",
         '{"age":121,...}', "HTTP 400,code=400",
         {"method": "POST", "path": "/api/register",
          "json": {"username": "u_ata_004", "password": "Passw0rd8",
                   "email": "u4@test.com", "age": 121}, "auth": False},
         {"status_code": 400, "code": 400}),
    case("DEMO-USER-006", "分页查询用户列表(带token)", "P0", "GET", "等价类",
         "已登录获取有效token", "1. GET /api/users?page=1&page_size=5,Authorization: Bearer <token>",
         "query: page=1&page_size=5", "HTTP 200,code=0,page=1",
         {"method": "GET", "path": "/api/users",
          "query": {"page": 1, "page_size": 5}, "auth": True},
         {"status_code": 200, "code": 0, "json_paths": {"data.page": 1}}),
    case("DEMO-USER-007", "未携带token访问用户列表", "P0", "GET", "等价类",
         "无", "1. GET /api/users 不带 Authorization", "无",
         "HTTP 401,code=401",
         {"method": "GET", "path": "/api/users", "auth": False},
         {"status_code": 401, "code": 401}),
    case("DEMO-USER-008", "无效token访问用户列表", "P1", "GET", "等价类",
         "无", "1. GET /api/users 携带伪造 token", "Authorization: Bearer invalid",
         "HTTP 401,code=401",
         {"method": "GET", "path": "/api/users", "auth": "invalid"},
         {"status_code": 401, "code": 401}),
    case("DEMO-USER-009", "查询存在的用户详情", "P0", "GET", "等价类",
         "用户 id=1(admin)存在", "1. GET /api/users/1,Authorization: Bearer <token>",
         "path: user_id=1", "HTTP 200,code=0,username=admin",
         {"method": "GET", "path": "/api/users/1", "auth": True},
         {"status_code": 200, "code": 0, "json_paths": {"data.username": "admin"}}),
    case("DEMO-USER-010", "查询不存在的用户", "P1", "GET", "等价类",
         "用户 id=999999 不存在", "1. GET /api/users/999999,Authorization: Bearer <token>",
         "path: user_id=999999", "HTTP 404,code=404,message 含 用户不存在",
         {"method": "GET", "path": "/api/users/999999", "auth": True},
         {"status_code": 404, "code": 404, "contains": ["用户不存在"]}),
    case("DEMO-USER-012", "用户列表条件组合(判定表)", "P2", "GET", "因果图",
         "已登录;admin 用户存在",
         "1. GET /api/users?page=1&page_size=5&keyword=admin,验证条件组合生效",
         "query: page=1&page_size=5&keyword=admin", "HTTP 200,code=0,keyword 条件命中 admin",
         {"method": "GET", "path": "/api/users",
          "query": {"page": 1, "page_size": 5, "keyword": "admin"}, "auth": True},
         {"status_code": 200, "code": 0, "json_paths": {"data.page": 1}}),
    case("DEMO-USER-011", "注册-登录-查详情场景链", "P1", "POST", "场景法",
         "用户名 u_ata_005 未被注册",
         "1. 注册 u_ata_005;2. 登录 u_ata_005 获取token;3. 用token查询该用户详情",
         "链式依赖,见步骤", "每步 HTTP 200,code=0",
         None, {},
         scenario=[
             {"description": "注册 u_ata_005", "method": "POST", "path": "/api/register",
              "json": {"username": "u_ata_005", "password": "Passw0rd8",
                       "email": "u5@test.com"}, "auth": False, "expect_status": 200},
             {"description": "登录获取token", "method": "POST", "path": "/api/login",
              "json": {"username": "u_ata_005", "password": "Passw0rd8"},
              "auth": False, "expect_status": 200},
             {"description": "查询用户详情", "method": "GET",
              "path": "/api/users/$step1.data.id",
              "headers": {"Authorization": "Bearer $step2.data.token"},
              "expect_status": 200, "json_paths": {"data.username": "u_ata_005"}},
         ]),
]

PRODUCT_CASES = [
    case("DEMO-PRODUCT-001", "按分类查询商品", "P0", "GET", "等价类",
         "种子数据含 books 分类", "1. GET /api/products?category=books", "query: category=books",
         "HTTP 200,code=0",
         {"method": "GET", "path": "/api/products", "query": {"category": "books"}, "auth": False},
         {"status_code": 200, "code": 0}),
    case("DEMO-PRODUCT-002", "最低价大于最高价返回400", "P1", "GET", "因果图",
         "无", "1. GET /api/products?min_price=100&max_price=1", "query: min_price=100&max_price=1",
         "HTTP 400,code=400,message 含 min_price不能大于max_price",
         {"method": "GET", "path": "/api/products",
          "query": {"min_price": 100, "max_price": 1}, "auth": False},
         {"status_code": 400, "code": 400, "contains": ["min_price不能大于max_price"]}),
    case("DEMO-PRODUCT-003", "创建商品成功", "P0", "POST", "等价类",
         "已登录获取有效token", "1. POST /api/products,携带合法商品数据",
         '{"name":"ATA测试品","price":9.9,"category":"books","stock":3}',
         "HTTP 200,code=0,data.name=ATA测试品",
         {"method": "POST", "path": "/api/products",
          "json": {"name": "ATA测试品", "price": 9.9, "category": "books", "stock": 3},
          "auth": True},
         {"status_code": 200, "code": 0, "json_paths": {"data.name": "ATA测试品"}}),
    case("DEMO-PRODUCT-004", "未登录创建商品", "P0", "POST", "等价类",
         "无", "1. POST /api/products 不带 token", "同上,无 Authorization",
         "HTTP 401,code=401",
         {"method": "POST", "path": "/api/products",
          "json": {"name": "ATA测试品", "price": 9.9, "category": "books"}, "auth": False},
         {"status_code": 401, "code": 401}),
    case("DEMO-PRODUCT-005", "创建商品分类非法", "P2", "POST", "边界值",
         "已登录", "1. POST /api/products,category=toys(不在枚举内)",
         '{"category":"toys",...}', "HTTP 400,code=400",
         {"method": "POST", "path": "/api/products",
          "json": {"name": "ATA测试品2", "price": 5.0, "category": "toys"}, "auth": True},
         {"status_code": 400, "code": 400}),
    case("DEMO-PRODUCT-006", "查询存在的商品详情", "P0", "GET", "等价类",
         "商品 id=1 存在", "1. GET /api/products/1", "path: product_id=1",
         "HTTP 200,code=0,id=1",
         {"method": "GET", "path": "/api/products/1", "auth": False},
         {"status_code": 200, "code": 0, "json_paths": {"data.id": 1}}),
    case("DEMO-PRODUCT-007", "查询不存在的商品", "P1", "GET", "等价类",
         "商品 id=999999 不存在", "1. GET /api/products/999999",
         "path: product_id=999999", "HTTP 404,code=404,message 含 商品不存在",
         {"method": "GET", "path": "/api/products/999999", "auth": False},
         {"status_code": 404, "code": 404, "contains": ["商品不存在"]}),
    case("DEMO-PRODUCT-008", "创建商品后查详情场景链", "P1", "POST", "场景法",
         "已登录获取有效token",
         "1. POST /api/products 创建商品;2. GET /api/products/{id} 查询该商品详情",
         "链式依赖,见步骤", "每步 HTTP 200,code=0",
         None, {},
         scenario=[
             {"description": "创建商品", "method": "POST", "path": "/api/products",
              "json": {"name": "ATA场景品", "price": 12.5, "category": "food", "stock": 2},
              "auth": True, "expect_status": 200},
             {"description": "查询该商品详情", "method": "GET",
              "path": "/api/products/$step1.data.id", "auth": False,
              "expect_status": 200, "json_paths": {"data.name": "ATA场景品"}},
         ]),
]


async def main() -> int:
    import shutil
    import subprocess

    # 清理现场:输出目录 + 8110 端口残留服务
    output_dir = ROOT / "output" / "e2e_demo"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    subprocess.run(["taskkill", "/F", "/FI", "WINDOWTITLE eq ata-e2e-*"],
                   capture_output=True)
    netstat = subprocess.run(["netstat", "-ano"], capture_output=True)
    netstat_text = netstat.stdout.decode("gbk", errors="replace")
    for line in netstat_text.splitlines():
        if ":8110" in line and "LISTENING" in line:
            pid = line.split()[-1]
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    time.sleep(1)

    script = PhaseScript()
    mock = MockOpenAI(port=18399, script=script)
    mock.start()

    cfg = AgentConfig.load(None, {
        "project_dir": str(ROOT / "demo_app"),
        "output_dir": str(output_dir),
        "project_code": "DEMO",
        "base_url": "http://127.0.0.1:8110",
        "app_cmd": (f'"{sys.executable}" -m uvicorn --app-dir "{ROOT}" '
                    f'demo_app.main:app --host 127.0.0.1 --port 8110'),
        "allure_home": r"D:\code\ceshi\component\allure-2.46.1",
        "non_interactive": True,
    })
    cfg.llm.model_id = "mock-model"
    cfg.llm.base_url = f"http://127.0.0.1:{mock.port}/v1"
    cfg.llm.api_key = "sk-mock"
    cfg.service.test_account = {"username": "admin", "password": "Admin12345"}
    cfg.service.login = {"path": "/api/login", "username_field": "username",
                         "password_field": "password", "token_field": "data.token",
                         "header_format": "Bearer {token}"}

    console = print
    pipe = Pipeline(cfg, console)

    console("=== [1/3] run()(预期停在 GATE1_REVIEW) ===")
    code = await pipe.run()
    assert code == 0, f"run() 返回 {code}"
    assert pipe.state.phase == "GATE1_REVIEW", pipe.state.phase
    assert len(pipe.state.endpoints) == 7, pipe.state.endpoints
    script.reset()

    console("=== [2/3] resume(approve-interfaces)(预期停在 GATE2_REVIEW) ===")
    code = await pipe.run(resume=True, approve_interfaces=True)
    assert code == 0, f"resume1 返回 {code}"
    assert pipe.state.phase == "GATE2_REVIEW", pipe.state.phase
    assert len(pipe.state.cases) == 22, f"用例数 {len(pipe.state.cases)}"
    script.reset()

    console("=== [3/3] resume(approve-cases)(预期 P3→P4→P5→DONE) ===")
    code = await pipe.run(resume=True, approve_cases=True)
    assert code == 0, f"resume2 返回 {code}"
    assert pipe.state.phase == "DONE", pipe.state.phase
    assert pipe.state.selftest.get("status") == "passed", pipe.state.selftest
    assert (Path(cfg.output_dir) / "02_测试用例.xlsx").exists()
    assert (Path(cfg.output_dir) / "00_总结.md").exists()
    report_index = Path(cfg.output_dir) / "allure-report" / "index.html"
    assert report_index.exists(), "allure HTML 报告未生成"
    console(f"\nE2E PASS: 用例 {len(pipe.state.cases)} 条,自测 {pipe.state.selftest.get('passed')}"
            f"/{pipe.state.selftest.get('total')} 通过,报告 {report_index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
