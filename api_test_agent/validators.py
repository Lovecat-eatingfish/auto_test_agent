"""Local validation of LLM-submitted test cases (hard gates for save_module_cases)."""
from __future__ import annotations

import re

VALID_PRIORITIES = ("P0", "P1", "P2", "P3")
VALID_METHODS = ("等价类", "边界值", "场景法", "因果图")
VALID_LOCATIONS = ("path", "query", "json", "header", "body")


def _err(module: str, index: int, msg: str) -> str:
    return f"用例[{module}#{index}] {msg}"


def validate_case(case: dict, index: int, module: str, id_pattern: str) -> list[str]:
    errors: list[str] = []
    for field_name in ("title", "priority", "design_method", "expected"):
        if not str(case.get(field_name, "")).strip():
            errors.append(_err(module, index, f"缺少必填字段 {field_name}"))
    if case.get("priority") not in VALID_PRIORITIES:
        errors.append(_err(module, index, f"priority 必须是 {VALID_PRIORITIES}"))
    if case.get("design_method") not in VALID_METHODS:
        errors.append(_err(module, index, f"design_method 必须是 {VALID_METHODS} 之一"))

    scenario_steps = case.get("scenario_steps") or []
    request = case.get("request") or {}
    if scenario_steps:
        # 场景链用例:请求定义在 scenario_steps 中
        for i, step_case in enumerate(scenario_steps, start=1):
            if not isinstance(step_case, dict) \
                    or not str(step_case.get("method", "")).upper() \
                    or not str(step_case.get("path", "")).startswith("/"):
                errors.append(_err(module, index,
                                   f"scenario_steps 第{i}步必须含 method 与 / 开头的 path"))
        return errors

    if not isinstance(request, dict):
        errors.append(_err(module, index, "request 必须是对象"))
        return errors
    method = str(request.get("method", "")).upper()
    path = str(request.get("path", ""))
    if method not in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
        errors.append(_err(module, index, f"request.method 非法: {method!r}"))
    if not path.startswith("/"):
        errors.append(_err(module, index, f"request.path 必须以 / 开头: {path!r}"))

    spec = case.get("expected_spec") or {}
    if not isinstance(spec, dict):
        errors.append(_err(module, index, "expected_spec 必须是对象"))
        return errors
    status = spec.get("status_code")
    if not isinstance(status, int) or not (100 <= status <= 599):
        errors.append(_err(module, index, f"expected_spec.status_code 非法: {status!r}"))
    code = spec.get("code")
    if code is not None and not isinstance(code, int):
        errors.append(_err(module, index, f"expected_spec.code 必须是整数: {code!r}"))
    jp = spec.get("json_paths")
    if jp is not None:
        if not isinstance(jp, dict):
            errors.append(_err(module, index, "expected_spec.json_paths 必须是对象"))
        else:
            for key, value in jp.items():
                if not isinstance(key, str) or not key.strip():
                    errors.append(_err(module, index, "json_paths 的键必须是点路径字符串"))
                if isinstance(value, (dict, list)):
                    errors.append(_err(module, index, f"json_paths[{key!r}] 的值必须是标量"))

    case_id = str(case.get("case_id", ""))
    if case_id and not re.match(id_pattern, case_id):
        errors.append(_err(module, index,
                           f"case_id 不符合编号规则 {id_pattern}: {case_id!r}(留空可自动编号)"))
    return errors


def _case_endpoints(case: dict) -> list[tuple[str, str]]:
    """用例触达的 (method, path) 列表;场景链取第一步。"""
    request = case.get("request")
    if isinstance(request, dict) and request.get("method"):
        return [(str(request.get("method", "")).upper(), str(request.get("path", "")))]
    steps = case.get("scenario_steps") or []
    if steps and isinstance(steps[0], dict):
        return [(str(steps[0].get("method", "")).upper(), str(steps[0].get("path", "")))]
    return []


def path_matches(template: str, actual: str) -> bool:
    """路径模板匹配:/api/users/{user_id} 可匹配 /api/users/1。"""
    template_parts = template.strip("/").split("/")
    actual_parts = actual.strip("/").split("/")
    if len(template_parts) != len(actual_parts):
        return False
    for t_part, a_part in zip(template_parts, actual_parts):
        if t_part.startswith("{") and t_part.endswith("}"):
            if not a_part:
                return False
        elif t_part != a_part:
            return False
    return True


def _judgment_table_candidate(endpoint: dict) -> bool:
    """存在 ≥2 个可选 query/body 条件参数的接口 → 因果图/判定表适用。"""
    optional = [p for p in (endpoint.get("params") or [])
                if isinstance(p, dict) and not p.get("required", True)
                and p.get("location") in ("query", "body")]
    return len(optional) >= 2


def validate_module_cases(cases: list[dict], module: str, id_pattern: str,
                          endpoints: list[dict], coverage_check: bool = True) -> list[str]:
    """coverage_check=False 时只做用例级校验(分批保存模式);
    接口覆盖/P0/设计方法覆盖由 finish_phase 在全部提交完后统一校验。"""
    errors: list[str] = []
    seen_ids: set[str] = set()
    for i, case in enumerate(cases):
        errors.extend(validate_case(case, i + 1, module, id_pattern))
        cid = str(case.get("case_id", ""))
        if cid and cid in seen_ids:
            errors.append(_err(module, i + 1, f"case_id 重复: {cid}"))
        seen_ids.add(cid)
    if not coverage_check:
        return errors

    module_key = module.upper()
    scoped = [ep for ep in endpoints
              if (ep.get("module") or "COMMON").upper() == module_key]
    if not scoped:
        errors.append(f"模块 {module} 不在已评审的接口清单中(无对应 endpoint)")

    for ep in scoped:
        ep_pair = (str(ep.get("method", "")).upper(), str(ep.get("path", "")))
        related = [c for c in cases
                   if any(method == ep_pair[0] and path_matches(ep_pair[1], path)
                          for method, path in _case_endpoints(c))]
        if not related:
            errors.append(f"接口 {ep.get('method')} {ep.get('path')} 没有任何用例")
            continue
        if not any(c.get("priority") == "P0" for c in related):
            errors.append(f"接口 {ep.get('method')} {ep.get('path')} 缺少 P0 用例")

    methods_seen = {c.get("design_method") for c in cases}
    required_methods = [m for m in ("等价类", "边界值", "场景法") ]
    if any(_judgment_table_candidate(ep) for ep in scoped):
        required_methods.append("因果图")
    missing = [m for m in required_methods if m not in methods_seen]
    if missing and len(scoped) >= 2:
        errors.append(f"模块 {module} 缺少设计方法覆盖: {missing}")
    return errors
