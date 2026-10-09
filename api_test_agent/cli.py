"""api-test-agent CLI entry."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from api_test_agent import skills as skills_mod
from api_test_agent.config import AgentConfig
from api_test_agent.pipeline import Pipeline
from api_test_agent.requirements_input import supported_formats

BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def console(message: str) -> None:
    print(message, flush=True)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project_dir", help="被测项目目录(或其子模块目录)")
    parser.add_argument("--config", help="agent.yaml 配置文件路径")
    parser.add_argument("--out", dest="output_dir", help="产物输出目录(默认 <项目>../ata_output/<项目名>)")
    parser.add_argument("--project-code", help="用例编号的项目码(默认取项目名大写)")
    parser.add_argument("--requirement-text", help="版本需求文本(迭代模式入口)")
    parser.add_argument("--requirement", help="需求文件(按扩展名分发加载器;v1 支持 txt/md)")
    parser.add_argument("--base-url", help="被测服务地址,如 http://127.0.0.1:8100")
    parser.add_argument("--app-cmd", help="被测服务启动命令(Agent 自动拉起,结束时停止)")
    parser.add_argument("--allure-home", help="allure 安装目录(如 D:\\...\\allure-2.46.1)")
    parser.add_argument("--non-interactive", action="store_true",
                        help="非交互模式:评审门禁处暂停退出,用 resume 继续")
    parser.add_argument("--verbose", action="store_true", help="输出详细日志")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="api-test-agent",
        description="接口自动化测试 Agent:扫描接口 → 生成用例(人工评审)→ 生成 pytest 工程 → "
                    "自测修复 → Allure 报告")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="启动全流程")
    _add_common_args(run_p)

    resume_p = sub.add_parser("resume", help="从评审门禁处继续")
    resume_p.add_argument("session_id", nargs="?", help="会话 id(默认取输出目录下最新会话)")
    resume_p.add_argument("--out", dest="output_dir", help="输出目录(与 run 时一致)")
    resume_p.add_argument("--config", help="agent.yaml 配置文件路径")
    resume_p.add_argument("--approve-interfaces", action="store_true",
                          help="跳过门禁1(接口清单已确认)")
    resume_p.add_argument("--approve-cases", action="store_true",
                          help="跳过门禁2(用例已确认,以当前文档为基线)")
    resume_p.add_argument("--non-interactive", action="store_true")
    resume_p.add_argument("--verbose", action="store_true")

    skills_p = sub.add_parser("skills", help="查看/初始化自定义 Skill")
    skills_p.add_argument("action", choices=["list", "init"])
    skills_p.add_argument("project_dir", nargs="?", default=".",
                          help="项目目录(list 查看 / init 导出默认 Skill 到其 .api-test-agent/skills)")

    env_p = sub.add_parser("check-env", help="环境自检")
    env_p.add_argument("--config", help="agent.yaml 配置文件路径")
    return parser


def _latest_state(cfg: AgentConfig) -> Path | None:
    state_file = cfg.state_file
    if state_file.exists():
        return state_file
    return None


def cmd_run(args) -> int:
    import asyncio

    overrides = {k: getattr(args, k, None) for k in
                 ("project_dir", "output_dir", "project_code", "requirement_text",
                  "requirement", "base_url", "app_cmd", "allure_home",
                  "non_interactive", "verbose")}
    cfg = AgentConfig.load(args.config, overrides)
    if cfg.non_interactive and not (cfg.requirement_text or cfg.requirement_file):
        console("[提示] 非交互且未提供需求:将以全量模式运行。")
    return asyncio.run(Pipeline(cfg, console).run())


def cmd_resume(args) -> int:
    overrides = {k: getattr(args, k, None) for k in
                 ("project_dir", "output_dir", "non_interactive", "verbose")}
    cfg = AgentConfig.load(args.config, overrides)
    state_file = _latest_state(cfg)
    if state_file is None:
        console(f"[错误] 找不到会话状态: {cfg.state_file}。请在与 run 相同的 --out 下执行。")
        return 2
    import asyncio

    return asyncio.run(Pipeline(cfg, console).run(
        resume=True, approve_interfaces=args.approve_interfaces,
        approve_cases=args.approve_cases))


def cmd_skills(args) -> int:
    if args.action == "list":
        found, warnings = skills_mod.discover_skills(args.project_dir)
        for warning in warnings:
            print(f"[警告] {warning}")
        for skill_type in ("case-design", "doc-format"):
            skill = found.get(skill_type)
            if skill:
                print(f"[{skill_type}] {skill.name}({skill.level}) — {skill.path}")
            else:
                print(f"[{skill_type}] (未找到)")
        return 0
    target = Path(args.project_dir) / ".api-test-agent" / "skills"
    copied = 0
    for skill_dir in BUILTIN_SKILLS_DIR.iterdir():
        if (skill_dir / "SKILL.md").exists():
            dest = target / skill_dir.name
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy(skill_dir / "SKILL.md", dest / "SKILL.md")
            copied += 1
    print(f"已导出 {copied} 个默认 Skill 到 {target};编辑后重新运行即可生效。")
    return 0


def cmd_check_env(args) -> int:
    cfg = AgentConfig.load(args.config, {})
    checks = []
    checks.append(("python>=3.11", sys.version_info >= (3, 11), sys.version.split()[0]))
    node = shutil.which("node") or shutil.which("bun")
    checks.append(("node/bun", bool(node), node or "未找到"))
    llm_ok = cfg.llm.is_configured()
    checks.append(("LLM(base_url/key/model)", llm_ok,
                   cfg.llm.base_url or "ATA_LLM_BASE_URL 等环境变量或 agent.yaml"))
    allure = shutil.which("allure") or shutil.which("allure.bat")
    if not allure and cfg.allure_home:
        base = Path(cfg.allure_home)
        for candidate in (base / "bin" / "allure.bat", base / "bin" / "allure"):
            if candidate.exists():
                allure = str(candidate)
                break
    checks.append(("allure", bool(allure), allure or
                   "未找到(不影响用例生成;报告阶段需要,可配置 allure_home)"))
    failed = 0
    for name, ok, detail in checks:
        mark = "✓" if ok else ("!" if name == "allure" and not ok else "✗")
        if not ok and mark == "✗":
            failed += 1
        print(f"  [{mark}] {name}: {detail}")
    print(f"需求输入支持: {supported_formats()}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {"run": cmd_run, "resume": cmd_resume, "skills": cmd_skills,
                "check-env": cmd_check_env}
    code = handlers[args.command](args)
    # pi SDK 的 node worker 可能残留在事件循环的子进程监视器里导致 asyncio.run 收尾挂死;
    # 流水线状态全部落盘,这里直接硬退出
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code if isinstance(code, int) else 0)


if __name__ == "__main__":
    sys.exit(main())
