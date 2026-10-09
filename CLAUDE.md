# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**api-test-agent** is an AI-powered API automation testing tool built on the `pi-coding-agent-python-sdk`. It automates the workflow: scan APIs → generate test cases (with human review gates) → create pytest framework → self-test and fix → generate Allure HTML reports.

The tool is **language-agnostic** and can test APIs from Java (Spring), Python (FastAPI/Flask), Go, etc. by leveraging LLM capabilities to identify and understand code.

**Key Characteristics:**
- **Two human review gates**: Interface list approval (Gate 1) and test case approval (Gate 2)
- **Phase-based state machine**: Deterministic outer loop with LLM-powered inner loops per phase
- **Customizable via Skills**: Users can define company-specific test design and documentation standards
- **Resume capability**: Sessions can be paused at review gates and resumed later

## Tech Stack

- **Python** ≥ 3.11
- **Node.js** ≥ 18 (required for pi SDK bridge/worker)
- **Core dependencies**:
  - `pi-coding-agent-python-sdk` ≥ 0.4.3 (LLM agent framework)
  - `pytest` ≥ 8.0 (test execution)
  - `openpyxl` ≥ 3.1.0 (Excel generation)
  - `PyYAML` ≥ 6.0 (configuration)
  - `allure-pytest` ≥ 2.13 (test reporting)
- **Optional**: Allure CLI for HTML report generation

## Repository Structure

```
.
├── api_test_agent/          # Main package (~2,652 LOC)
│   ├── cli.py              # CLI entry point (main command dispatcher)
│   ├── pipeline.py         # Outer loop: phase state machine (P0-P5 + gates)
│   ├── config.py           # Configuration loader (YAML + env vars)
│   ├── pi_agent.py         # LLM agent wrapper (pi SDK integration)
│   ├── phase_tools.py      # Phase-specific tools for LLM (P1/P2/P4)
│   ├── code_tools.py       # Custom code tools (read_file/grep_code with size limits)
│   ├── docgen.py           # Excel/Markdown generation and readback
│   ├── skills.py           # Skill discovery and loading system
│   ├── runner.py           # Service lifecycle management + pytest/allure execution
│   ├── state.py            # Session state persistence (JSON)
│   ├── validators.py       # Case validation (enforces Skill constraints)
│   ├── requirements_input.py  # Requirement file loading (txt/md)
│   ├── testframe/          # pytest framework generator (templates + generator)
│   └── skills/             # Built-in default Skills
│       ├── default-case-design/   # Test design methodology (equivalence classes, etc.)
│       └── default-doc-format/    # Test case documentation format (ID pattern, columns)
├── tests/                  # Unit tests for the agent itself (26 tests)
├── scripts/                # Smoke tests, e2e mock, proxy scripts
│   ├── smoke_sdk_*.py      # SDK integration tests
│   ├── e2e_mock.py         # Full e2e with mock LLM
│   ├── smoke_java.py       # Java project scan test
│   └── logging_proxy.py    # Optional local proxy for LLM requests
├── demo_app/               # Demo FastAPI app (login/users/products)
├── jmeter-demo/            # Demo Java Spring Boot app (JMeter testing project)
├── docs/                   # Requirements and design documents (Chinese)
├── output/                 # Previous test runs (Allure + testframe)
├── agent.yaml              # Example configuration (copied and customized)
├── agent_java.yaml         # Configuration for Java demo project
└── config.example.yaml     # Configuration template with comments
```

## Common Development Commands

### Installation

```bash
# Install in editable mode (from repo root)
pip install -e .

# Install with test dependencies
pip install -e ".[test]"
```

### Environment Self-Check

```bash
# Verify Python version, Node.js, LLM config, Allure availability
api-test-agent check-env
api-test-agent check-env --config agent.yaml
```

### Running the Agent

```bash
# Full scan mode (no requirement text)
api-test-agent run <project_dir> --config agent.yaml --base-url http://127.0.0.1:8100

# Iteration mode (with requirement text)
api-test-agent run <project_dir> --config agent.yaml --requirement-text "本版本需求: ..."

# Non-interactive mode (pauses at gates, use resume to continue)
api-test-agent run <project_dir> --non-interactive --config agent.yaml

# With Allure report generation
api-test-agent run <project_dir> --config agent.yaml --allure-home "D:\path\to\allure-2.46.1"

# Resume from a paused gate
api-test-agent resume --out <output_dir> --approve-interfaces   # Skip Gate 1
api-test-agent resume --out <output_dir> --approve-cases        # Skip Gate 2
```

