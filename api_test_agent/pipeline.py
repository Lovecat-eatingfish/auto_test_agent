"""Outer loop: deterministic phase state machine with human review gates."""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

from api_test_agent import docgen, runner, skills as skills_mod
from api_test_agent.code_tools import make_code_tools
from api_test_agent.phase_tools import (PhaseContext, p1_tools, p2_tools, p4_tools,
                                        route_hint_count)
from api_test_agent.pi_agent import PhaseAgent, check_llm_ready
from api_test_agent.requirements_input import load_requirement
from api_test_agent.state import SessionState
from api_test_agent.testframe.generator import generate_framework

PAUSED = "paused"
DONE = "done"
ABORTED = "aborted"


class Pipeline:
    def __init__(self, cfg, console):
        self.cfg = cfg
        self.console = console
        self.state: SessionState | None = None
        self.service_handle = None

    # ---------------------------------------------------------- entry
    async def run(self, resume: bool = False, approve_interfaces: bool = False,
                  approve_cases: bool = False) -> int:
        cfg = self.cfg
        if resume and cfg.state_file.exists():
            self.state = SessionState.load(cfg.state_file)
            # 续跑时补齐配置缺省:project_dir/service 若未显式配置,采用创建会话时的值,
            # 避免把启动目录当成被测项目(曾导致 P4 服务在错误 cwd 启动、修复阶段看错代码)
            snapshot = self.state.config_snapshot or {}
            if snapshot.get("project_dir") and \
                    Path(cfg.project_dir).resolve() == Path.cwd().resolve():
                cfg.project_dir = snapshot["project_dir"]
            if snapshot.get("base_url") and not cfg.service.base_url:
                cfg.service.base_url = snapshot["base_url"]
            if snapshot.get("app_cmd") and not cfg.service.app_cmd:
                cfg.service.app_cmd = snapshot["app_cmd"]
            cfg.normalize()
            self.console(f"恢复会话 {self.state.session_id}(阶段 {self.state.phase})")
        else:
            self.state = SessionState()
            self.state.config_snapshot = {
                "project_dir": cfg.project_dir, "output_dir": cfg.output_dir,
                "project_code": cfg.project_code, "base_url": cfg.service.base_url,
                "app_cmd": cfg.service.app_cmd,
            }
            cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state.save(cfg.state_file)
            self.console(f"会话 {self.state.session_id} 创建,输出目录: {cfg.output_dir}")

        if approve_interfaces:
            self.state.interfaces_approved = True
        if approve_cases:
            self.state.cases_approved = True

        handlers = {
            "P0_ENV": self.p0_env,
            "P1_LOCATE": self.p1_locate,
            "GATE1_REVIEW": self.gate1,
            "P2_DESIGN": self.p2_design,
            "GATE2_REVIEW": self.gate2,
            "P3_CODEGEN": self.p3_codegen,
            "P4_SELFTEST": self.p4_selftest,
            "P5_REPORT": self.p5_report,
            "DONE": self.p_done,
        }
        while True:
            handler = handlers.get(self.state.phase)
            if handler is None:
                self.console(f"未知阶段: {self.state.phase}")
                return 2
            outcome = await handler()
            self.state.save(cfg.state_file)
            if outcome == PAUSED:
                self.console(f"—— 会话已暂停于 {self.state.phase},处理完后执行: "
                             f"api-test-agent resume {self.state.session_id}")
                return 0
            if outcome == ABORTED:
                return 1
            if outcome == DONE:
                return 0

    # ---------------------------------------------------------- P0
    async def p0_env(self) -> str:
        cfg, state = self.cfg, self.state
        self.console("[P0] 环境自检")
        problems = []
        if not Path(cfg.project_dir).is_dir():
            problems.append(f"被测项目目录不存在: {cfg.project_dir}")
        ok, msg = check_llm_ready(cfg)
        if not ok:
            problems.append(msg)
        if not shutil.which("node"):
            problems.append("未找到 node(pi SDK 需要 Node.js ≥ 18 或 bun)")
        allure_bin = runner.find_allure(cfg)
        if allure_bin:
            self.console(f"  allure: {allure_bin}")
        else:
            self.console("  [警告] 未找到 allure 命令;不影响前序阶段,P5 报告阶段将降级"
                         "(可通过 agent.yaml 的 allure_home 配置安装路径)")
        if problems:
            for problem in problems:
                self.console(f"  [错误] {problem}")
            self.state.phase = "DONE"
            return ABORTED
        self.state.requirement = {"text": "", "source": ""}
        try:
            doc = load_requirement(cfg.requirement_text, cfg.requirement_file)
            self.state.requirement = {"text": doc.text, "source": doc.source}
        except Exception as exc:
            self.console(f"  [错误] 需求加载失败: {exc}")
            self.state.phase = "DONE"
            return ABORTED
        self.console(f"  LLM: {cfg.llm.model_id} @ {cfg.llm.base_url}")
        self.console(f"  需求来源: {self.state.requirement['source'] or '全量模式(未提供需求)'}")
        self.state.phase = "P1_LOCATE"
        return None

    # ---------------------------------------------------------- P1
    async def p1_locate(self) -> str:
        cfg, state = self.cfg, self.state
        self.console("[P1] 扫描项目与需求定位(pi session 启动)")
        ctx = PhaseContext(cfg, state, self.console)
        agent = self._agent(allowed_builtin=["find", "ls"])
        requirement = state.requirement.get("text", "")
        requirement_block = (f"当前版本需求(来源 {state.requirement.get('source')}):\n{requirement}"
                             if requirement else "未提供版本需求(全量模式):登记项目全部对外 HTTP 接口。")
        prompt = f"""你是接口自动化测试 Agent 的「扫描与定位」阶段(P1)。只读探索,禁止修改任何文件。

项目目录: {cfg.project_dir}
{requirement_block}

执行要求(必须按顺序,禁止跳过第 1 步直接登记):
1. 先探索,后登记:
   a) 用 grep_code 在项目里搜索路由定义(大文件/大结果会自动截断):Java 搜 @GetMapping|@PostMapping|@PutMapping|@DeleteMapping|@RequestMapping;Python 搜 @app.(get|post|put|delete|route) 与 @router.(get|post);Go 搜 HandleFunc|http.Handle;记下每个命中文件。
   b) 用 read_file 打开每个命中文件(大文件分页返回,续读传 offset),确认真实对外 HTTP 接口、路径、参数与鉴权依赖(如 Depends(get_current_user)、JwtInterceptor)。
2. 识别统一响应约定并调用 record_response_convention:响应 JSON 结构(信封)、成功判定(如 code==0)、失败是独立 HTTP 状态码还是 200+业务码、常见业务码含义、鉴权失败表现。必须引用异常处理器/响应包装类/controller 返回结构的真实源码,不要臆测。
3. 调用 record_endpoints 登记接口(整体替换,可多次调用修正):每项含 method/path/module(大写模块码如 USER/PRODUCT/AUTH)/module_name(中文名)/summary/description/auth_required/params(参数摘要:name,location,type,required,description)/source_file/evidence(必填:该接口路由定义行的源码原文,缺失会被打回)。
4. 需求模式下:把需求逐点映射到接口,每条给出 related_requirements、reason(入选理由)、confidence(高/中/低)。宁可多报候选交给人审,不许漏报。与需求无关的接口不要登记。
5. 全部完成后调用 finish_phase 结束,不要做用例设计(那是下一阶段)。"""
        hint = route_hint_count(cfg.project_dir)
        if hint:
            prompt += (f"\n\n提示:启发式统计显示项目中约有 {hint} 处路由定义,"
                       "请确保登记数量与此量级相符(允许少量出入,但明显偏少会被打回)。")
        result = await self._run_phase(agent, prompt, p1_tools(ctx) + make_code_tools([cfg.project_dir]),
                                       continue_check=lambda: self._p1_nudge(hint),
                                       max_continuations=8)
        self.console(f"  [会话] LLM 工具调用: {result.tool_names or '(无)'}")
        if not result.ok:
            self.console(f"  [错误] P1 阶段执行失败: {result.error}")
            return ABORTED
        if not self.state.phase_flags.get("P1"):
            self.console("  [错误] P1 会话结束但未通过完成校验(接口登记不完整),中止。"
                         "可重新运行;若持续失败,考虑更换模型或调大 phase_timeout_seconds。")
            return ABORTED
        state.phase = "GATE1_REVIEW"
        self._write_review_file()
        return None

    def _write_review_file(self) -> None:
        path = Path(self.cfg.output_dir) / "01_接口清单与需求映射.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        state = self.state
        requirement = state.requirement.get("text", "")
        lines = [
            "# 接口清单与需求映射(评审用)",
            "",
            f"- 项目码:`{self.cfg.project_code}`;需求来源:{state.requirement.get('source') or '全量模式'}",
            f"- 需求摘要:{(requirement[:160] + '…') if len(requirement) > 160 else (requirement or '(全量模式)')}",
            "",
            "## 响应约定",
            "",
            state.response_convention or "(未记录)",
            "",
            "## 接口清单",
            "",
            "| 序号 | 方法 | 路径 | 模块 | 模块名 | 摘要 | 关联需求 | 入选理由 | 置信度 | 需鉴权 |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for i, ep in enumerate(state.endpoints, start=1):
            lines.append("| {no} | {method} | {path} | {module} | {module_name} | {summary} | "
                         "{req} | {reason} | {confidence} | {auth} |".format(
                no=i, method=ep.get("method", ""), path=ep.get("path", ""),
                module=ep.get("module", ""), module_name=ep.get("module_name", ""),
                summary=(ep.get("summary", "") or "").replace("|", "\\|"),
                req=(ep.get("related_requirements", "") or "").replace("|", "\\|"),
                reason=(ep.get("reason", "") or "").replace("|", "\\|"),
                confidence=ep.get("confidence", ""), auth="是" if ep.get("auth_required") else "否"))
        lines += ["", "> 评审说明:可直接编辑本文件(删除行=移除接口,修改单元格=修正信息,表尾可新增行)。"
                  "编辑后在本终端输入 e 回读;输入 y 直接确认。", ""]
        path.write_text("\n".join(lines), encoding="utf-8")
        state.interface_review_file = str(path)
        self.console(f"  评审文件已生成: {path}")

    def _parse_review_file(self) -> list[dict]:
        """Read back the (possibly user-edited) review markdown."""
        path = Path(self.state.interface_review_file)
        text = path.read_text(encoding="utf-8")
        endpoints = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 6 or not cells[0].isdigit():
                continue
            method = cells[1].upper()
            path_value = cells[2]
            if method not in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS") \
                    or not path_value.startswith("/"):
                continue
            original = None
            idx = int(cells[0]) - 1
            if 0 <= idx < len(self.state.endpoints):
                original = self.state.endpoints[idx]
            endpoint = dict(original or {})
            endpoint.update({
                "method": method, "path": path_value,
                "module": (cells[3] or endpoint.get("module") or "COMMON").upper(),
                "module_name": cells[4] or endpoint.get("module_name", ""),
                "summary": cells[5].replace("\\|", "|") if len(cells) > 5 else "",
            })
            if len(cells) > 8:
                endpoint["confidence"] = cells[8]
            if len(cells) > 9:
                endpoint["auth_required"] = cells[9] == "是"
            endpoints.append(endpoint)
        return endpoints

    # ---------------------------------------------------------- gate 1
    async def gate1(self) -> str:
        cfg, state = self.cfg, self.state
        self.console(f"[门禁1] 接口清单评审({len(state.endpoints)} 个接口)")
        if state.interfaces_approved:
            state.interfaces_approved = False
            state.phase = "P2_DESIGN"
            self.console("  已通过 resume 审批,继续用例设计。")
            return None
        if cfg.non_interactive:
            return PAUSED
        while True:
            answer = input("  确认接口清单?[y=确认 / e=我已编辑文件,回读 / a=添加接口 / d=删除接口 / n=放弃] ")
            answer = answer.strip().lower()
            if answer == "y":
                break
            if answer == "e":
                try:
                    state.endpoints = self._parse_review_file()
                except Exception as exc:
                    self.console(f"  回读失败: {exc};请检查文件格式后重试")
                    continue
                self._write_review_file()
                self.console(f"  已回读,当前 {len(state.endpoints)} 个接口")
                state.save(cfg.state_file)
            elif answer == "a":
                raw = input("  新增接口(格式: 方法 路径 模块码,如 POST /api/xxx USER): ").strip()
                parts = raw.split()
                if len(parts) >= 2 and parts[0].upper() in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                    state.endpoints.append({
                        "method": parts[0].upper(), "path": parts[1],
                        "module": (parts[2] if len(parts) > 2 else "COMMON").upper(),
                        "module_name": parts[2] if len(parts) > 2 else "公共",
                        "summary": "人工添加", "reason": "评审人工添加", "confidence": "高",
                    })
                    self._write_review_file()
                    state.save(cfg.state_file)
                else:
                    self.console("  格式不对,已忽略")
            elif answer == "d":
                raw = input("  输入要删除的序号(逗号分隔): ").strip()
                try:
                    drop = {int(x) for x in raw.split(",") if x.strip()}
                except ValueError:
                    self.console("  序号格式不对,已忽略")
                    continue
                state.endpoints = [ep for i, ep in enumerate(state.endpoints, start=1)
                                   if i not in drop]
                self._write_review_file()
                state.save(cfg.state_file)
            elif answer == "n":
                self.console("  已放弃。")
                return ABORTED
        state.phase = "P2_DESIGN"
        return None

    # ---------------------------------------------------------- P2
    async def p2_design(self) -> str:
        cfg, state = self.cfg, self.state
        self.console("[P2] 测试用例设计(pi session 启动)")
        skills_by_type, warnings = skills_mod.discover_skills(cfg.project_dir)
        design_skill = {k: v for k, v in skills_by_type.items()}
        doc_skill = design_skill
        for warning in warnings:
            self.console(f"  [警告] {warning}")
        state.design_skill_name = skills_by_type["case-design"].name if skills_by_type.get("case-design") else ""
        state.doc_skill_name = skills_by_type["doc-format"].name if skills_by_type.get("doc-format") else ""
        if not skills_by_type.get("case-design") or not skills_by_type.get("doc-format"):
            self.console("  [错误] 缺少内置 Skill,安装不完整")
            return ABORTED
        id_pattern = skills_mod.default_case_id_pattern(doc_skill.get("doc-format"))
        module_scope = state.modules()
        ctx = PhaseContext(cfg, state, self.console)
        design_text = design_skill.get("case-design").full_text()
        doc_text = doc_skill.get("doc-format").full_text()

        for module in module_scope:
            if state.phase_flags.get(f"P2:{module}"):
                self.console(f"  模块 {module} 已有设计结果,跳过(重跑请删除对应标记)")
                continue
            module_endpoints = [ep for ep in state.endpoints
                                if (ep.get("module") or "COMMON").upper() == module]
            tools = (p2_tools(ctx, [module], id_pattern, cfg.project_code)
                     + make_code_tools([cfg.project_dir]))
            # 每批一个短会话:请求体只随本批历史重发,避免单会话历史二次膨胀(实测占总量 62%)
            idle_rounds = consecutive_failures = batch_rounds = 0
            while not state.phase_flags.get(f"P2:{module}"):
                batch_rounds += 1
                if batch_rounds > 80:
                    self.console(f"  [错误] 模块 {module} 批次会话超过上限(80),中止。")
                    return ABORTED
                agent = self._agent(allowed_builtin=["ls"])
                before = sum(1 for c in state.cases if c.get("module") == module)
                prompt = self._p2_batch_prompt(module, module_endpoints, design_text,
                                               doc_text, id_pattern, idle_rounds >= 1)
                result = await self._run_phase(agent, prompt, tools)
                after = sum(1 for c in state.cases if c.get("module") == module)
                self.console(f"  [批次 {batch_rounds}] 模块 {module}: 工具调用 {result.tool_calls},"
                             f"用例累计 {after} 条"
                             + ("" if result.ok else f";会话异常: {result.error[:100]}"))
                if state.phase_flags.get(f"P2:{module}"):
                    break
                if not result.ok:
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        self.console(f"  [错误] 模块 {module} 连续 {consecutive_failures} 个批次会话失败,中止。")
                        return ABORTED
                    continue
                consecutive_failures = 0
                if after == before:
                    idle_rounds += 1
                    if idle_rounds >= 4:
                        self.console(f"  [错误] 模块 {module} 连续 {idle_rounds} 轮未保存任何用例,中止。")
                        return ABORTED
                else:
                    idle_rounds = 0

        if not state.cases:
            self.console("  [错误] P2 结束但无用例保存")
            return ABORTED
        state.phase = "GATE2_REVIEW"
        self._write_case_docs()
        return None

    def _write_case_docs(self) -> None:
        cfg, state = self.cfg, self.state
        skills_by_type, _ = skills_mod.discover_skills(cfg.project_dir)
        doc_format = skills_by_type.get("doc-format")
        columns = docgen.DEFAULT_COLUMNS
        if doc_format and doc_format.machine.get("excel_columns"):
            columns = [str(c) for c in doc_format.machine["excel_columns"]]
        overview = {
            "project": cfg.project_code, "project_dir": cfg.project_dir,
            "requirement_source": state.requirement.get("source") or "全量模式",
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "design_skill": state.design_skill_name, "doc_skill": state.doc_skill_name,
        }
        xlsx = docgen.write_excel(state.cases, Path(cfg.output_dir) / "02_测试用例.xlsx",
                                  columns, overview, state.endpoints)
        md = docgen.write_markdown(state.cases, Path(cfg.output_dir) / "02_测试用例.md",
                                   columns, overview, state.endpoints)
        state.case_files = {"xlsx": str(xlsx), "md": str(md), "columns": columns}
        self.console(f"  用例文档已生成: {xlsx}")
        self.console(f"                {md}")

    # ---------------------------------------------------------- gate 2
    async def gate2(self) -> str:
        cfg, state = self.cfg, self.state
        self.console(f"[门禁2] 测试用例评审(共 {len(state.cases)} 条)")
        if state.cases_approved:
            state.cases_approved = False
            state.phase = "P3_CODEGEN"
            self.console("  已通过 resume 审批,继续脚本生成。")
            return None
        if cfg.non_interactive:
            return PAUSED
        while True:
            answer = input("  用例是否确认?[y=确认,生成自动化脚本 / e=我已修改 Excel/MD,回读 / n=放弃] ")
            answer = answer.strip().lower()
            if answer == "y":
                break
            if answer == "e":
                files = state.case_files or {}
                path = files.get("xlsx") or files.get("md")
                if not path or not Path(path).exists():
                    self.console("  找不到用例文档,无法回读")
                    continue
                columns = files.get("columns") or docgen.DEFAULT_COLUMNS
                merged, warnings = docgen.readback_cases(path, state.cases, columns)
                for warning in warnings[:20]:
                    self.console(f"  [回读] {warning}")
                state.cases = merged
                self._write_case_docs()
                self.console(f"  已回读 {len(merged)} 条用例,文档已同步刷新")
                state.save(cfg.state_file)
            elif answer == "n":
                self.console("  已放弃。")
                return ABORTED
        state.phase = "P3_CODEGEN"
        return None

    # ---------------------------------------------------------- P3
    async def p3_codegen(self) -> str:
        cfg, state = self.cfg, self.state
        self.console("[P3] 生成 pytest 自动化工程(确定性生成,无 LLM)")
        testframe_dir = Path(cfg.output_dir) / "testframe"
        result = generate_framework(testframe_dir, state.cases, cfg, self._state_dict())
        state.testframe_dir = str(testframe_dir)
        state.node_map = result["node_map"]
        for case in state.cases:
            case["pytest_node"] = state.node_map.get(case.get("case_id", ""), "")
        self._write_case_docs()
        manual = sum(1 for c in state.cases if c.get("manual"))
        self.console(f"  工程目录: {testframe_dir}")
        self.console(f"  模块: {', '.join(result['modules'])};自动化用例 {len(state.node_map)} 条"
                     + (f";手工用例 {manual} 条(不参与自动化)" if manual else ""))
        state.phase = "P4_SELFTEST"
        return None

    def _state_dict(self) -> dict:
        return {"endpoints": self.state.endpoints}

    # ---------------------------------------------------------- P4
    async def p4_selftest(self) -> str:
        cfg, state = self.cfg, self.state
        self.console("[P4] 自测:拉起服务并运行生成的测试")
        handle, message = runner.ensure_service(cfg)
        self.console(f"  {message}")
        if handle is None and "已由 Agent" not in message and "已在运行" not in message:
            self.console(f"  [错误] {message}")
            state.selftest = {"status": "service_error", "message": message}
            return ABORTED
        self.service_handle = handle

        summary = runner.run_pytest(state.testframe_dir)
        self._log_pytest("  第 1 轮", summary)
        rounds = 0
        while not summary["ok"] and rounds < cfg.max_fix_rounds:
            rounds += 1
            self.console(f"  [修复轮 {rounds}/{cfg.max_fix_rounds}] 调用 LLM 归因修复(pi session 启动)")
            fix = await self._fix_round(summary)
            if not fix:
                self.console("  [警告] 修复阶段未产生变化,提前结束修复循环")
                break
            summary = runner.run_pytest(state.testframe_dir)
            self._log_pytest(f"  第 {rounds + 1} 轮", summary)

        state.selftest = {
            "status": "passed" if summary["ok"] else "failed",
            "rounds": rounds, "passed": summary["passed"], "failed": summary["failed"],
            "total": summary["total"], "defects": state.selftest.get("defects", []),
        }
        self._fill_exec_results(summary)
        self._write_case_docs()
        if self.service_handle:
            self.service_handle.stop()
            self.service_handle = None
        state.phase = "P5_REPORT"
        return None

    async def _fix_round(self, summary: dict) -> bool:
        cfg, state = self.cfg, self.state
        case_by_id = {c.get("case_id"): c for c in state.cases}
        failures = []
        for failure in summary["failures"]:
            case = case_by_id.get(failure.get("case_id"), {})
            failures.append({
                "case_id": failure.get("case_id"), "nodeid": failure.get("nodeid"),
                "title": case.get("title", ""),
                "request": case.get("request"),
                "expected_spec": case.get("expected_spec"),
                "error": failure.get("message", "")[-1200:],
            })
        ctx = PhaseContext(cfg, state, self.console)
        agent = self._agent(allowed_builtin=["ls"])
        prompt = f"""你是接口自动化测试 Agent 的「自测修复」阶段(P4)。被测服务可能存在真实缺陷,归因必须谨慎。

pytest 运行失败详情(JSON):
{json.dumps(failures, ensure_ascii=False, indent=1)}

响应约定:
{state.response_convention}

归因三分类与处理:
1) 脚本/框架缺陷 → 调用 fix_script_file 修复生成工程内的文件(只允许该工程内路径)。
2) 用例期望错误(与响应约定/被测代码实际行为不符)→ 调用 patch_case_data 修正期望。
3) 疑似被测系统缺陷(代码行为与业务规则矛盾)→ 调用 report_defect 上报,严禁通过改脚本或期望让其通过。
你可以用 read_file/grep_code/ls 查看被测项目代码({cfg.project_dir})与生成的测试工程核实行为后再归因。

修复纪律(必须遵守):
- 小步快改:优先 patch_case_data(参数小);改文件时单次 content ≤150 行,更大分块写(首块 mode='replace',后续 mode='append')。
- 禁止长时间只读分析:定位到一个失败的原因后立即提交对应修复,再分析下一个。
- 每条失败逐一处理;全部处理完调用 finish_phase 结束。"""
        tools = (p4_tools(ctx, Path(state.testframe_dir))
                 + make_code_tools([cfg.project_dir, state.testframe_dir]))
        before = state.selftest.get("defects", []), state.stats.get("tool_calls", 0)
        result = await self._run_phase(agent, prompt, tools,
                                       continue_check=lambda: self._p4_nudge(),
                                       max_continuations=15)
        if not result.ok:
            self.console(f"  [警告] 修复会话异常: {result.error}")
        after = state.selftest.get("defects", []), state.stats.get("tool_calls", 0)
        return before != after

    def _fill_exec_results(self, summary: dict) -> None:
        failed_ids = {f.get("case_id") for f in summary["failures"]}
        for case in self.state.cases:
            cid = case.get("case_id")
            if case.get("manual"):
                case["exec_status"] = "手工"
                continue
            if summary["total"] == 0:
                case["exec_status"] = "未执行"
                continue
            case["exec_status"] = "失败" if cid in failed_ids else "通过"
            case["actual_result"] = ("断言失败,详见测试报告" if cid in failed_ids
                                     else "自动化执行通过" if cid else "")

    def _log_pytest(self, prefix: str, summary: dict) -> None:
        self.console(f"{prefix}: 总数 {summary['total']},通过 {summary['passed']},"
                     f"失败 {summary['failed']},错误 {summary['errors']}")
        for failure in summary["failures"][:8]:
            first_line = (failure.get("message") or "").strip().splitlines()
            self.console(f"    [失败] {failure.get('case_id') or failure.get('nodeid')}: "
                         f"{first_line[0] if first_line else '详见报告'}")

    # ---------------------------------------------------------- P5
    async def p5_report(self) -> str:
        cfg, state = self.cfg, self.state
        self.console("[P5] 生成测试报告与总结")
        results_dir = Path(state.testframe_dir) / "allure-results"
        report_dir = Path(cfg.output_dir) / "allure-report"
        allure_bin = runner.find_allure(cfg)
        if allure_bin and results_dir.exists() and any(results_dir.iterdir()):
            ok, output = runner.generate_allure_report(allure_bin, results_dir, report_dir)
            state.report_dir = str(report_dir) if ok else ""
            self.console(f"  Allure HTML 报告: {report_dir / 'index.html'}" if ok
                         else f"  [警告] allure 报告生成失败: {output[:200]}")
        else:
            self.console("  [警告] allure 不可用或无结果数据,跳过 HTML 报告"
                         "(allure-results 目录已保留,可手工执行 allure generate)")
        self._write_summary()
        state.phase = "DONE"
        self.console(f"[完成] 全部产物见输出目录: {cfg.output_dir}")
        return DONE

    def _write_summary(self) -> None:
        cfg, state = self.cfg, self.state
        selftest = state.selftest or {}
        lines = [
            f"# 执行总结 — {cfg.project_code}", "",
            f"- 会话:`{state.session_id}`;生成时间:{datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
            f"- 需求来源:{state.requirement.get('source') or '全量模式'}",
            f"- 接口:{len(state.endpoints)} 个;用例:{len(state.cases)} 条"
            f"(自动化 {len(state.node_map)} 条,手工 {sum(1 for c in state.cases if c.get('manual'))} 条)",
            f"- 自测:状态 **{selftest.get('status', '-')}**,修复轮数 {selftest.get('rounds', 0)},"
            f"通过 {selftest.get('passed', 0)}/{selftest.get('total', 0)}",
            f"- Allure 报告:{state.report_dir or '(未生成)'}",
            f"- 设计 Skill:{state.design_skill_name};文档 Skill:{state.doc_skill_name}",
            "", "## 产物清单", "",
        ]
        for name in ("01_接口清单与需求映射.md", "02_测试用例.xlsx", "02_测试用例.md",
                     "testframe", "allure-report"):
            path = Path(cfg.output_dir) / name
            if path.exists():
                lines.append(f"- {path}")
        defects = selftest.get("defects") or []
        if defects:
            lines += ["", "## 疑似被测系统缺陷(未修改,需人工确认)", ""]
            for defect in defects:
                lines.append(f"- **{defect.get('case_id')}**: {defect.get('analysis')}"
                             + (f"(证据: {defect.get('evidence')})" if defect.get("evidence") else ""))
        path = Path(cfg.output_dir) / "00_总结.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        state.summary_file = str(path)

    async def p_done(self) -> str:
        self.console("[会话已完成] 如需重新生成,删除输出目录后再次运行。")
        return DONE

    # ---------------------------------------------------------- helpers
    def _agent(self, allowed_builtin: list[str]) -> PhaseAgent:
        cfg = self.cfg
        cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state.stats["llm_sessions"] = self.state.stats.get("llm_sessions", 0) + 1
        return PhaseAgent(
            project_dir=cfg.project_dir, provider=cfg.llm.provider,
            model_id=cfg.llm.model_id, base_url=cfg.llm.base_url, api_key=cfg.llm.api_key,
            context_window=cfg.llm.context_window, max_tokens=cfg.llm.max_tokens,
            allowed_builtin=allowed_builtin, timeout_seconds=cfg.phase_timeout_seconds,
            verbose=cfg.verbose, extra_body=dict(cfg.llm.extra_body),
            on_progress=lambda message: self.console(message),
        )

    def _p1_nudge(self, hint: int) -> str:
        state = self.state
        if state.phase_flags.get("P1"):
            return ""
        registered = len(state.endpoints)
        if not state.endpoints or not state.response_convention:
            return ("你还没有完成 P1:必须调用 record_endpoints 登记接口,"
                    "并调用 record_response_convention 记录响应约定,然后调用 finish_phase。")
        return (f"你登记了 {registered} 个接口,但启发式统计显示项目约有 {hint} 处路由定义,差距过大。"
                "请用 grep_code 搜索路由注解/装饰器、用 read_file 打开命中的 controller/route 文件,"
                "把全部对外接口补齐到 record_endpoints(每项带 evidence 源码行原文),"
                "然后调用 finish_phase。")

    def _p2_batch_prompt(self, module: str, module_endpoints: list[dict], design_text: str,
                         doc_text: str, id_pattern: str, idle: bool) -> str:
        cfg, state = self.cfg, self.state
        endpoints_json = json.dumps(
            [{k: ep.get(k) for k in ("method", "path", "module", "module_name", "summary",
                                     "description", "auth_required", "params")}
             for ep in module_endpoints], ensure_ascii=False, indent=1)
        account = cfg.service.test_account or {}
        login = cfg.service.login or {}
        saved = [c for c in state.cases if (c.get("module") or "").upper() == module]
        coverage: dict[str, int] = {}
        for case in saved:
            request = case.get("request") or {}
            key = f"{request.get('method', '')} {request.get('path', '')}"
            coverage[key] = coverage.get(key, 0) + 1
        coverage_text = "\n".join(f"- {key}: {count} 条" for key, count in coverage.items()) or "(尚无)"
        titles_text = "\n".join(
            f"- {c.get('case_id')} {c.get('title', '')}"
            f"[{c.get('priority', '')}|{c.get('design_method', '')}]" for c in saved) or "(尚无)"
        gaps = state.p2_gaps.get(module) or state.p2_gaps.get(module.upper()) or []
        gap_block = ("\n\n上一轮 finish_phase 校验发现的缺口(本轮优先补齐):\n- " + "\n- ".join(gaps)
                     ) if gaps else ""
        idle_block = ("\n\n警告:上一轮会话没有保存任何用例。本轮禁止只做分析,"
                      "读完必要代码后立即调用 save_module_cases。") if idle else ""
        # 数量护栏:用例数到达上限后转为收尾,防止模型无限加用例(实测单接口被设计 20+ 条)
        case_cap = max(10, 6 * len(module_endpoints))
        if len(saved) >= case_cap and not gaps:
            task_line = (f"本模块已保存 {len(saved)} 条用例,数量已充足(上限 {case_cap})。"
                         f"本轮禁止再设计新用例,直接调用 finish_phase(module_scope 已限定为本模块)结束;"
                         f"若 finish_phase 报出校验缺口,按缺口最小限度补齐后再次 finish_phase。")
        else:
            task_line = (f"本轮任务:设计并提交模块 {module} 的下一批 2-3 条用例,优先选择尚未覆盖的接口"
                         f"和尚未覆盖的等价类/边界值/场景。调用一次 save_module_cases(module=\"{module}\", "
                         f"cases=...) 提交(单次严禁超过 5 条,case_id 留空)。若你确认本模块所有接口与 "
                         f"P0 负向场景已全覆盖,改为调用 finish_phase(系统会做覆盖校验,不通过会列出缺口)。"
                         f"提交成功后立即结束回复,只输出一句\"已提交,累计 N 条\",禁止长篇分析或罗列用例。")
        return f"""你是接口自动化测试 Agent 的「用例设计」阶段(P2)。这是一个短会话批次任务,只负责模块 {module} 的下一小批用例。禁止修改被测项目文件,只允许调用保存工具。

本模块的接口(用例范围基线):
{endpoints_json}

项目响应约定(用例预期必须与之匹配):
{state.response_convention}

可用测试账号(设计登录成功/鉴权用例时必须使用这组真实数据):
username: {account.get('username', '')}
password: {account.get('password', '')}
登录接口: {login.get('path', '(无)')},token 字段: {login.get('token_field', 'data.token')}

可用工具 read_file/grep_code/ls 查看被测代码理解业务规则(read_file 大文件分页返回)。

模块进度:
已覆盖接口(用例数):
{coverage_text}
已保存用例(禁止重复设计相同场景;可补充其未覆盖的等价类/边界值/异常分支):
{titles_text}{gap_block}{idle_block}

{task_line}

=== 用例设计规范 Skill(必须遵循) ===
{design_text}

=== 用例文档规范 Skill(编号等机读规则必须遵循) ===
{doc_text}
编号正则(机读校验): {id_pattern}
项目码: {cfg.project_code}(case_id 一律留空,由系统自动按 项目码-模块-序号 连续编号,禁止自己编号)

用例要求:
1. 每条用例的 request 必须可直接执行: 真实参数值、真实业务数据;query/json 为对象;auth 取 true(带有效token)/"invalid"(无效token)/false(不携带)。
2. expected_spec 必须与响应约定一致。成功用例断言关键业务字段(json_paths);错误用例断言状态码与业务码。
3. 需要鉴权的接口至少包含: 未携带token(false)、无效token("invalid")两条负向用例。
4. 场景法用例(多步)用 scenario_steps,支持 "$stepN.data.token" 引用前序步骤响应字段。
5. 保存工具会做用例级校验,校验失败按错误信息修正重提。"""

    def _p4_nudge(self) -> str:
        if self.state.phase_flags.get("P4"):
            return ""
        return ("修复阶段尚未结束:请继续逐条处理剩余失败(先归因再修复,禁止只分析不动手),"
                "全部处理完后调用 finish_phase 结束。")

    async def _run_phase(self, agent: PhaseAgent, prompt: str, tools,
                         continue_check=None, max_continuations: int = 3) -> object:
        return await agent.run(prompt, tools, continue_check=continue_check,
                               max_continuations=max_continuations)
