# 模块总结 003：Harness 复用与持久会话

日期：2026-10-29。设计基准：[v0.2](MAGI_产品与技术设计_v0.2.md)。

状态：有界复用试验通过；已建立集中配置、只读工具与跨进程会话入口。第二模型提供方、长期记忆、计划写入、三人格与完整个人助手 MVP 尚未完成。

## 1. 本轮决策与范围

用户确认先复用现成执行框架，避免继续扩建通用 runner。本轮安装并验证 Deep Agents SDK，现有 `agent/runner.py` 保留为学习和对照实现；原文件工具及其校验被实际复用。调查报告及证据模块未删除，也没有把自然聊天强制改成调查报告 JSON。

独立创建 `.venv-harness`，使用现有 Python 3.12.3，原 `.venv` 未升级。仍调用本机已有 `qwen3.5:4b`；未下载模型、未接付费 API、未迁入另一个项目。旧 Agent 项目未提供可核验仓库，本轮不声称复用了其代码。

这次成功证明所选执行组件能承接小型流程，不证明 Deep Agents 在质量、速度或成本上优于原 runner。未做同任务性能对照，也不决定未来全部专业能力必须使用同一框架。

## 2. 实际改动与责任分配

| 文件 | 作用 |
| --- | --- |
| `src/magi/harness/config.py` | TOML 集中配置，严格类型与未知字段检查，路径基于配置文件解析 |
| `src/magi/harness/runtime.py` | 接入现成执行图与 SQLite checkpointer；配置模型、只读边界、预算和事件 |
| `src/magi/harness/cli.py` | 一次消息、交互聊天、历史查看；通过 `--session` 续接 |
| `magi.harness.toml` | 模型、服务地址、资料目录、存储路径与预算 |
| `tests/test_harness_config.py` | 配置边界测试，无框架依赖 |
| `tests/test_harness_runtime.py` | 真实执行图和 SQLite 的离线集成测试，模型由脚本替身提供 |
| `docs/examples/harness_note.txt` | 小型合成验收资料，不是项目进度或开发指令 |
| `pyproject.toml` | 声明可选 `ollama`、`harness` 依赖组 |
| `requirements-harness.lock.txt` | 本机试验环境的依赖版本快照，67 个包；MAGI 可编辑安装单独处理 |
| `.gitignore` | 排除独立环境和 `.magi` 本地数据 |

框架负责模型—工具往返和 checkpoint。MAGI 负责开放哪些工具、配置、事件、输入及输出验收。本轮只复用原文件执行器，未额外实现通用状态引擎。

```text
CLI / 用户消息
    → 配置与会话 ID
    → Deep Agents / LangGraph 执行循环
         → ChatOllama → 本机模型
         → magi_read_file / magi_list_files → 原 MAGI 文件工具
    → SQLite checkpoint + 本地事件 JSONL
    → 返回回答（仅代表执行结束，不代表结论已验证）
```

## 3. 关键边界

- `tools=` 是添加工具，不会自动移除框架默认工具。本轮排除默认工具、禁用默认子代理，并在模型曝光和实际调度处使用两个只读工具的白名单。
- 暴露名称为 `magi_read_file` / `magi_list_files`，内部调用原 `create_file_tools`，沿用路径、UTF-8 和 32768 字节读取检查。
- 不使用真实文件系统写后端、shell 或 MCP；文件系统中间件保留框架要求的结构，但其工具不对模型开放。
- 摘要、内容卸载和子代理未启用，避免本轮出现未覆盖的额外模型调用。输入采用 UTF-8 字节大小加输出与模板预留的保守容量检查，它不是精确 tokenizer；较长文本可能提前被拒绝。
- 每次用户输入分别限制模型请求和工具调用。工具批次整体超限时，在执行前拒绝；模型次数用尽前已经发出的合法只读工具可能执行，然后在下一次模型请求前失败。这与原 runner 的末轮处理并不完全相同。
- 非空但被长度上限截断的回答、无法解析的工具请求、空回答均不能记为成功。
- `run_completed` 标记 `scope=execution_only`，没有事实支持、资料时效或业务操作成功的含义。模块 002 的语义缺口仍未解决。
- 事件立即追加到本地 JSONL，仅记录事件、身份、时间和错误类型；不保存提问及文件正文。SQLite checkpoint 包含聊天与工具正文，属于本机持久数据。此试验显式禁用外部 tracing。
- 同一数据库路径与相同 `thread_id` 才能续接。每轮只提交新增用户消息，不重新提交全量历史。不同 ID 不共享历史。
- 上次 checkpoint 存在待执行节点时，拒绝自动重放，提示查看历史并另开会话。异常恢复、并发会话服务、自动重试和写入幂等尚不在本模块范围。

## 4. 版本与安装

已验证：Deep Agents 0.7.32、LangChain 1.4.7、LangGraph 1.2.18、langchain-ollama 1.1.0、langgraph-checkpoint-sqlite 3.1.2、Ollama Python SDK 0.6.3。这里只记录本轮安装版本，不宣称它们永远最新。

在仓库根目录用 PowerShell 重建独立环境：

```powershell
.\.venv\Scripts\python.exe -m venv .venv-harness
.\.venv-harness\Scripts\python.exe -m pip install -r requirements-harness.lock.txt
.\.venv-harness\Scripts\python.exe -m pip install --no-deps -e .
```

也可使用 `pip install -e ".[harness,dev]"` 获取直接依赖限定的版本，但传递依赖可能与本次快照不同。版本快照未加包哈希，未验证其他操作系统。新机器仍需准备 Ollama 服务及目标模型；安装 Python 包不会自动准备模型。

