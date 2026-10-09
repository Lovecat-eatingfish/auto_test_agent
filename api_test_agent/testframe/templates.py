"""Static file templates for the generated pytest project. $-placeholders via string.Template.

Note: templates for python files use triple-single-quoted strings because the
generated content itself contains triple-double-quoted docstrings.
"""
from __future__ import annotations

from string import Template

PYTEST_INI = """[pytest]
testpaths = testcases
addopts = -v --alluredir=allure-results --clean-alluredir
log_cli = false
"""

REQUIREMENTS_TXT = """pytest>=8.0
pytest-json-report>=1.5
requests>=2.31
PyYAML>=6.0
allure-pytest>=2.13
"""

CONFIG_YAML = Template("""\
base_url: "$base_url"
timeout: ${timeout}
test_account:
  username: "$username"
  password: "$password"
login:
  path: "$login_path"
  username_field: "$username_field"
  password_field: "$password_field"
  token_field: "$token_field"
  header_format: "$header_format"
""")

CONFTEST_PY = Template('''\
"""Session fixtures for the generated test project (created by api-test-agent)."""
from pathlib import Path

import pytest
import yaml

from common.api_client import ApiClient
from common.assertions import get_path


def load_config() -> dict:
    cfg_path = Path(__file__).parent / "config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def cfg() -> dict:
    return load_config()


@pytest.fixture(scope="session")
def api_client(cfg):
    client = ApiClient(cfg["base_url"], cfg.get("timeout", 15))
    yield client
    client.close()


@pytest.fixture(scope="session")
def auth_headers(cfg, api_client):
    """Login once and return the auth header dict (empty dict when login is not configured)."""
    login_cfg = cfg.get("login") or {}
    if not login_cfg.get("path"):
        return {}
    account = cfg.get("test_account") or {}
    resp = api_client.post(login_cfg["path"], json={
        login_cfg.get("username_field", "username"): account.get("username", ""),
        login_cfg.get("password_field", "password"): account.get("password", ""),
    })
    try:
        body = resp.json()
    except Exception as exc:
        raise AssertionError(f"登录接口返回非 JSON: {resp.status_code} {resp.text[:200]}") from exc
    if resp.status_code != 200:
        raise AssertionError(
            f"登录失败(status={resp.status_code}):请检查 config.yaml 的 test_account 与 login 配置。"
            f" 响应: {str(body)[:200]}")
    token = get_path(body, login_cfg.get("token_field", "data.token"))
    if not token:
        raise AssertionError(f"登录响应中未找到 token 字段 {login_cfg.get('token_field')!r}: {str(body)[:200]}")
    return {"Authorization": login_cfg.get("header_format", "Bearer {token}").format(token=token)}
''')

API_CLIENT_PY = '''\
"""Thin requests wrapper with per-request logging."""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger("api_client")


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def request(self, method: str, path: str, params=None, json=None,
                headers=None, data=None):
        url = f"{self.base_url}{path}"
        logger.info(">>> %s %s params=%s json=%s headers=%s",
                    method.upper(), url, params, json, headers)
        resp = self.session.request(method=method.upper(), url=url, params=params,
                                    json=json, headers=headers, data=data,
                                    timeout=self.timeout)
        logger.info("<<< %s %s -> %s %s", method.upper(), path, resp.status_code, resp.text[:500])
        return resp

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs):
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs):
        return self.request("DELETE", path, **kwargs)

    def close(self):
        self.session.close()
'''

ASSERTIONS_PY = '''\
"""JSON-aware assertion helpers."""
from __future__ import annotations


def get_path(body, dotted: str):
    """Read a value from nested dict/list by dotted path, e.g. data.items.0.username."""
    current = body
    for part in str(dotted).split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                raise AssertionError(f"路径 {dotted!r} 在响应中不存在(数组索引 {part} 非法)")
        elif isinstance(current, dict):
            if part not in current:
                raise AssertionError(f"路径 {dotted!r} 在响应中不存在(缺少键 {part!r}),"
                                     f"实际: {str(current)[:200]}")
            current = current[part]
        else:
            raise AssertionError(f"路径 {dotted!r} 无法继续解析(类型: {type(current).__name__})")
    return current


def check_response(resp, spec: dict, case_id: str = "") -> None:
    """Assert status_code / code / contains / json_paths / not_json_paths."""
    prefix = f"[{case_id}] " if case_id else ""
    expected_status = spec.get("status_code")
    if expected_status is not None and resp.status_code != expected_status:
        raise AssertionError(
            f"{prefix}HTTP 状态码不符: 期望 {expected_status}, 实际 {resp.status_code};"
            f" 响应: {resp.text[:300]}")

    try:
        body = resp.json()
    except ValueError:
        body = None

    code = spec.get("code")
    if code is not None:
        if body is None:
            raise AssertionError(f"{prefix}期望业务码 {code}, 但响应不是 JSON: {resp.text[:200]}")
        actual_code = get_path(body, "code")
        if actual_code != code:
            raise AssertionError(f"{prefix}业务码不符: 期望 {code}, 实际 {actual_code};"
                                 f" 响应: {str(body)[:300]}")

    for fragment in spec.get("contains") or []:
        if fragment not in resp.text:
            raise AssertionError(f"{prefix}响应文本未包含 {fragment!r}: {resp.text[:300]}")

    for dotted, expected in (spec.get("json_paths") or {}).items():
        if body is None:
            raise AssertionError(f"{prefix}期望断言 {dotted}, 但响应不是 JSON")
        actual = get_path(body, dotted)
        if actual != expected:
            raise AssertionError(f"{prefix}{dotted} 不符: 期望 {expected!r}, 实际 {actual!r}")

    for dotted in spec.get("not_json_paths") or []:
        if body is not None:
            try:
                value = get_path(body, dotted)
            except AssertionError:
                continue
            raise AssertionError(f"{prefix}期望字段 {dotted} 不存在, 实际为 {value!r}")
'''

