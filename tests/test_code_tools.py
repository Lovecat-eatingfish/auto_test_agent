# 单元测试:code_tools 的路径护栏 / 分页 / 截断 / glob
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_test_agent.code_tools import make_code_tools

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def tools(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "\n".join(f"line {i}: x = {i}" for i in range(1, 51)), encoding="utf-8")
    (tmp_path / "src" / "a.java").write_text("class A {\n  @GetMapping(\"/x\")\n}\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    return make_code_tools([tmp_path / "src"]), tmp_path


def _run(coro):
    return asyncio.run(coro)


def _text(tool, args):
    result = _run(tool.execute("t1", args, None, None, None))
    return "".join(b.text for b in result.content)


def test_tool_names(tools):
    made, _ = tools
    assert [t.name for t in made] == ["read_file", "grep_code"]


def test_read_file_pages_and_caps(tools):
    made, tmp = tools
    read_file = made[0]
    out = _text(read_file, {"path": "app.py"})
    assert "共 50 行" in out and "line 1" in out
    assert len(out) < 5000
    out = _text(read_file, {"path": "app.py", "offset": 45})
    assert "第 45-" in out and "line 50" in out


def test_read_file_rejects_outside_root(tools):
    made, tmp = tools
    out = _text(made[0], {"path": "../secret.txt"})
    assert "错误" in out
    out = _text(made[0], {"path": str(tmp / "secret.txt")})
    assert "错误" in out


def test_read_file_offset_overflow(tools):
    made, _ = tools
    out = _text(made[0], {"path": "app.py", "offset": 999})
    assert "超出范围" in out


def test_grep_code_hits_and_glob(tools):
    made, _ = tools
    grep_code = made[1]
    out = _text(grep_code, {"pattern": "GetMapping", "glob": "*.java"})
    assert "a.java" in out and "GetMapping" in out
    out = _text(grep_code, {"pattern": "GetMapping", "glob": "*.py"})
    assert "无命中" in out


def test_grep_code_bad_regex(tools):
    made, _ = tools
    out = _text(made[1], {"pattern": "["})
    assert "正则不合法" in out


def test_grep_code_skips_secret_dir(tools):
    # secret.txt 在根外(src 之外),不应出现
    made, _ = tools
    out = _text(made[1], {"pattern": "secret"})
    assert "无命中" in out
