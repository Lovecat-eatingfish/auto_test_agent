"""Service lifecycle (ensure the target API is up) and pytest/allure execution."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests


# ---------------------------------------------------------------- service
class ServiceHandle:
    def __init__(self):
        self.process: subprocess.Popen | None = None

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            if os.name == "nt":
                # shell=True 时直接 terminate 杀不掉子进程树,用 taskkill /T
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                               capture_output=True)
            else:
                self.process.terminate()
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.process.kill()


def _probe(base_url: str, health_path: str) -> bool:
    for path in (health_path, "/"):
        try:
            resp = requests.get(base_url.rstrip("/") + path, timeout=3)
            if resp.status_code < 500:
                return True
        except requests.RequestException:
            continue
    return False


def _service_log_tail(log_file: Path, max_chars: int = 400) -> str:
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    tail = "\n".join(text.splitlines()[-8:])
    return tail[-max_chars:]


def _start_service_once(cfg, log_file: Path, attempt: int) -> tuple[ServiceHandle, str | None]:
    """启动一次服务并等待就绪。返回 (句柄, 错误消息);错误为 None 表示已就绪。"""
    handle = ServiceHandle()
    with open(log_file, "a", encoding="utf-8") as out:
        out.write(f"\n===== 服务启动尝试 {attempt}({time.strftime('%Y-%m-%d %H:%M:%S')})=====\n")
        out.write(f"[diag] child cwd={cfg.project_dir!r} exists={Path(cfg.project_dir).is_dir()} "
                  f"proc cwd={os.getcwd()!r} cmd={cfg.service.app_cmd!r}\n")
        out.flush()
        handle.process = subprocess.Popen(
            cfg.service.app_cmd, shell=True, cwd=cfg.project_dir,
            stdout=out, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        deadline = time.time() + 40
        while time.time() < deadline:
            if _probe(cfg.service.base_url.rstrip("/"), cfg.service.health_path):
                return handle, None
            if handle.process.poll() is not None:
                return handle, f"服务启动命令退出(码 {handle.process.returncode})"
            time.sleep(1)
    handle.stop()
    return handle, "服务启动超时(40s)"


def ensure_service(cfg) -> tuple[ServiceHandle | None, str]:
    """返回 (句柄, 消息)。句柄非空表示服务由 Agent 拉起,结束时需要 stop。"""
    base_url = cfg.service.base_url.rstrip("/")
    if not base_url:
        return None, "未配置被测服务地址(service.base_url)"
    if _probe(base_url, cfg.service.health_path):
        return None, f"服务已在运行: {base_url}"
    if not cfg.service.app_cmd:
        return None, (f"服务未运行且未配置启动命令(service.app_cmd): {base_url}。"
                      f"请先启动被测服务,或提供 base_url/app_cmd 配置。")
    # 服务输出落盘(不再丢弃到 DEVNULL):启动失败时能直接看到真实报错
    log_file = Path(cfg.output_dir) / "service.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    message = ""
    for attempt in (1, 2):
        handle, error = _start_service_once(cfg, log_file, attempt)
        if error is None:
            return handle, f"已由 Agent 启动服务: {base_url}(app_cmd)"
        message = (f"{error}: {cfg.service.app_cmd}。服务输出见 {log_file},末尾:\n"
                   f"{_service_log_tail(log_file)}")
        if attempt == 1 and "退出" in error:
            # 实测出现过一次性的启动瞬态失败;先清理残留再重试一次
            handle.stop()
            time.sleep(2)
            continue
        break
    return None, message


# ---------------------------------------------------------------- pytest
def run_pytest(testframe_dir: str | Path, pytest_exe: str | None = None) -> dict:
    testframe_dir = Path(testframe_dir)
    report_file = testframe_dir / ".ata_pytest_report.json"
    cmd = [pytest_exe or sys.executable, "-m", "pytest", "--json-report",
           f"--json-report-file={report_file}"]
    proc = subprocess.run(cmd, cwd=testframe_dir, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if not report_file.exists():
        return {"ok": False, "total": 0, "passed": 0, "failed": 0, "errors": 1,
                "failures": [],
                "message": f"pytest 未产出报告(退出码 {proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-1000:]}"}
    report = json.loads(report_file.read_text(encoding="utf-8"))
    tests = report.get("tests", [])
    failures = []
    for test in tests:
        outcome = test.get("outcome")
        if outcome == "passed":
            continue
        nodeid = test.get("nodeid", "")
        case_id = nodeid.rsplit("[", 1)[-1].rstrip("]") if "[" in nodeid else ""
        call = test.get("call") or {}
        longrepr = str(call.get("longrepr", ""))[-1500:]
        failures.append({"nodeid": nodeid, "case_id": case_id,
                         "outcome": outcome, "message": longrepr})
    summary = report.get("summary", {})
    return {"ok": summary.get("failed", 0) == 0 and summary.get("error", 0) == 0,
            "total": summary.get("total", len(tests)),
            "passed": summary.get("passed", 0),
            "failed": summary.get("failed", 0),
            "errors": summary.get("error", 0),
            "failures": failures, "message": proc.stdout[-2000:]}


# ---------------------------------------------------------------- allure
def find_allure(cfg) -> str | None:
    """PATH → cfg.allure_home → 常见默认安装路径。返回可执行文件路径或 None。"""
    candidates = []
    found = shutil.which("allure") or shutil.which("allure.bat")
    if found:
        return found
    if cfg.allure_home:
        base = Path(cfg.allure_home)
        candidates += [base / "bin" / "allure.bat", base / "bin" / "allure",
                       base / "allure.bat", base / "allure"]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def generate_allure_report(allure_bin: str, results_dir: str | Path,
                           report_dir: str | Path) -> tuple[bool, str]:
    report_dir = Path(report_dir)
    cmd = [allure_bin, "generate", str(results_dir), "-o", str(report_dir), "--clean"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", shell=False)
    ok = proc.returncode == 0 and (report_dir / "index.html").exists()
    return ok, (proc.stdout + proc.stderr)[-800:]
