"""Session state: the outer-loop's persisted world (state.json)."""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

PHASES = ["P0_ENV", "P1_LOCATE", "GATE1_REVIEW", "P2_DESIGN", "GATE2_REVIEW",
          "P3_CODEGEN", "P4_SELFTEST", "P5_REPORT", "DONE"]


@dataclass
class SessionState:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    phase: str = "P0_ENV"
    config_snapshot: dict = field(default_factory=dict)

    # P1 产物
    endpoints: list[dict] = field(default_factory=list)
    response_convention: str = ""
    requirement: dict = field(default_factory=lambda: {"text": "", "source": ""})
    interface_review_file: str = ""
    interfaces_approved: bool = False

    # P2 产物
    cases: list[dict] = field(default_factory=list)
    case_files: dict = field(default_factory=lambda: {"xlsx": "", "md": ""})
    cases_approved: bool = False
    design_skill_name: str = ""
    doc_skill_name: str = ""
    p2_gaps: dict = field(default_factory=dict)  # 模块 -> finish_phase 校验缺口(跨批次会话传递)

    # P3 产物
    testframe_dir: str = ""
    node_map: dict = field(default_factory=dict)   # case_id -> pytest node id

    # P4/P5 产物
    selftest: dict = field(default_factory=dict)   # {rounds, passed, failed, defects[], status}
    report_dir: str = ""
    summary_file: str = ""

    # 统计与阶段完成标记
    stats: dict = field(default_factory=lambda: {"llm_sessions": 0, "tool_calls": 0})
    phase_flags: dict = field(default_factory=dict)

    # ---------- io ----------
    def save(self, state_file: Path) -> None:
        self.updated_at = time.time()
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                              encoding="utf-8")

    @classmethod
    def load(cls, state_file: Path) -> "SessionState":
        data = json.loads(Path(state_file).read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}  # noqa: C416
        return cls(**{k: v for k, v in data.items() if k in known})

    # ---------- helpers ----------
    def modules(self) -> list[str]:
        seen: list[str] = []
        for ep in self.endpoints:
            module = ep.get("module") or "COMMON"
            if module not in seen:
                seen.append(module)
        return seen

    def next_case_id(self, module: str, pattern_project: str) -> str:
        # 取「模块内最大规范序号」与「模块用例数」的较大者+1:
        # 分批保存/重提交时不撞号,也兼容非规范编号的历史用例
        seq = 0
        count = 0
        for c in self.cases:
            if c.get("module") != module:
                continue
            count += 1
            m = re.match(rf"^{re.escape(pattern_project)}-{re.escape(module)}-(\d+)$",
                         str(c.get("case_id", "")))
            if m:
                seq = max(seq, int(m.group(1)))
        return f"{pattern_project}-{module}-{max(seq, count) + 1:03d}"
