"""Per-phase custom tools (ToolDefinition factories) shared by pipeline and sessions."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from pi_coding_agent import ToolDefinition

from api_test_agent import docgen, validators
from api_test_agent.pi_agent import make_tool, text_tool_result

VALID_HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")

# 启发式路由统计(仅用于给 LLM 一个"最低覆盖提示",不是事实来源)
ROUTE_HINT_RE = re.compile(
    r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)"
    r"|@(app|router|blueprint)\.(get|post|put|delete|patch|route|api_route)"
    r"|\.(get|post|put|delete|patch)\(\s*['\"]/"
    r"|@RestController|@Controller|HandleFunc|app\.listen",
    re.IGNORECASE)
HINT_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "target", "dist", "build",
                  "__pycache__", ".idea", ".session", "allure-results", "allure-report"}
HINT_SUFFIXES = {".py", ".java", ".kt", ".go", ".js", ".ts", ".mjs", ".cs"}


def route_hint_count(project_dir: str | Path) -> int:
    total = 0
    root = Path(project_dir)
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in HINT_SUFFIXES:
            continue
        if any(part in HINT_SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        total += len(ROUTE_HINT_RE.findall(text))
        if total > 500:
            break
    return total


class PhaseContext:
    """Shared mutable context handed to tool execute closures."""

    def __init__(self, cfg, state, console: Callable[[str], None]):
        self.cfg = cfg
        self.state = state
        self.console = console

    def save(self) -> None:
        self.state.save(self.cfg.state_file)


# ---------------------------------------------------------------- P1
def p1_tools(ctx: PhaseContext) -> list[ToolDefinition]:
    async def record_endpoints(tool_call_id, args, cancel, update, tool_ctx):
        endpoints = args.get("endpoints")
        if not isinstance(endpoints, list) or not endpoints:
            return text_tool_result("错误:endpoints 必须是非空数组;即使全量扫描未发现需求相关接口,"
                                    "也要登记扫描到的接口并说明。")
        cleaned, problems, no_evidence = [], [], []
        for i, ep in enumerate(endpoints):
            method = str(ep.get("method", "")).upper()
            path = str(ep.get("path", ""))
            if method not in VALID_HTTP_METHODS:
                problems.append(f"endpoints[{i}].method 非法: {ep.get('method')!r}")
                continue
            if not path.startswith("/"):
                problems.append(f"endpoints[{i}].path 必须以 / 开头: {path!r}")
                continue
            evidence = str(ep.get("evidence", "")).strip()
            if not evidence:
                no_evidence.append(f"{method} {path}")
            cleaned.append({
                "method": method,
                "path": path,
                "module": str(ep.get("module") or "COMMON").upper(),
                "module_name": str(ep.get("module_name") or ep.get("module") or "公共"),
                "summary": str(ep.get("summary", "")),
                "description": str(ep.get("description", "")),
                "auth_required": bool(ep.get("auth_required", False)),
                "params": ep.get("params") or [],
                "related_requirements": str(ep.get("related_requirements", "")),
                "reason": str(ep.get("reason", "")),
                "confidence": ep.get("confidence", "中"),
                "source_file": str(ep.get("source_file", "")),
                "evidence": evidence,
            })
        if not cleaned:
            return text_tool_result("错误:所有 endpoint 都不合法:\n" + "\n".join(problems))
        ctx.state.endpoints = cleaned
        ctx.state.stats["tool_calls"] += 1
        ctx.save()
        message = f"已登记 {len(cleaned)} 个接口。"
        if problems:
            message += ";警告: " + ";".join(problems)
        if no_evidence:
            message += (";以下接口缺少 evidence(路由定义所在源码行原文),"
                        "下次调用 record_endpoints 时必须补上: " + "; ".join(no_evidence))
        return text_tool_result(message)

    async def record_response_convention(tool_call_id, args, cancel, update, tool_ctx):
        text = str(args.get("convention", "")).strip()
        if not text:
            return text_tool_result("错误:convention 不能为空;请描述统一响应结构、成功/失败判定、业务码。")
        ctx.state.response_convention = text
        ctx.save()
        return text_tool_result("响应约定已记录。")

    async def finish_phase(tool_call_id, args, cancel, update, tool_ctx):
        if not ctx.state.endpoints:
            return text_tool_result("错误:尚未登记任何接口,不能结束阶段。请先调用 record_endpoints。")
        if not ctx.state.response_convention:
            return text_tool_result("错误:尚未记录响应约定,请先调用 record_response_convention。")
        hint = route_hint_count(ctx.cfg.project_dir)
        registered = len(ctx.state.endpoints)
        if registered < max(1, hint // 2):
            return text_tool_result(
                f"错误:登记的接口数({registered})明显少于项目中的路由定义数(启发式统计约 {hint} 处)。"
                "你很可能还没有充分探索项目。请:1) 用 grep 搜索路由注解/装饰器"
                "(如 @GetMapping/@PostMapping/@RequestMapping、@app.get/@router.get 等);"
                "2) 用 read 打开命中的每个 controller/route 文件确认真实接口;"
                "3) 补齐 record_endpoints(带 evidence 源码行原文)后再 finish_phase。"
                "如确认部分命中不是对外 HTTP 接口,请在 summary 中说明。")
        missing_evidence = [f"{ep['method']} {ep['path']}" for ep in ctx.state.endpoints
                            if not ep.get("evidence")]
        if missing_evidence:
            return text_tool_result(
                "错误:以下接口缺少 evidence(未提供源码证据,可能臆测): "
                + "; ".join(missing_evidence)
                + "。请用 read 查看对应源码,补全 evidence 后重新 record_endpoints。")
        ctx.state.phase_summary = str(args.get("summary", ""))
        ctx.state.phase_flags["P1"] = True
        ctx.save()
        ctx.console("  [phase] P1 扫描定位完成")
        return text_tool_result("P1 阶段完成。请停止调用工具,直接输出总结。")

    return [
        make_tool(
            "record_endpoints",
            "登记扫描/定位到的接口清单(整体替换)。每项: method(必填,HTTP方法), path(必填,以/开头), "
            "module(模块码,大写,如 USER), module_name(模块中文名), summary(接口摘要), "
            "description(功能说明), auth_required(布尔), params(参数摘要数组:"
            "{name,location(path|query|body),type,required,description}), "
            "related_requirements(关联的需求点), reason(入选理由), confidence(高|中|低), "
            "source_file, evidence(必填:源码中该接口的路由注解/装饰器行原文,如 "
            "'@GetMapping(\"/user/{id}\")'),"
            "没有 evidence 的接口会被视为臆测并打回。",
            {"type": "object",
             "properties": {"endpoints": {"type": "array", "items": {"type": "object"}}},
             "required": ["endpoints"]},
            record_endpoints),
        make_tool(
            "record_response_convention",
            "记录项目响应约定:统一响应 JSON 结构、成功判定(如 code==0)、失败时 HTTP 状态码还是 200+业务码、"
            "常见业务码含义、鉴权失败的表现。",
            {"type": "object", "properties": {"convention": {"type": "string"}},
             "required": ["convention"]},
            record_response_convention),
        make_tool(
            "finish_phase",
            "结束 P1 阶段(前置:已登记接口与响应约定)。",
            {"type": "object", "properties": {"summary": {"type": "string"}}, "required": []},
            finish_phase),
    ]


# ---------------------------------------------------------------- P2
def p2_tools(ctx: PhaseContext, module_scope: list[dict], id_pattern: str,
             project_code: str) -> list[ToolDefinition]:
    async def save_module_cases(tool_call_id, args, cancel, update, tool_ctx):
        module = str(args.get("module", "")).upper()
        cases = args.get("cases")
        if not module:
            return text_tool_result("错误:module 不能为空。")
        if not isinstance(cases, list) or not cases:
            return text_tool_result("错误:cases 必须是非空数组。")
        for i, case in enumerate(cases):
            case.setdefault("module", module)
            case["module"] = module
            if not str(case.get("case_id", "")).strip():
                case["case_id"] = ctx.state.next_case_id(module, project_code)
        # 分批保存:用例级校验(覆盖度由 finish_phase 统一校验)
        errors = validators.validate_module_cases(cases, module, id_pattern,
                                                  ctx.state.endpoints, coverage_check=False)
        ctx.state.stats["tool_calls"] += 1
        if errors:
            ctx.save()
            return text_tool_result("校验未通过,请修正后重新提交本批用例:\n- " + "\n- ".join(errors))
        # 按 case_id 全局增量合并(重复 id 视为修正替换);同模块历史批次必须保留
        merged = {c.get("case_id"): c for c in ctx.state.cases}
        for case in cases:
            merged[case["case_id"]] = case
        ctx.state.cases = list(merged.values())
        saved = sum(1 for c in ctx.state.cases if c.get("module") == module)
        ctx.save()
        return text_tool_result(f"本批 {len(cases)} 条已保存,模块 {module} 当前累计 {saved} 条。"
                                "继续提交下一批(每次 2-3 条);全部提交完后调用 finish_phase 结束。")

    async def finish_phase(tool_call_id, args, cancel, update, tool_ctx):
        saved_modules = {c.get("module") for c in ctx.state.cases}
        missing = [m for m in module_scope if m not in saved_modules]
        if missing:
            return text_tool_result(f"错误:以下模块尚未保存用例: {missing}。请先调用 save_module_cases。")
        # 全量统一校验:接口覆盖 + P0 + 设计方法覆盖
        scope = {m.upper() for m in module_scope}
        all_errors = []
        for m in scope:
            module_cases = [c for c in ctx.state.cases if c.get("module") == m]
            all_errors.extend(validators.validate_module_cases(
                module_cases, m, id_pattern, ctx.state.endpoints, coverage_check=True))
        if all_errors:
            # 批次会话模式下,缺口要跨会话传递给下一批的提示词
            ctx.state.p2_gaps = {"|".join(module_scope): all_errors}
            ctx.save()
            return text_tool_result("校验未通过,请补充或修正后重新 save_module_cases:\n- "
                                    + "\n- ".join(all_errors))
        ctx.state.p2_gaps = {}
        ctx.state.phase_summary = str(args.get("summary", ""))
        ctx.state.phase_flags["P2:" + "|".join(module_scope)] = True
        ctx.save()
        ctx.console("  [phase] P2 用例设计完成")
        return text_tool_result("P2 阶段完成。请停止调用工具,直接输出总结。")

    return [
        make_tool(
            "save_module_cases",
            "分批保存一个模块的测试用例(按 case_id 增量合并,重复 id 视为修正替换)。"
            "每次调用只提交 2-3 条,严禁单次超过 5 条;全部提交完后调用 finish_phase 统一校验。"
            "每条用例: "
            "case_id(可留空自动编号), module, title, priority(P0-P3), design_method(等价类|边界值|场景法|因果图), "
            "precondition, steps, test_data, expected(人类可读预期), "
            "request: {method, path, query(对象,可空), json(请求体对象,可空), auth(true 用有效token|false 不带token|\"invalid\" 无效token)}, "
            "expected_spec: {status_code(必填,整数), code(业务码,整数,可空), contains(消息子串数组,可空), "
            "json_paths(点路径→期望标量,可空), not_json_paths(点路径数组,断言字段不存在,可空)}, "
            "scenario_steps(仅场景法多步用例: [{description, method, path, query, json, auth, expect_status}])。",
            {"type": "object",
             "properties": {"module": {"type": "string"},
                            "cases": {"type": "array", "items": {"type": "object"}}},
             "required": ["module", "cases"]},
            save_module_cases),
        make_tool(
            "finish_phase",
            "结束 P2 阶段(前置:所有模块已保存且接口全覆盖)。",
            {"type": "object", "properties": {"summary": {"type": "string"}}, "required": []},
            finish_phase),
    ]


# ---------------------------------------------------------------- P4(修复)
def p4_tools(ctx: PhaseContext, testframe_dir: Path) -> list[ToolDefinition]:
    def _safe_path(rel: str) -> Path | None:
        candidate = (testframe_dir / rel).resolve()
        try:
            candidate.relative_to(testframe_dir.resolve())
        except ValueError:
            return None
        return candidate

    async def fix_script_file(tool_call_id, args, cancel, update, tool_ctx):
        rel = str(args.get("path", "")).strip().replace("\\", "/")
        content = args.get("content")
        mode = str(args.get("mode", "replace")).lower()
        target = _safe_path(rel)
        if target is None or not target.is_file():
            return text_tool_result(f"错误:只允许修改已生成的测试工程内现有文件,非法路径: {rel!r}")
        if not isinstance(content, str) or not content.strip():
            return text_tool_result("错误:content 不能为空。")
        # node bridge 对 >12KB 的工具参数会静默丢弃,工具侧在安全线内提前拦截并给出分块指引
        if len(content.encode("utf-8")) > 6000:
            return text_tool_result(
                f"错误:本次 content 约 {len(content.encode('utf-8'))//1024}KB,超过单次 6KB 上限"
                "(超大会被运行环境静默丢弃)。请分多次调用:首块 mode='replace' 写入文件开头,"
                "后续块 mode='append' 依次续写,每块 ≤150 行。")
        if mode == "append":
            with open(target, "a", encoding="utf-8") as f:
                f.write(content)
        else:
            target.write_text(content, encoding="utf-8")
        ctx.state.stats["tool_calls"] += 1
        ctx.save()
        return text_tool_result(f"已更新文件 {rel}。重新运行验证后继续。")

    async def patch_case_data(tool_call_id, args, cancel, update, tool_ctx):
        case_id = str(args.get("case_id", ""))
        case = next((c for c in ctx.state.cases if c.get("case_id") == case_id), None)
        if case is None:
            return text_tool_result(f"错误:找不到用例 {case_id}")
        patch = args.get("patch") or {}
        if patch.get("expected_spec"):
            case["expected_spec"] = patch["expected_spec"]
        if patch.get("expected"):
            case["expected"] = patch["expected"]
        if patch.get("request"):
            case["request"] = patch["request"]
        if patch.get("scenario_steps"):
            case["scenario_steps"] = patch["scenario_steps"]
        errors = validators.validate_case(case, 0, case.get("module", ""), r".+")
        if errors:
            return text_tool_result("patch 校验失败:\n- " + "\n- ".join(errors))
        ctx.state.stats["tool_calls"] += 1
        ctx.save()
        (testframe_dir / "data").mkdir(exist_ok=True)
        from api_test_agent.testframe.generator import write_data_files
        write_data_files(testframe_dir, ctx.state.cases)
        return text_tool_result(f"用例 {case_id} 已修补并同步到数据文件。")

    async def report_defect(tool_call_id, args, cancel, update, tool_ctx):
        defect = {"case_id": str(args.get("case_id", "")), "analysis": str(args.get("analysis", "")),
                  "evidence": str(args.get("evidence", ""))}
        ctx.state.selftest.setdefault("defects", []).append(defect)
        ctx.save()
        return text_tool_result("疑似缺陷已登记,将写入总结报告,不修改脚本。")

    async def finish_phase(tool_call_id, args, cancel, update, tool_ctx):
        ctx.state.phase_summary = str(args.get("summary", ""))
        ctx.state.phase_flags["P4"] = True
        ctx.save()
        return text_tool_result("P4 修复阶段结束。")

    return [
        make_tool("fix_script_file",
                  "修复测试工程内的现有文件(仅限生成的工程目录)。path 相对工程根;"
                  "mode='replace' 整文件重写(默认),mode='append' 在文件末尾续写。"
                  "单次 content 严禁超过 150 行/6KB,更大内容分多次调用:首块 replace,后续 append。",
                  {"type": "object",
                   "properties": {"path": {"type": "string"}, "content": {"type": "string"},
                                  "mode": {"type": "string", "enum": ["replace", "append"]}},
                   "required": ["path", "content"]},
                  fix_script_file),
        make_tool("patch_case_data",
                  "修正某条用例数据(判定为'用例错误'时首选,参数小速度快)。"
                  "case_id + patch{expected_spec?, expected?, request?, scenario_steps?}。",
                  {"type": "object", "properties": {"case_id": {"type": "string"},
                                                    "patch": {"type": "object"}},
                   "required": ["case_id", "patch"]},
                  patch_case_data),
        make_tool("report_defect",
                  "上报疑似被测系统缺陷(不允许修脚本或期望来让测试通过)。",
                  {"type": "object",
                   "properties": {"case_id": {"type": "string"}, "analysis": {"type": "string"},
                                  "evidence": {"type": "string"}},
                   "required": ["case_id", "analysis"]},
                  report_defect),
        make_tool("finish_phase", "结束修复阶段。",
                  {"type": "object", "properties": {"summary": {"type": "string"}}, "required": []},
                  finish_phase),
    ]
