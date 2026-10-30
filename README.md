# MAGI

> 当前定位：[独立产品与学习主线](docs/MAGI_设计决策_002_独立产品与学习主线.md)（2026-10-29）：掌握并长期迭代自己的 MAGI，Yggdrasil 是调用方之一。
> 当前实施计划：[MVP 整体设计与七天改造计划 v0.3](docs/MAGI_MVP整体设计与七天改造计划_v0.3.md)：独立 MAGI 后端、可替换界面、完整使用闭环；结合上述定位补充执行。
> 长期产品方向：[产品与技术设计 v0.2](docs/MAGI_产品与技术设计_v0.2.md)；本阶段范围与顺序以 v0.3 为准。
> 定位变更：[个人助手定位决策](docs/MAGI_设计决策_001_个人助手定位.md)。v0.1 的 Coding Agent 路线保留为历史记录。
> 协作方式（2026-10-29）：[先设计任务书，再切换执行模型，边学习边开发](docs/MAGI_协作开发流程.md)。
> Web MVP 已实现：[模块 005 整合总结](docs/MAGI_模块总结_005_Web_MVP整合.md)、[真实演示与验收](docs/MAGI_MVP演示与验收.md)。包括 API、Open WebUI 两种模式、三人格、确认保存、重启查询和反馈修订；模型质量与部署限制见验收记录。原[启动导读 005](docs/MAGI_开发启动与代码导读_005.md)保留为实施前基线。
> 最新验收：[模块 007：云端稳定性验收与阅读导引](docs/MAGI_模块总结_007_云端稳定性验收.md)。千问固定评测 12/12 结构通过，真实网页闭环通过；人工复核仍发现 5 份候选需澄清时间表述，不能等同于全面建议质量通过。
> 最新改进：[模块 008：计划内容一致性](docs/MAGI_模块总结_008_计划内容一致性.md)。拆分任务、休息、复盘和缓冲，程序汇总分钟；区分原文依据、模型假设和待确认事项。252 项离线测试通过，新结构的千问真实生成验收待补。
> 当前实现：[模块 009：通用计划兼容性](docs/MAGI_模块总结_009_通用计划兼容性.md)。活动标签可自定义或省略；无时间预算时不强制估时，有明确预算时核对分钟合计。259 项离线测试通过，真实模型验收待补。008 的固定分类要求已取消。
> 最新真实验收：[模块 010：跨场景云端验收](docs/MAGI_模块总结_010_跨场景云端验收.md)。固定八轮真实千问调用，四轮完成、四轮因引用或超时未完成，稳定性尚未通过；兼容空估时与自定义标签已有真实证据。263 项离线测试通过，核心函数新增中文说明。
> 当前轮次：[模块 011：原文引用与超时诊断](docs/MAGI_模块总结_011_原文引用与超时诊断.md)。引用改为选择编号、程序填入原文；区分连接等请求超时与整轮期限。相同八案例本轮七轮完成、一轮连接超时，无摘录匹配失败；276项离线测试通过，内容歧义仍如实保留。
> 最新界面：[模块 012：计划查看与版本对比](docs/MAGI_模块总结_012_计划查看与版本对比.md)。计划列表、历史正文、版本差异和基于最新版的反馈入口已接通；277项离线测试通过，真实浏览器配合模拟模型完成保存与重启验证。典型工程问题持续记录在 [question.md](question.md)。
> 当前交互：[模块 013：页面最小交互优化](docs/MAGI_模块总结_013_页面最小交互优化.md)。连接后直接聊天，三人格用开关启用，计划按需查看，高级参数和讨论详情收起；刷新后可恢复真实保存状态。保留现有配色，原作风格美术后续再定。
> P1导出：[模块 015：已保存计划Markdown导出](docs/MAGI_模块总结_015_已保存计划Markdown导出.md)。在计划详情选择版本并下载文件；不调用模型、不修改计划、不代表执行计划中的任务。279项离线测试通过，真实浏览器下载已验证。
> 最新功能：[模块 016：网页个人记忆管理](docs/MAGI_模块总结_016_网页个人记忆管理.md)。按需查看、添加、修改与删除已确认全局记忆，先预览后确认；变更复用版本、幂等和上下文失效机制。283项离线测试通过，真实浏览器配合模拟模型验证输入中的记忆更新。
> 自动建议：[设计决策003](docs/MAGI_设计决策_003_自动记忆候选.md)、[模块017总结](docs/MAGI_模块总结_017_自动记忆候选与确认.md)。原生网页回答后可提出记忆候选，用户编辑确认才生效，设置中可暂停。304项测试通过，固定8例真实提取的分支符合预设，仍发现一例遗漏期限，需用户复核。