RUNNER_PY = '''\
"""Case runner: executes one designed case (single request or scenario chain)."""
from __future__ import annotations

import re

import allure

from common.assertions import check_response

SEVERITY = {"P0": allure.severity_level.CRITICAL, "P1": allure.severity_level.NORMAL,
            "P2": allure.severity_level.MINOR, "P3": allure.severity_level.TRIVIAL}

INVALID_TOKEN_HEADER = {"Authorization": "Bearer invalid-token-for-negative-test"}

PLACEHOLDER_RE = re.compile(r"\\$(step\\d+)((?:\\.[A-Za-z0-9_]+)*)")


def _headers_for(auth, auth_headers, extra=None):
    headers = {}
    if auth is True:
        headers.update(auth_headers or {})
    elif auth == "invalid":
        headers.update(INVALID_TOKEN_HEADER)
    if extra:
        headers.update(extra)
    return headers


def _do_request(client, auth_headers, step: dict):
    headers = _headers_for(step.get("auth"), auth_headers, step.get("headers"))
    return client.request(method=step.get("method", "GET"), path=step.get("path", "/"),
                          params=step.get("query") or None, json=step.get("json"),
                          headers=headers or None)


def run_case(client, auth_headers, case: dict) -> None:
    allure.dynamic.title(f'{case.get("case_id", "")} {case.get("title", "")}')
    severity = SEVERITY.get(case.get("priority", "P2"))
    if severity:
        allure.dynamic.severity(severity)

    steps = case.get("scenario_steps") or []
    if steps:
        _run_scenario(client, auth_headers, case, steps)
        return

    with allure.step(f'{case["request"]["method"]} {case["request"]["path"]}'):
        resp = _do_request(client, auth_headers, case["request"])
    check_response(resp, case.get("expected_spec") or {}, case.get("case_id", ""))


def _run_scenario(client, auth_headers, case: dict, steps: list) -> None:
    """Scenario chain. Any string value may embed "$stepN.a.b" placeholders that
    resolve to fields of an earlier step's JSON response (step numbering from 1).
    """
    history: dict[str, object] = {}

    def lookup(ref: str, dotted: str):
        if ref not in history:
            raise AssertionError(f'场景 {case.get("case_id")}: 引用 ${ref} 前该步骤尚未执行')
        current = history[ref]
        for part in [p for p in dotted.split(".") if p]:
            if isinstance(current, list):
                current = current[int(part)]
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise AssertionError(
                    f'场景 {case.get("case_id")}: ${ref}{dotted} 在历史响应中不存在')
        return current

    def resolve(value):
        if isinstance(value, str):
            return PLACEHOLDER_RE.sub(
                lambda m: str(lookup(m.group(1), m.group(2))), value)
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v) for v in value]
        return value

    for index, step in enumerate(steps, start=1):
        resolved = resolve(step)
        label = step.get("description") or f'{step.get("method", "GET")} {step.get("path", "")}'
        with allure.step(f"步骤{index}: {label}"):
            resp = _do_request(client, auth_headers, resolved)
            spec = {"status_code": step.get("expect_status", 200),
                    "code": step.get("code"),
                    "contains": step.get("contains") or [],
                    "json_paths": step.get("json_paths") or {},
                    "not_json_paths": step.get("not_json_paths") or []}
            spec = {k: v for k, v in spec.items() if v not in (None, [])}
            check_response(resp, spec, f'{case.get("case_id", "")} 步骤{index}')
            history[f"step{index}"] = resp.json() if resp.content else {}
'''

README_MD = Template("""\
# 自动化测试工程($project_code)

由接口自动化测试 Agent 生成,用例基线见上级目录 `02_测试用例.xlsx / 02_测试用例.md`。

## 运行

```bash
pip install -r requirements.txt
# 修改 config.yaml 中的 base_url / 测试账号
pytest                      # 运行全部
pytest -k USER-001          # 按用例编号运行
pytest --alluredir=allure-results
allure generate allure-results -o allure-report --clean   # 生成 HTML 报告(需本机 allure)
```

## 结构

- `config.yaml` — 环境与账号配置
- `conftest.py` — base_url / 登录 token fixtures
- `common/` — API 客户端、断言、用例执行器
- `data/` — 数据驱动用例(与用例编号一一对应)
- `testcases/` — 按模块组织的测试文件
- `allure-results/` — 运行后生成;`allure generate` 产出 HTML 报告

## 说明

- 每条用例的 `case_id` 即 pytest 用例 id,与用例文档可双向追溯。
- 场景链用例中,`$$stepN.data.token` 语法可引用前面步骤的响应字段。
- 手工用例(评审时新增但未提供请求定义)不在本工程内,见文档标注。
""")
