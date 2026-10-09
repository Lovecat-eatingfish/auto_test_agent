# api-test-agent — 接口自动化测试 Agent

基于 [pi-coding-agent-python-sdk](https://pypi.org/project/pi-coding-agent-python-sdk/) 的专用 Agent:输入一个被测项目目录(可选:一段版本需求),产出

```
接口清单与需求映射 → 测试用例(Excel + Markdown,人工评审)→ pytest 自动化工程 → 自测修复 → Allure HTML 报告
```

架构:**外循环 = 确定性阶段状态机(带 2 个人工评审门禁),内循环 = 每阶段一个 pi session(LLM 自由使用工具)**。

## 流程

```
P0 环境自检 → P1 扫描定位 ──【门禁1:人审接口清单】──▶ P2 用例设计(Skill 驱动)──【门禁2:人审用例】──▶
                                    P3 pytest 工程生成(确定性)→ P4 自测+LLM修复循环 → P5 Allure 报告
```

- **语言无关**:Java(Spring controller)、Python(FastAPI/Flask)、Go 等由 LLM 识别,不写死解析器。
- **迭代模式**:给出本版本需求文本,Agent 只针对需求相关接口设计用例;不给需求则全量扫描。
- **响应约定自动识别**:统一信封、业务码、异常处理器从被测代码中读取,用例预期与断言以此为准。
- **修复三分类**:脚本缺陷 → 修脚本;期望错误 → 修期望;**疑似被测系统缺陷 → 只上报,不修**。

## 快速开始

```bash
# 1. 安装(本仓库根目录)
pip install -e .
# 2. 环境自检
api-test-agent check-env
# 3. 配置 LLM(OpenAI 兼容),任选其一:
#    环境变量: ATA_LLM_BASE_URL / ATA_LLM_API_KEY / ATA_LLM_MODEL
#    或 agent.yaml(参考 config.example.yaml)
# 4. 运行(全量模式)
api-test-agent run <被测项目目录> --base-url http://127.0.0.1:8100 --allure-home <allure安装目录>
#    迭代模式:追加 --requirement-text "本版本需求:..."
# 5. 两个评审门禁处按终端提示操作(y 确认 / e 修改文件后回读 / n 放弃);
#    非交互模式(--non-interactive)会在门禁处暂停,之后:
api-test-agent resume --approve-interfaces        # 确认接口清单
api-test-agent resume --approve-cases             # 确认用例(以当前 Excel/MD 为基线)
```

产物输出到 `ata_output/<项目名>/`:

| 文件 | 说明 |
| --- | --- |
| `01_接口清单与需求映射.md` | 门禁 1 评审载体(可编辑回读) |
| `02_测试用例.xlsx / .md` | 用例文档,门禁 2 评审载体(可直接修改) |
| `testframe/` | 生成的 pytest 工程(config/conftest/common/data/testcases,allure 埋点) |
| `allure-report/index.html` | HTML 测试报告 |
| `00_总结.md` | 产物清单、自测结论、疑似缺陷列表 |
| `.session/state.json` | 会话状态(断点续跑) |

## 用户自定义 Skill(公司规范接入)

两类 Skill,放在 `<项目>/.api-test-agent/skills/<名字>/SKILL.md`(项目级,优先)或 `~/.api-test-agent/skills/`(全局),同名覆盖内置默认:

```bash
api-test-agent skills init     # 导出内置默认 Skill 作为起点
api-test-agent skills list     # 查看当前生效的 Skill
```

- `type: case-design` — 用例**设计规范**:等价类/边界值/场景法/因果图在本公司怎么用、优先级定义。
- `type: doc-format` — 用例**文档规范**:编号规则、列定义、格式;front matter 中的机读字段
  (`case_id_pattern`、`excel_columns` 等)由本地校验器**强制执行**,不依赖 LLM 自觉。

## 命令一览

```
api-test-agent run <dir>      [--requirement-text ...] [--config agent.yaml] [--out ...]
                              [--base-url URL | --app-cmd "启动命令"] [--non-interactive]
api-test-agent resume [session_id] [--approve-interfaces] [--approve-cases]
api-test-agent skills list|init
api-test-agent check-env
```

## 本仓库内容

```
api_test_agent/        Agent 包(pipeline/skills/docgen/testframe/pi_agent/...)
api_test_agent/skills/ 内置默认 Skill(default-case-design / default-doc-format)
demo_app/              被测示例项目(FastAPI,登录/用户/商品,含参数约束与统一信封)
jmeter-demo/           Java Spring 被测示例(语言无关性验证)
docs/                  需求说明书 + 方案设计
scripts/               SDK 冒烟 / mock LLM 端到端 / Java 冒烟脚本
tests/                 Agent 自身单元测试(python -m pytest tests/)
```

## 验证状态(2026-10-04)

- ✅ SDK 集成冒烟:node bridge(bundled worker)+ OpenAI 兼容 provider(SSE)+ 自定义工具循环(`scripts/smoke_sdk_2.py`)
- ✅ 端到端(mock LLM 驱动全流程):demo_app 上 22 条用例全部生成、自测 22/22 通过、Allure 报告生成(`scripts/e2e_mock.py`)
- ✅ Java 项目扫描冒烟:jmeter-demo 上 P1 定位链路跑通(`scripts/smoke_java.py`)
- ✅ 单元测试 26 项全通过
- ✅ **真实 LLM 全流程验证(2026-10-04,glm-5.2)**:demo_app 上 P1 扫描 10/10 接口全对(含鉴权标记与代码证据)→ P2 四模块 104 条用例(等价类/边界值/场景法,分批保存)→ P3 生成 pytest 工程 → P4 自测 104/104 通过(1 轮 LLM 修复,6 条用例期望修正)→ P5 Allure 报告
- ✅ **真实 LLM 全流程验证:Java 项目(2026-10-04,glm-5.2)**:jmeter-demo(Spring Boot + SQLite + JWT)P1 扫描 7/7 全对 → P2 四模块 40 条用例(每批一个短会话)→ P3 生成 pytest 工程 → P4 自测 40/40 通过(1 轮 LLM 修复:10 条期望修正 + 2 处脚本修复)→ P5 Allure 报告;`agent_java.yaml` 为本次运行配置
- ✅ **直连完整流程复测(2026-10-04,不经中转)**:P1 7/7 → P2 50 条(新增模块用例数量护栏)→ P4 自测 49/50 —— 唯一失败是 Agent 发现并上报的**被测系统真实缺陷**(`/slow` 接口阻塞时长截断但返回消息用原始入参,附源码证据),验证了"不修改期望迁就缺陷"的归因纪律;同时修复了 resume 链路 project_dir 被 cwd 污染的既有缺陷(曾致服务在错误目录启动)

## 真实 LLM 运行注意事项(实测踩坑)

1. **推理模型需关闭思考**:glm-5.2 等会持续输出 reasoning_content 导致长时间不出工具调用,配置
   `extra_body: {"thinking": {"type": "disabled"}}`(yaml)或环境变量 `ATA_LLM_EXTRA_BODY`。
   阶跃 step 系列实测无法关闭思考,不推荐。
2. **本地中转可选**(`scripts/logging_proxy.py`):sensenova 直连已实测可用(2026-10-04 复测,
   含流式工具调用与长文本流,真实 P1 阶段 15 次连续请求跑通)。如再遇直连流式停滞,或需要
   请求日志排查/客户端限流,再启用中转:
   `python scripts/logging_proxy.py 18412 https://token.sensenova.cn/v1`
   (配 `ATA_LLM_BASE_URL=http://127.0.0.1:18412/v1`;`ATA_PROXY_MIN_INTERVAL=6` 可限流防 429 风暴)。
3. **工具参数有大小上限**(node bridge 对 >12KB 的单次工具调用参数会静默丢弃):
   P2 用例按 2-3 条/批分批保存,`fix_script_file` 单次 ≤150 行(支持 replace/append 分块),均为规避此限制。
4. **会话偶发收尾挂起**:dispose 已内置 20s 超时兜底,CLI 结束时硬退出;残留 node worker 可用
   `Get-CimInstance Win32_Process | ? CommandLine -match 'node_worker'` 查杀。
5. **被测服务启动失败可诊断**:`service.app_cmd` 的输出写入 `output_dir/service.log`,
   启动即退(瞬态失败)自动重试一次,报错信息附带日志末尾,不再是无诊断信息的退出码。
6. **输入 token 控制(多轮会话的请求体随历史整体重发)**:
   - 内置 read/grep 会把整文件/全部命中写进历史,已替换为带字符上限的自定义 `read_file`(分页)/`grep_code`(截断)工具(`api_test_agent/code_tools.py`);
   - P2 每个批次用独立短会话运行(系统自动拼接模块进度 Recap),避免单会话历史二次膨胀(实测长会话占请求总量 62%);
   - 追问循环中历史超过 60000 字符自动调用会话压缩(`PhaseAgent.compact_above_chars`)。
   实测同规模项目平均请求体从 50.6KB 降到 31.4KB(-38%)。

## 环境要求

Python ≥ 3.11;Node.js ≥ 18(pi SDK bridge);allure 命令行(报告阶段,支持 `--allure-home` 或 PATH)。
注意:代码与需求内容会发送给所配置的 LLM API 服务。
