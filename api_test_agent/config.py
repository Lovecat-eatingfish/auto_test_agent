"""Agent configuration: YAML file + environment variables."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class LLMConfig:
    provider: str = "openai-compatible"
    model_id: str = ""
    api: str = "openai-completions"
    base_url: str = ""
    api_key: str = ""
    context_window: int = 128000
    max_tokens: int = 32000
    # 额外请求体参数(如 GLM 关闭思考: {"thinking": {"type": "disabled"}})
    extra_body: dict = field(default_factory=dict)

    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model_id)


@dataclass
class ServiceConfig:
    base_url: str = ""
    app_cmd: str = ""            # 启动被测服务的命令(留空则假定服务已在运行)
    health_path: str = "/health"
    timeout: float = 15.0
    test_account: dict = field(default_factory=lambda: {"username": "", "password": ""})
    login: dict = field(default_factory=lambda: {
        "path": "", "username_field": "username", "password_field": "password",
        "token_field": "data.token", "header_format": "Bearer {token}",
    })


@dataclass
class AgentConfig:
    project_dir: str = "."
    requirement_text: str = ""
    requirement_file: str = ""
    output_dir: str = ""
    project_code: str = "AUTO"
    llm: LLMConfig = field(default_factory=LLMConfig)
    service: ServiceConfig = field(default_factory=ServiceConfig)
    allure_home: str = ""
    non_interactive: bool = False
    max_fix_rounds: int = 2
    phase_timeout_seconds: int = 1200
    verbose: bool = False

    # ---------- loading ----------
    @classmethod
    def load(cls, config_file: str | None, overrides: dict) -> "AgentConfig":
        data: dict = {}
        if config_file:
            with open(config_file, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        llm_raw = data.get("llm", {}) or {}
        extra_body_raw = llm_raw.get("extra_body") or os.environ.get("ATA_LLM_EXTRA_BODY") or {}
        if isinstance(extra_body_raw, str):
            extra_body_raw = json.loads(extra_body_raw or "{}")
        llm = LLMConfig(
            provider=llm_raw.get("provider", "openai-compatible"),
            model_id=llm_raw.get("model_id") or os.environ.get("ATA_LLM_MODEL", ""),
            base_url=llm_raw.get("base_url") or os.environ.get("ATA_LLM_BASE_URL", ""),
            api_key=llm_raw.get("api_key") or os.environ.get("ATA_LLM_API_KEY", ""),
            context_window=llm_raw.get("context_window", 128000),
            max_tokens=llm_raw.get("max_tokens", 32000),
            extra_body=extra_body_raw,
        )
        svc_raw = data.get("service", {}) or {}
        service = ServiceConfig(
            base_url=svc_raw.get("base_url", ""),
            app_cmd=svc_raw.get("app_cmd", ""),
            health_path=svc_raw.get("health_path", "/health"),
            test_account=svc_raw.get("test_account", {"username": "", "password": ""}),
            login=svc_raw.get("login", ServiceConfig().login),
        )
        cfg = cls(
            project_dir=data.get("project_dir", "."),
            output_dir=data.get("output_dir", ""),
            project_code=data.get("project_code", "AUTO"),
            llm=llm,
            service=service,
            allure_home=data.get("allure_home", os.environ.get("ATA_ALLURE_HOME", "")),
            max_fix_rounds=data.get("max_fix_rounds", 2),
        )
        for key, value in overrides.items():
            if value is None:
                continue
            if key == "project_dir":
                cfg.project_dir = str(value)
            elif key == "output_dir":
                cfg.output_dir = str(value)
            elif key == "project_code":
                cfg.project_code = str(value)
            elif key == "requirement_text":
                cfg.requirement_text = str(value)
            elif key == "requirement_file":
                cfg.requirement_file = str(value)
            elif key == "base_url":
                cfg.service.base_url = str(value)
            elif key == "app_cmd":
                cfg.service.app_cmd = str(value)
            elif key == "allure_home":
                cfg.allure_home = str(value)
            elif key == "non_interactive":
                cfg.non_interactive = bool(value)
            elif key == "verbose":
                cfg.verbose = bool(value)
        cfg.normalize()
        return cfg

    def normalize(self) -> None:
        self.project_dir = str(Path(self.project_dir).resolve())
        if not self.output_dir:
            self.output_dir = str(Path(self.project_dir).parent / "ata_output" / Path(self.project_dir).name)
        self.output_dir = str(Path(self.output_dir).resolve())
        if not self.project_code or self.project_code == "AUTO":
            self.project_code = Path(self.project_dir).name.upper()[:10].replace("-", "") or "PROJ"

    @property
    def session_dir(self) -> Path:
        return Path(self.output_dir) / ".session"

    @property
    def state_file(self) -> Path:
        return self.session_dir / "state.json"