### Testing

```bash
# Run all unit tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_agent.py -v

# Run specific test
python -m pytest tests/test_agent.py::test_builtin_skills_discovered -v

# Generate coverage report
python -m pytest tests/ --cov=api_test_agent --cov-report=html
```

### Output Artifacts

Agent produces output in `ata_output/<project_name>/` (or `--out` specified):

- `01_接口清单与需求映射.md` — Gate 1 review document (interfaces list)
- `02_测试用例.xlsx / .md` — Test cases (Gate 2 review document)
- `testframe/` — Generated pytest framework (config/conftest/tests/data)
- `allure-report/` — HTML Allure report
- `00_总结.md` — Summary (artifacts, self-test results, suspected defects)
- `.session/state.json` — Session state (resume capability)

## Architecture

### Phase State Machine (pipeline.py)

The outer loop is a deterministic state machine with these phases:

| Phase | Name | Description | Interaction |
|-------|------|-------------|-------------|
| P0 | ENV | Environment self-check | Automatic |
| P1 | LOCATE | API discovery & requirement mapping | **Gate 1: User review required** |
| P2 | DESIGN | Test case design (Skill-driven, LLM-powered) | **Gate 2: User review required** |
| P3 | CODEGEN | pytest framework generation (deterministic, no LLM) | Automatic |
| P4 | SELFTEST | Self-test + LLM-powered fix loop | Automatic |
| P5 | REPORT | Allure HTML report generation | Automatic |

**Resumption**: Use `api-test-agent resume --approve-interfaces/--approve-cases` to continue after manual review.

### Key Abstractions

#### 1. Configuration (config.py)

- `LLMConfig`: Provider-agnostic OpenAI-compatible config (base_url, api_key, model_id, extra_body)
- `ServiceConfig`: Target service config (base_url, app_cmd, health_path, login credentials)
- `AgentConfig`: Top-level config (project_dir, output_dir, requirement_text, etc.)

**Priority**: CLI args > YAML file > Environment variables

**Environment Variables**:
- `ATA_LLM_BASE_URL`, `ATA_LLM_API_KEY`, `ATA_LLM_MODEL`
- `ATA_LLM_EXTRA_BODY` (JSON string, e.g., `{"thinking": {"type": "disabled"}}`)
- `ATA_ALLURE_HOME`

#### 2. Skills System (skills.py)

Two types of Skills (placed in `<project>/.api-test-agent/skills/<name>/SKILL.md` or `~/.api-test-agent/skills/`):

- **case-design**: Defines how to apply equivalence partitioning, boundary analysis, scenario testing, etc.
- **doc-format**: Defines case ID pattern, Excel columns, field semantics, and validation rules

**Built-in defaults** in `api_test_agent/skills/` can be overridden by project-level Skills.

**Discovery**: Project-level Skills take precedence over built-in Skills of the same name.

#### 3. Test Case Validation (validators.py)

Enforces Skill constraints on generated test cases (case_id pattern, required fields, Excel column names). This is a **hard constraint**, not a soft LLM guideline — validation errors prevent document saving.

#### 4. Test Framework Generation (testframe/)

Deterministic code generation from test cases:
- `generator.py`: Generates pytest project structure
- `templates.py`: Jinja2 templates for conftest.py, test files, data files

Generated structure:
```
testframe/
├── config.yaml          # Allure + pytest config
├── conftest.py          # Fixtures (auth, client, env setup)
├── common/              # Shared utilities
├── data/                # Test data (CSV/JSON/YAML)
└── testcases/           # pytest test files (one per module)
```

#### 5. Service Lifecycle (runner.py)

- `ensure_service()`: Starts/stops target service via `app_cmd`, health-checks readiness
- `run_pytest()`: Executes pytest, collects JSON results
- Windows: Uses `taskkill /F /T` to kill process tree

### Inner Loop: pi SDK Agent (pi_agent.py, phase_tools.py)

Each LLM-powered phase (P1, P2, P4) uses a `PhaseAgent` instance with:
- **PhaseContext**: Shared state (cases, endpoints, response conventions)
- **Phase-specific tools**: P1 tools (scan, grep), P2 tools (case management), P4 tools (fix scripts, report defects)
- **Custom code tools** (code_tools.py): `read_file` (paginated, max 4000 chars), `grep_code` (truncated results) — avoids tool parameter size limits
- **Auto-compaction**: Conversation history > 60,000 chars triggers automatic compression