## 5. 使用与参数解释

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 -m magi.harness.cli --session study
```

输入普通消息；输入 `/exit` 退出。再次运行相同命令，恢复同一会话。`-X utf8` 让 Windows 管道中的中文输出使用 UTF-8；它不是模型参数。

一次发送与查看历史：

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 -m magi.harness.cli --session study --message "本会话的临时口令是 MAPLE-126，请回复收到。"
.\.venv-harness\Scripts\python.exe -X utf8 -m magi.harness.cli --session study --message "刚才的临时口令是什么？"
.\.venv-harness\Scripts\python.exe -X utf8 -m magi.harness.cli --session study --history
```

`--history` 不请求模型；省略 `--session` 时生成新 ID。不要用多个进程同时写同一个会话，本轮不是并发服务。输入超过容量、工具失败或模型超限时返回非零退出码。

配置中的 `num_ctx=8192` 是当前模型上下文窗口，`num_predict=512` 是每次生成上限，`timeout_seconds=60` 为 HTTP 超时而非整轮绝对时间上限；`model_calls=4`、`tool_calls=3` 为本轮调用预算。这些参数用途不同，不能靠无限调大它们消除业务错误。

修改模型名称与地址集中在 TOML 和 `build_model`，实际只验证了 Ollama。9B 虽本地存在，本轮没有使用；也没有完成不同 API 提供方的等价能力测试。

## 6. 真实运行证据

本机 `qwen3.5:4b`、会话 `m003-live-20261029-a`，以下每一步均使用独立 Python 进程：

| 步骤 | 操作 | 实际观察 |
| --- | --- | --- |
| 1 | 告知合成临时口令 `LANTERN-7391` | 回答“收到”；1 次模型请求、无工具 |
| 2 | 重新启动后询问口令 | 正确回答 `LANTERN-7391`；1 次模型请求、无工具 |
| 3 | 要求读取 `examples/harness_note.txt` | 实际调用 `magi_read_file`，正确回答 `ORBIT-4286` 和学习任务；2 次模型请求、1 次工具 |
| 4 | 新进程使用 `--history` | 显示以上三轮消息与回复，无模型请求 |

合计 4 次真实模型请求、1 次只读工具调用。原始事件保存在本机 `.magi/harness-events.jsonl`，checkpoint 在 `.magi/harness.sqlite3`，均未纳入版本控制。这是一次小样例连通性与恢复验证，不是成功率或长期记忆效果评测。

## 7. 检查结果

2026-10-29 的最终检查：

| 命令 / 范围 | 结果 |
| --- | --- |
| `.venv-harness` 中 `python -X utf8 -m pytest -q` | 全仓 **155 passed in 4.57s**：原有 105 项、配置 26 项、harness 集成 24 项 |
| 原 `.venv` 中 `python -X utf8 -m pytest -q` | **131 passed, 1 skipped**；没有安装 harness，跳过其整个可选集成测试模块 |
| `.venv-harness` 中 `python -m pip check` | 无依赖冲突 |
| Ruff check / format --check | `src/magi/harness` 与两份新增测试，共 6 个文件通过 |

新增测试覆盖配置、SQLite 恢复、会话隔离、工具白名单、路径和参数错误、预算、异常恢复边界、输出截断、无效工具调用，以及用户输入/工具输出/恢复历史的容量拒绝。模拟测试显式禁止网络连接。Ruff 的检查范围不等于全仓格式状态；没有为了清理历史格式而重写旧学习代码。

## 8. 与设计验收对照

| 验收 | 本轮结果 |
| --- | --- |
| PA-01 日常对话 | 小样例通过；普通消息不默认三人格，不开放写工具 |
| PA-02 重启恢复 | 短会话在离线与真实跨进程实验中通过 |
| PA-03 记忆管理 | 未实现；会话历史不是已确认长期记忆 |
| PA-04/05 资料到计划、次日修改 | 只验证读取；保存和更新未实现 |
| PA-06 重复操作与中断 | 拒绝自动重放未完成执行；写入幂等尚待模块 004 |
| PA-07 三人格 | 未实现 |
| PA-08 模型配置 | Ollama 配置集中化通过，第二提供方待接入 |
| PA-09 失败与预算 | 有界只读流程的离线异常场景通过 |
| PA-10 资料支持和时效 | 沿用模块 002 已知缺口；没有新增语义验证 |

## 9. 学习与下一步

建议依次阅读：`magi.harness.toml` → `config.py` → `cli.main` → `open_session` → `HarnessSession.ask` → 两个工具适配函数。重点理解：框架运行状态、用户业务数据、模型上下文是不同层；SQLite checkpoint 持久化不会自动赋予内容真实性。

小练习：用两个不同 `--session` 设置不同临时口令，退出后分别查询；观察它们的隔离。然后将试验配置的 `tool_calls` 设为 0，用新会话要求读取文件，查看失败事件，完成后恢复原值。能说明“模型提出调用”与“程序实际执行”之间的区别即可，不要求重写整套循环。

用户在本轮提出后续采用[设计—执行—Review 流程](MAGI_协作开发流程.md)。下一个执行模型按[模块 004 任务书](MAGI_开发任务书_004_记忆与计划.md)先完成已确认记忆的完整单元，随后再做计划与任务；本轮不开始这些业务实现。

本次接入参考 [Deep Agents 定制](https://docs.langchain.com/oss/python/deepagents/customization)、[文件后端](https://docs.langchain.com/oss/python/deepagents/backends)、[Harness profiles](https://docs.langchain.com/oss/python/deepagents/profiles) 和 [LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)，并核对实际安装源码和离线行为。profile API 为当前依赖中的 beta 扩展，因此锁定版本，升级需重新检查工具曝光、摘要和预算行为。