MAGI 将建设为受 EVA 启发的长期个人 AI 助手：在持续对话中陪伴用户学习、研究与创造，保留已确认上下文，调用工具完成可检查的任务，并在用户需要权衡时启用三人格审议。

产品主线聚焦**三人格个人决策与行动**：目标与约束 → 多视角权衡 → 确认计划 → 执行反馈 → 版本调整。Yggdrasil 负责知识检索与证据问答；MAGI 不再建设一套知识库平台。聊天和记忆是支撑能力，双项目的区别落实在业务流程与验收中，详见设计决策 002 第 3.1 节。

- **MELCHIOR**：科学、证据、逻辑与可行性。
- **BALTHASAR**：人的福祉、安全、成本与可逆性。
- **CASPER**：自主性、长期目标与战略价值。

第一版聚焦个人学习、科研和求职中的聊天、资料整理、记忆与计划保存。软件开发与符合性验证作为后续专业能力，研究与产品验收分别记录。

## 当前阶段与协作边界

已实现单 Agent 工具循环、调用预算、文件发现与读取，以及任务内证据登记和结构化报告引用校验。模块 003 完成持久会话试验；模块 004A 新增已确认记忆的显式管理、版本与幂等、独立业务库及基于 epoch 的会话上下文失效；004B 已实现 CLI 计划保存、版本更新、幂等回执与模型只读查询。

文件调查曾出现旧资料误用，内容质量验收尚未通过。当前已有显式 CLI 管理的少量已确认记忆和版本计划；普通聊天和模型输出不能自行写入。模块 005 已补齐网页候选确认、三人格有界工作流和两种网页入口。

当前已接通 Open WebUI 日常聊天 → 按需三人格 → 确认保存 → 重启查询 → 执行反馈 → 确认新版本。新增服务不开放模型写工具，真实保存由显式确认入口完成。

当前按完整功能或验收轮次推进：助手完成一轮后集中讲解流程、参数、异常路径和验证结果，用户先阅读并复述，纠正理解后再推进下一轮。每轮留下总结，未通过的验收如实保留。

- [模块 001：单 Agent 执行基础](docs/MAGI_模块总结_001_单Agent执行基础.md)
- [模块 002：文件调查与证据验收](docs/MAGI_模块总结_002_文件调查与证据验收.md)
- [模块 003：Harness 复用与持久会话](docs/MAGI_模块总结_003_Harness复用与持久会话.md)
- [模块 004 开发任务书：记忆与计划](docs/MAGI_开发任务书_004_记忆与计划.md)
- [模块 004A：已确认记忆](docs/MAGI_模块总结_004A_已确认记忆.md)
- [模块 004B：计划与操作任务](docs/MAGI_模块总结_004B_计划与操作任务.md)
- [技术选型记录 001](docs/MAGI_技术选型记录_001_模型与执行环境.md)

## Web MVP 启动（模块 005）

使用现有 `.venv-harness`，先安装千问与 Web 增量依赖，再启动单 worker 服务：

```powershell
.\.venv-harness\Scripts\python.exe -m pip install -r requirements-qianwen.lock.txt
.\scripts\start_magi.ps1
```