**Known LLM Constraints**:
- Tool parameters > 12KB are silently dropped (node bridge limit) → batches are small (P2: 2-3 cases/batch; P4 fix: ≤150 lines/file)
- Reasoning models (e.g., glm-5.2) require `extra_body: {"thinking": {"type": "disabled"}}` to avoid output-only reasoning loops
- Session dispose has 20s timeout; stray node workers can be killed via PowerShell (see README for command)

## Demo Projects

### demo_app (Python FastAPI)

- **Location**: `demo_app/`
- **Stack**: FastAPI + uvicorn
- **Endpoints**: /health, /api/register, /api/login, /api/users/*, /api/products/*
- **Default credentials**: admin / Admin12345
- **Run**: `uvicorn main:app --reload --port 8100` (from demo_app directory)

### jmeter-demo (Java Spring Boot)

- **Location**: `jmeter-demo/`
- **Stack**: Java 8 + Spring Boot 2.7.18 + SQLite + JWT
- **Port**: 38000
- **Database**: `data/demo.db` (auto-created, 1000 seed users)
- **Build/Run**: `mvn -DskipTests package && java -jar target/jmeter-demo-1.0.0.jar`

## Testing Strategy

### Unit Tests (tests/)

Run with `python -m pytest tests/ -v`. Coverage includes:
- Tool result shape validation
- Skill discovery and override behavior
- Case validation logic
- Excel/Markdown roundtrip
- Requirement loading
- State persistence
- Framework generation
- Code tools (read_file pagination, grep_code truncation)
- Service handle (Windows process kill)

### Integration Tests (scripts/)

- `smoke_sdk_2.py`: SDK integration with pi bridge + mock LLM
- `e2e_mock.py`: Full end-to-end with mock LLM (22 cases, 22/22 pass)
- `smoke_java.py`: Java project P1 scan verification
- `logging_proxy.py`: Optional local proxy for request logging + rate limiting

## Key Design Patterns

1. **Deterministic outer loop**: Phase transitions are explicit and persisted; can resume from any gate
2. **LLM autonomy per phase**: Each phase gets a fresh pi session; LLM freely uses tools within phase constraints
3. **Document-driven handoff**: Excel + Markdown serve as persistent artifacts for human review; changes are read back
4. **Fix attribution**: P4 self-test distinguishes between script bugs (fix script), expectation errors (fix expectation), and suspected system defects (report only, do not fix)
5. **Size-aware tooling**: Custom tools with size caps to avoid node bridge 12KB silent-drop limit

## Known Issues & Workarounds

1. **Reasoning models**: Must disable thinking mode via `extra_body` (see config.example.yaml)
2. **Node bridge parameter limit**: Keep batches small; P2 uses 2-3 cases/batch, P4 fix limited to 150 lines per file
3. **Session hang on exit**: `os._exit(code)` used in cli.py:main to force-exit (workaround for node worker monitor hang)
4. **Stray node workers**: Check and kill via PowerShell if needed (command in README)
5. **No .gitignore**: Add one if needed for virtual environments, __pycache__, output dirs

## Development Workflow

When modifying this codebase:

1. **Prefer surgical changes**: Touch only files related to the task; avoid "cleanup" or "improvement" of adjacent code
2. **Maintain CLI compatibility**: Changes to config, pipeline, or phases should preserve existing CLI interface
3. **Respect size limits**: Any new LLM tool must account for 12KB parameter limit; use chunking/pagination
4. **Preserve resume semantics**: State file (`state.json`) is the source of truth for resume; never break backward compatibility
5. **Validate Skills**: New/updated Skills must be validated by `validators.py` logic; don't bypass constraints
6. **Windows compatibility**: Code handles Windows paths and process management (e.g., `taskkill /F /T`); test on Windows

## Example Workflows

See `README.md` for detailed usage examples:
- Full scan mode (all APIs, no requirement)
- Iteration mode (requirement text provided)
- Non-interactive + resume workflow
- Custom Skill usage

See `docs/` for:
- `需求说明书.md` — Detailed requirements (S1/S2/S3 scenarios, FR-1 to FR-12, acceptance criteria)
- `方案设计.md` — Architecture design (state machine, pi session integration, fix attribution)
