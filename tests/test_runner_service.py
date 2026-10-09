# 单元测试:ensure_service 的输出落盘 / 瞬态失败重试 / 正常启停
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_test_agent import runner


def _cfg(tmp_path, app_cmd, port):
    return SimpleNamespace(
        project_dir=str(tmp_path),
        output_dir=str(tmp_path / "out"),
        service=SimpleNamespace(base_url=f"http://127.0.0.1:{port}",
                                health_path="/health", app_cmd=app_cmd),
    )


def test_start_failure_is_logged_and_retried(tmp_path):
    cfg = _cfg(tmp_path, f'"{sys.executable}" -c "import sys; sys.exit(3)"', 39871)
    handle, message = runner.ensure_service(cfg)
    assert handle is None
    assert "退出(码 3)" in message and "service.log" in message
    log = (tmp_path / "out" / "service.log").read_text(encoding="utf-8")
    # 瞬态退出自动重试一次,两次尝试都记录在案
    assert log.count("服务启动尝试") == 2


def test_start_success_and_stop(tmp_path):
    server_py = tmp_path / "mini_server.py"
    server_py.write_text(
        "import http.server, sys\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200)\n"
        "        self.end_headers()\n"
        "        self.wfile.write(b'ok')\n"
        "    def log_message(self, *a):\n"
        "        pass\n"
        "http.server.HTTPServer(('127.0.0.1', 39872), H).serve_forever()\n",
        encoding="utf-8")
    cfg = _cfg(tmp_path, f'"{sys.executable}" "{server_py}"', 39872)
    handle, message = runner.ensure_service(cfg)
    assert handle is not None and "已由 Agent 启动服务" in message
    handle.stop()


def test_no_app_cmd_message(tmp_path):
    cfg = _cfg(tmp_path, "", 39873)
    handle, message = runner.ensure_service(cfg)
    assert handle is None and "未配置启动命令" in message
