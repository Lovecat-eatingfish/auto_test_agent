"""Requirement input abstraction (FR: loader interface, pluggable by format)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RequirementDoc:
    text: str
    source: str  # 描述来源,如 "inline-text" / "file:xxx.txt"


class RequirementLoader(ABC):
    """需求加载器接口:v1 内置文本输入;后续按扩展名注册新实现(txt/word/...)。"""

    extensions: tuple[str, ...] = ()

    @abstractmethod
    def load(self, source: str) -> RequirementDoc:
        ...


class InlineTextLoader(RequirementLoader):
    """直接输入需求文本(--requirement-text)。"""

    def load(self, source: str) -> RequirementDoc:
        text = (source or "").strip()
        if not text:
            raise ValueError("需求文本为空,请通过 --requirement-text 提供需求内容")
        return RequirementDoc(text=text, source="inline-text")


class TextFileLoader(RequirementLoader):
    """txt / md 需求文件(utf-8)。"""

    extensions = (".txt", ".md")

    def load(self, source: str) -> RequirementDoc:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"需求文件不存在: {source}")
        return RequirementDoc(text=path.read_text(encoding="utf-8"), source=f"file:{path.name}")


class DocxLoader(RequirementLoader):
    """word 需求文件:v1 未实现,留作扩展点。"""

    extensions = (".docx", ".doc")

    def load(self, source: str) -> RequirementDoc:
        raise NotImplementedError(
            f"v1 暂不支持 {Path(source).suffix} 需求文件;请将需求内容粘贴为 --requirement-text,"
            "或自行实现 RequirementLoader 并注册(见 docs/方案设计.md §6)")


_REGISTRY: dict[str, RequirementLoader] = {}
for _loader_cls in (TextFileLoader, DocxLoader):
    _loader = _loader_cls()
    for _ext in _loader.extensions:
        _REGISTRY[_ext] = _loader


def load_requirement(text: str = "", file: str = "") -> RequirementDoc:
    """按优先级加载需求:内联文本 > 按扩展名分发的文件加载器。"""
    if text:
        return InlineTextLoader().load(text)
    if file:
        suffix = Path(file).suffix.lower()
        loader = _REGISTRY.get(suffix)
        if loader is None:
            raise NotImplementedError(
                f"暂不支持的需求文件格式: {suffix or '(无扩展名)'};当前支持: {sorted(_REGISTRY)} 或直接输入文本")
        return loader.load(file)
    return RequirementDoc(text="", source="none")


def supported_formats() -> list[str]:
    return ["inline-text"] + sorted(_REGISTRY)
