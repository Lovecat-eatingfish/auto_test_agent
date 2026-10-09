"""Deterministic pytest-framework generation from the reviewed case baseline (P3)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from api_test_agent.testframe import templates as T

CASE_LOADER_PY = '''\
"""Load data-driven cases for a module."""
from __future__ import annotations

from pathlib import Path

import yaml


def load_module_cases(module_key: str) -> list[dict]:
    path = Path(__file__).resolve().parents[1] / "data" / f"{module_key}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("cases", [])
'''

TEST_MODULE_PY = '''\
"""$module_name 模块自动化用例(由接口自动化测试 Agent 生成)。

数据来源: data/$module_key.yaml;用例编号与 02_测试用例 文档一一对应。
"""
import allure
import pytest

from common.case_loader import load_module_cases
from common.runner import run_case

MODULE_CASES = load_module_cases("$module_key")


@allure.feature("$module_name")
@pytest.mark.parametrize("case", MODULE_CASES, ids=[c["case_id"] for c in MODULE_CASES])
def test_$module_key(case, api_client, auth_headers):
    run_case(api_client, auth_headers, case)
'''


def _module_key(module: str) -> str:
    key = re.sub(r"[^0-9a-zA-Z_]+", "_", str(module).strip()).strip("_").lower()
    if not key or key[0].isdigit():
        key = f"m_{key or 'common'}"
    return key


def _module_display(state, module: str) -> str:
    for ep in state.get("endpoints", []):
        if (ep.get("module") or "COMMON").upper() == module.upper() and ep.get("module_name"):
            return ep["module_name"]
    return module


def _is_automated(case: dict) -> bool:
    return not case.get("manual") and bool(case.get("request") or case.get("scenario_steps"))


def write_data_files(testframe_dir: str | Path, cases: list[dict]) -> None:
    """Refresh data/<MODULE>.yaml (also used by the P4 patch tool)."""
    testframe_dir = Path(testframe_dir)
    data_dir = testframe_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    modules: list[str] = []
    for case in cases:
        if not _is_automated(case):
            continue
        module = str(case.get("module") or "COMMON").upper()
        if module not in modules:
            modules.append(module)
    for module in modules:
        module_cases = [c for c in cases
                        if str(c.get("module") or "COMMON").upper() == module
                        and _is_automated(c)]
        payload = {"module": module,
                   "cases": [_clean_case(c) for c in module_cases]}
        (data_dir / f"{_module_key(module)}.yaml").write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _clean_case(case: dict) -> dict:
    keep = ("case_id", "title", "priority", "design_method", "precondition", "steps",
            "test_data", "expected", "request", "expected_spec", "scenario_steps")
    cleaned = {k: case.get(k) for k in keep if case.get(k) not in (None, "", [], {})}
    request = cleaned.get("request")
    if isinstance(request, dict):
        request.setdefault("auth", None)
        for key in ("query", "json", "path_params", "headers"):
            request.setdefault(key, {})
    return cleaned


def generate_framework(testframe_dir: str | Path, cases: list[dict], cfg,
                       state_dict: dict) -> dict:
    """Generate the whole pytest project. Returns {node_map, modules}."""
    testframe_dir = Path(testframe_dir)
    testframe_dir.mkdir(parents=True, exist_ok=True)
    (testframe_dir / "testcases").mkdir(exist_ok=True)
    (testframe_dir / "common").mkdir(exist_ok=True)

    service = cfg.service
    login = service.login or {}
    (testframe_dir / "pytest.ini").write_text(T.PYTEST_INI, encoding="utf-8")
    (testframe_dir / "requirements.txt").write_text(T.REQUIREMENTS_TXT, encoding="utf-8")
    (testframe_dir / "config.yaml").write_text(T.CONFIG_YAML.substitute(
        base_url=service.base_url, timeout=service.timeout,
        username=(service.test_account or {}).get("username", ""),
        password=(service.test_account or {}).get("password", ""),
        login_path=login.get("path", ""), username_field=login.get("username_field", "username"),
        password_field=login.get("password_field", "password"),
        token_field=login.get("token_field", "data.token"),
        header_format=login.get("header_format", "Bearer {token}"),
    ), encoding="utf-8")
    (testframe_dir / "conftest.py").write_text(T.CONFTEST_PY.substitute(), encoding="utf-8")
    (testframe_dir / "README.md").write_text(T.README_MD.substitute(
        project_code=cfg.project_code), encoding="utf-8")

    (testframe_dir / "common" / "__init__.py").write_text("", encoding="utf-8")
    (testframe_dir / "common" / "api_client.py").write_text(T.API_CLIENT_PY, encoding="utf-8")
    (testframe_dir / "common" / "assertions.py").write_text(T.ASSERTIONS_PY, encoding="utf-8")
    (testframe_dir / "common" / "runner.py").write_text(T.RUNNER_PY, encoding="utf-8")
    (testframe_dir / "common" / "case_loader.py").write_text(CASE_LOADER_PY, encoding="utf-8")
    (testframe_dir / "testcases" / "__init__.py").write_text("", encoding="utf-8")

    write_data_files(testframe_dir, cases)

    node_map: dict[str, str] = {}
    modules: list[str] = []
    for case in cases:
        if not _is_automated(case):
            continue
        module = str(case.get("module") or "COMMON").upper()
        if module not in modules:
            modules.append(module)
    for module in modules:
        key = _module_key(module)
        display = _module_display(state_dict, module)
        (testframe_dir / "testcases" / f"test_{key}.py").write_text(
            TEST_MODULE_PY.replace("$module_name", display).replace("$module_key", key),
            encoding="utf-8")
        for case in [c for c in cases
                     if str(c.get("module") or "COMMON").upper() == module
                     and _is_automated(c)]:
            node_map[case["case_id"]] = f"testcases/test_{key}.py::test_{key}[{case['case_id']}]"

    (testframe_dir / ".ata_codegen.json").write_text(json.dumps({
        "modules": modules, "node_map": node_map}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"node_map": node_map, "modules": modules}
