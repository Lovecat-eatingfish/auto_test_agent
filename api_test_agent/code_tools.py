"""带大小上限的代码阅读工具(read_file / grep_code),替代 SDK 内置 read/grep。

内置 read/grep 会把整个文件/全部命中行原样写入会话历史,而历史在后续每一轮
请求中都会整体重发,是多轮会话输入 token 膨胀的主因(实测占请求体近半)。
自定义版本按字符上限截断并给出续读提示;只允许读取 allowed_roots 内的路径。
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from api_test_agent.pi_agent import make_tool, text_tool_result

READ_CAP_CHARS = 4000
READ_MAX_LINES = 200
GREP_CAP_CHARS = 6000
GREP_MAX_HITS = 40
_SKIP_DIRS = {".git", ".svn", ".idea", ".venv", "venv", "node_modules", "__pycache__",
              "target", "build", "dist", ".gradle", "allure-results", "allure-report"}
_TEXT_SUFFIXES = {".java", ".py", ".go", ".js", ".ts", ".xml", ".yml", ".yaml", ".properties",
                  ".sql", ".md", ".json", ".txt", ".html", ".css", ".cfg", ".ini", ".toml",
                  ".kt", ".scala", ".groovy", ".sh", ".bat", ".cs", ".rb", ".php", ".c", ".h",
                  ".cpp", ".hpp", ".rs"}


def _resolve(roots: list[Path], rel: str) -> Path | None:
    """把工具传入的相对/绝对路径解析到 allowed_roots 之内,防越界。"""
    text = str(rel or "").strip().strip('"')
    if not text:
        return None
    for root in roots:
        candidate = (root / text).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if candidate.exists():
            return candidate
    # 绝对路径或带 ../ 的写法:直接尝试解析后校验
    try:
        candidate = Path(text).resolve()
        for root in roots:
            candidate.relative_to(root.resolve())
            return candidate
    except (ValueError, OSError):
        return None
    return None


def _iter_files(root: Path, pattern: str | None):
    if root.is_file():
        yield root
        return
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _SKIP_DIRS:
                    stack.append(entry)
                continue
            if pattern and not fnmatch.fnmatch(entry.name, pattern):
                continue
            suffix = entry.suffix.lower()
            if suffix and suffix not in _TEXT_SUFFIXES:
                continue
            try:
                if entry.stat().st_size > 1_000_000:
                    continue
            except OSError:
                continue
            yield entry


def make_code_tools(roots: list[str | Path]) -> list:
    """构造 read_file / grep_code 两个工具。roots 为允许读取的目录。"""
    allowed = [Path(r) for r in roots]

    async def read_file(tool_call_id, args, cancel, update, tool_ctx):
        rel = str(args.get("path", ""))
        offset = args.get("offset") or 1
        try:
            offset = max(1, int(offset))
        except (TypeError, ValueError):
            offset = 1
        target = _resolve(allowed, rel)
        if target is None:
            roots_text = ", ".join(str(r) for r in allowed)
            return text_tool_result(f"错误:路径不在允许范围内或不存在: {rel!r}(允许的根目录: {roots_text})")
        if not target.is_file():
            return text_tool_result(f"错误:不是文件: {rel}")
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return text_tool_result(f"错误:读取失败: {exc}")
        total = len(lines)
        if total and offset > total:
            return text_tool_result(f"{target}(共 {total} 行)已无更多内容:请求的 offset={offset} 超出范围。")
        picked: list[str] = []
        used = 0
        index = offset - 1
        while index < total and len(picked) < READ_MAX_LINES:
            line = lines[index]
            if used + len(line) + 1 > READ_CAP_CHARS and picked:
                break
            picked.append(line)
            used += len(line) + 1
            index += 1
        end_line = offset + len(picked) - 1
        rel_display = str(target)
        header = f"{rel_display}(共 {total} 行)第 {offset}-{end_line} 行:"
        body = "\n".join(picked)
        if index < total:
            body += (f"\n…(已按上限截断;续读请调用 read_file, path 同前, offset={end_line + 1})")
        return text_tool_result(f"{header}\n{body}")

    async def grep_code(tool_call_id, args, cancel, update, tool_ctx):
        pattern = str(args.get("pattern", "")).strip()
        glob = str(args.get("glob", "") or "").strip()
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return text_tool_result(f"错误:正则不合法: {exc}")
        hits: list[str] = []
        used = 0
        truncated = False
        for root in allowed:
            for path in _iter_files(root, None):
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if glob and not fnmatch.fnmatch(str(path.relative_to(root)).replace("\\", "/"), glob) \
                        and not fnmatch.fnmatch(path.name, glob):
                    continue
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if not regex.search(line):
                        continue
                    rel_display = str(path)
                    entry = f"{rel_display}:{lineno}: {line.strip()[:200]}"
                    if used + len(entry) + 1 > GREP_CAP_CHARS or len(hits) >= GREP_MAX_HITS:
                        truncated = True
                        break
                    hits.append(entry)
                    used += len(entry) + 1
                if truncated:
                    break
            if truncated:
                break
        if not hits:
            return text_tool_result(f"无命中(pattern={pattern!r})。提示:缩小 pattern 或检查 glob 过滤。")
        note = (f"\n…(命中过多已截断,仅显示前 {len(hits)} 条;请用更精确的 pattern 或 glob 继续搜索)"
                if truncated else "")
        return text_tool_result(f"共 {len(hits)} 条命中{'(可能有更多)' if truncated else ''}:\n"
                                + "\n".join(hits) + note)

    return [
        make_tool("read_file",
                  "读取允许目录内的文本文件,按行分页,单次最多约 4000 字符。"
                  "args: path(相对允许根目录的路径), offset(起始行,默认 1)。大文件请用 offset 续读。",
                  {"type": "object",
                   "properties": {"path": {"type": "string"}, "offset": {"type": "integer"}},
                   "required": ["path"]},
                  read_file),
        make_tool("grep_code",
                  "在允许目录内按正则搜索文本文件,返回 file:line: 内容 列表,最多 40 条/约 6000 字符。"
                  "args: pattern(正则), glob(可选文件名过滤,如 *.java)。",
                  {"type": "object",
                   "properties": {"pattern": {"type": "string"}, "glob": {"type": "string"}},
                   "required": ["pattern"]},
                  grep_code),
    ]
