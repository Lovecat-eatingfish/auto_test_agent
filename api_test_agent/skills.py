"""User-defined skill system: discovery, front-matter parsing, override merging.

Skill layout (each skill is a directory containing SKILL.md):
    <project>/.api-test-agent/skills/<name>/SKILL.md   (项目级,最高优先)
    ~/.api-test-agent/skills/<name>/SKILL.md           (用户全局级)
    <package>/skills/<name>/SKILL.md                   (内置默认,可被覆盖)

SKILL.md = YAML front matter (--- ... ---) + Markdown 规范正文。
front matter 中小写下划线键为机读字段(如 case_id_pattern / excel_columns),
由本地校验器与文档生成器强制执行;正文由 LLM 遵循。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SKILL_TYPE_DESIGN = "case-design"
SKILL_TYPE_DOC = "doc-format"
VALID_TYPES = (SKILL_TYPE_DESIGN, SKILL_TYPE_DOC)

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


@dataclass
class SkillDoc:
    name: str
    skill_type: str
    description: str = ""
    machine: dict = field(default_factory=dict)   # 机读字段
    body: str = ""                                # 规范正文(markdown)
    path: str = ""                                # 来源文件
    level: str = "builtin"                        # project | user | builtin

    def full_text(self) -> str:
        """注入 LLM 提示的完整文本。"""
        return f"# Skill: {self.name} ({self.skill_type})\n\n{self.body}".strip()


def parse_skill_md(path: Path, level: str) -> SkillDoc:
    raw = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER_RE.match(raw)
    if not match:
        raise ValueError(f"SKILL.md 缺少 YAML front matter: {path}")
    meta = yaml.safe_load(match.group(1)) or {}
    body = match.group(2).strip()
    name = str(meta.get("name") or path.parent.name)
    skill_type = str(meta.get("type") or "")
    if skill_type not in VALID_TYPES:
        raise ValueError(f"SKILL.md 的 type 必须是 {VALID_TYPES} 之一: {path}")
    machine = {k: v for k, v in meta.items()
               if re.fullmatch(r"[a-z][a-z0-9_]*", str(k)) and k not in ("name", "type", "description")}
    return SkillDoc(
        name=name, skill_type=skill_type,
        description=str(meta.get("description", "")),
        machine=machine, body=body, path=str(path), level=level,
    )


def _scan_dir(base: Path, level: str, found: dict[tuple[str, str], SkillDoc]) -> list[str]:
    errors: list[str] = []
    if not base.is_dir():
        return errors
    for skill_md in sorted(base.glob("*/SKILL.md")):
        try:
            skill = parse_skill_md(skill_md, level)
        except Exception as exc:  # 坏的 skill 不阻断,记录错误
            errors.append(f"[skill 加载失败] {skill_md}: {exc}")
            continue
        found[(skill.name, skill.skill_type)] = skill
    return errors


def discover_skills(project_dir: str | Path) -> tuple[dict[str, SkillDoc], list[str]]:
    """按 项目级 > 用户级 > 内置 的优先级合并两类 Skill。

    返回 ({"case-design": SkillDoc, "doc-format": SkillDoc}, 加载警告列表)。
    """
    found: dict[tuple[str, str], SkillDoc] = {}
    warnings: list[str] = []
    levels = [
        (Path.home() / ".api-test-agent" / "skills", "user"),
        (Path(__file__).resolve().parent / "skills", "builtin"),
        (Path(project_dir) / ".api-test-agent" / "skills", "project"),
    ]
    for base, level in levels:
        warnings.extend(_scan_dir(base, level, found))  # 后扫的覆盖先扫的(project 最后)
    by_type = {t: s for (_, t), s in found.items()}
    return by_type, warnings


def default_case_id_pattern(doc_skill: SkillDoc | None) -> str:
    if doc_skill and doc_skill.machine.get("case_id_pattern"):
        return str(doc_skill.machine["case_id_pattern"])
    return r"^[A-Z][A-Z0-9]*-[A-Z]+-\d{3,4}$"