默认使用千问AI平台 `qwen3.8-max`，不运行本地 Ollama；未设置 `DASHSCOPE_API_KEY` 时，脚本会隐藏输入并仅传给当前进程，不保存密钥。云端接入与实测见 [模块 006](docs/MAGI_模块总结_006_千问接入与生成校验.md)。需要手动选择本地模型时，运行 `.\scripts\start_magi.ps1 -Config magi.harness.toml`。

启动窗口另行生成 MAGI 本机网页访问凭据；它与千问 API Key 不同。打开 [本机 MAGI 聊天页](http://127.0.0.1:8765)，填入凭据、会话名，加载历史后开始聊天。三人格模式可填总时间预算（分钟）；修订时填原计划 ID 和版本号。生成成功后点击“确认保存此候选”；查询区可读取旧版本。模型输出本身不是保存证据。

首选界面 Open WebUI 的独立安装、Pipe/Action 导入、完整命令示例见 [集成说明](integrations/openwebui/README.md)。服务只监听本机；用启动窗口 Ctrl+C 停止。启动脚本受策略限制时可使用 `powershell -ExecutionPolicy Bypass -File .\scripts\start_magi.ps1`。

离线完整检查与真实 Ollama 演示分别运行：

```powershell
.\.venv-harness\Scripts\python.exe -m pytest -q
.\.venv-harness\Scripts\python.exe -X utf8 scripts/demo_mvp.py
```

演示使用临时业务库和 checkpoint，不清空现有 `.magi`；每次证据保存在 `.magi/demo-evidence-<UTC时间>.json`。该脚本使用真实模型和 HTTP 应用测试客户端；实际浏览器及进程重启证据另见 [验收记录](docs/MAGI_MVP演示与验收.md)。失败会明确结束，不自动重试模型。长会话容量、模型时间预算错误等仍可能导致 PARTIAL 或失败，请新发更具体请求或新建聊天。

## 环境安装

使用 Python 3.12。先确认 `python --version` 输出为 `3.12.x`，然后在仓库根目录运行。
使用标准库 `venv` 和 `pip`，无需安装 uv。`.python-version` 供支持该文件的工具选择版本，
它不会自行安装或切换 Python。

Windows PowerShell（直接调用虚拟环境解释器，无需修改脚本执行策略）：

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

macOS / Linux（需已安装 Python 3.12）：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

`-e` 将 `src/magi` 可编辑安装到虚拟环境，修改源码后无需重新安装；`[dev]` 安装 pytest 和 Ruff。
旧模型演示依赖通过 `pip install -e ".[ollama,dev]"` 安装。模型演示仍需要本机 Ollama 服务及已下载的模型；本地现有实验使用 `qwen3.5:4b`。

## 模块 003：持久聊天试验

本轮使用独立环境，现有 `.venv` 保持原有依赖。重建试验环境：

```powershell
.\.venv\Scripts\python.exe -m venv .venv-harness
.\.venv-harness\Scripts\python.exe -m pip install -r requirements-harness.lock.txt
.\.venv-harness\Scripts\python.exe -m pip install --no-deps -e .
.\.venv-harness\Scripts\python.exe -X utf8 -m magi.harness.cli --session study
```

输入 `/exit` 退出，再运行相同命令恢复会话。`--message "你好"` 可发送一次消息后退出，`--history` 查看同一会话历史且不请求模型。

模型和预算位于 [magi.harness.toml](magi.harness.toml)。只开放 `docs` 下的原有只读文件工具；默认写入、shell、子代理均未开放。checkpoint 和事件位于被 Git 忽略的 `.magi`。模块 003 最初仅接入 Ollama；当前也提供独立千问配置。长上下文超过保守容量预算时明确拒绝，未完成的执行不自动重放。详细解释与限制见模块 003 总结。

`requirements-harness.lock.txt` 是 Windows Python 3.12.3 的试验版本快照；直接依赖也在 `[harness]` 可选组中声明。安装软件包不会自动下载模型。

## 模块 004A：已确认记忆

交互聊天需先退出，再运行记忆管理命令；当前单进程锁会拒绝同时修改。记忆变化后使用原逻辑会话 ID 重新启动聊天，程序会选择新的内部会话段。

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 -m magi.personal.cli memory add --key answer_style --scope global --value "回答先给结论" --request-id demo-memory-001
.\.venv-harness\Scripts\python.exe -X utf8 -m magi.personal.cli memory list
.\.venv-harness\Scripts\python.exe -X utf8 -m magi.personal.cli memory update --id <ID> --expected-version 1 --value "回答先解释依据" --request-id demo-memory-002
.\.venv-harness\Scripts\python.exe -X utf8 -m magi.personal.cli memory delete --id <ID> --expected-version 2 --request-id demo-memory-003
.\.venv-harness\Scripts\python.exe -X utf8 -m magi.personal.cli operation show --request-id demo-memory-003
```

`memory list --scope global` 可按作用域过滤。聊天入口的 `--history` 按历史段显示且不请求模型；历史可查看不代表它仍会进入当前推理。

## 模块 004B：计划版本与操作

计划通过用户显式 CLI 命令保存和修改。模型只获得 `magi_list_plans` 和 `magi_get_plan` 查询工具，没有计划写工具。

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 -m magi.personal.cli plan create --title "本周计划" --body "完成实验" --source "notes.txt" --request-id demo-plan-001
.\.venv-harness\Scripts\python.exe -X utf8 -m magi.personal.cli plan list
.\.venv-harness\Scripts\python.exe -X utf8 -m magi.personal.cli plan show --id <ID>
.\.venv-harness\Scripts\python.exe -X utf8 -m magi.personal.cli plan update --id <ID> --expected-version 1 --title "本周计划" --body "完成实验并写总结" --request-id demo-plan-002
.\.venv-harness\Scripts\python.exe -X utf8 -m magi.personal.cli plan show --id <ID> --version 1
```

长计划可用 `--input plan.json --content-sha256 <SHA256>` 导入。JSON 只允许 `title`、`body`、`sources`。记忆 epoch 变化后，旧计划标记为 `needs_review`；CLI 仍可查看供用户复核，模型查询不会获得其正文。

## 验证与格式化

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -c "import magi; print(magi.__file__)"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

需要实际格式化时运行：

```powershell
.\.venv\Scripts\python.exe -m ruff format .
```

macOS / Linux 将上述 `.\.venv\Scripts\python.exe` 替换为 `.venv/bin/python`。
pytest 从 `tests/` 发现测试；请先执行可编辑安装。Ruff 检查基础语法、未使用导入和导入排序，
统一使用 88 字符行宽。测试包括领域练习、回复校验、工具循环、文件工具和调查报告。检查命令列在此处不表示当前全部检查已通过；实际范围与结果见模块总结。

验证新执行入口时使用 `.venv-harness` 运行 `python -m pytest -q`；没有安装可选依赖的原环境会跳过 harness 集成测试。离线测试使用模拟模型，真实模型证据另行记录。

## 项目结构与下一步

```text
.
├── docs/                   # 当前设计、决策与模块总结
├── expirements/            # 已有调用与文件调查演示（沿用现有目录拼写）
├── src/magi/
│   ├── agent/              # 执行循环、工具、证据及校验
│   ├── harness/            # 开源执行框架接入、配置与持久会话 CLI
│   ├── personal/           # 已确认记忆、版本计划、操作回执与有效上下文
│   └── domain/             # 早期 A/B 领域学习实现
├── tests/                  # 对应的离线验证
└── pyproject.toml
```

模块 003、004A、004B 的历史能力与验证范围见各自总结。Web、三人格与反馈闭环的当前实现见模块 005 和演示验收。执行交接文本见 v0.3 第 13 节。旧项目仍需取得代码后才能确认可复用模块，不把简历描述视为本仓库能力。

历史设计、旧交接和模块总结中的“下一步”以其记录时点为准；总体范围以 v0.3 为准，最新轮次和验收顺序见模块017。模型 API、执行 SDK、框架及界面产品的选型均不等于已完成接入。
