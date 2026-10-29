# MAGI 开发任务书 004：已确认记忆与版本计划

日期：2026-10-29。状态：**历史实施约定，004A/004B 已有实现与离线验证记录**。实际交付与限制见各自模块总结；聊天内确认整合仍待实现。当前整体开发计划见 [v0.3](MAGI_MVP整体设计与七天改造计划_v0.3.md)，不再按本文的单单元边界限制整个 MVP 推进。下文保留当时的任务约定，不是完成报告。

产品基准：[产品与技术设计 v0.2](MAGI_产品与技术设计_v0.2.md)。执行时先阅读最新模块 003 总结、实际代码和测试，核对与本文的差异。003 尚未验收的事项不得因本任务书出现而视为通过。

## 1. 交付目标与推进方式

让用户能明确保存少量个人偏好，查看、更正、删除它们；能保存、查询和修改有版本的个人计划。程序以数据库记录确认操作结果，模型可以解释和提出候选，不能自行宣称写入完成或批准动作。

分两个完整学习单元推进：

| 单元 | 用户能实际完成的事情 | 本轮结束必须交付 |
| --- | --- | --- |
| 004A 记忆 | 显式保存偏好，重启后查询；更正或删除后旧偏好不再进入新的模型请求 | 实现、离线验证、真实模型观察、流程解释、小练习、004A 总结 |
| 004B 计划与任务 | 保存计划、重启读取、带版本更新；重复提交不产生重复产物 | 实现、故障验证、使用演示、流程解释、小练习、004B 总结 |

默认先执行用户指定的一个单元。一次完成该单元的代码、必要测试与解释，不拆成逐字段确认；不要未经用户推进就把两个单元和后续界面全部做完。用户可先阅读完整参考实现，再完成一个小改动并集中 Review。

执行模型开始时：检查工作区变更，保留用户已有文件；读本任务书及相关代码；说明本单元交付；实现并运行相应验证；最后给出运行方法、学习讲解和未通过项。遇到局部实现选择可自行决定并记录，只有改变产品范围或关键约束时再提出问题。

## 2. 范围与现有基础

限定 Python 3.12、标准库 `sqlite3`、已有 Pydantic/Deep Agents/LangGraph 依赖。沿用 `.venv-harness`，不引入新的模型服务、Web 框架、向量数据库、ORM、消息队列或长期调度器；不以升级整个依赖集解决局部问题。

本模块先用**明确的 CLI 操作提交业务写入**。自然语言聊天仍以查询、解释和生成候选为主；把“帮我保存”可靠转换成绑定参数的会话内确认流程，留给后续整合。不得因此宣称完整 PA-04 已完成。

| 已有路径 | 可复用内容 | 不应误认为已经具备 |
| --- | --- | --- |
| `src/magi/harness/config.py` | TOML 配置、路径解析与参数校验 | 业务库配置及记忆策略 |
| `src/magi/harness/runtime.py` | 模型接入、只读白名单、调用预算、模型输出检查、事件、SQLite checkpoint | 长期记忆、业务授权或业务事务 |
| `src/magi/harness/cli.py` | 新建/恢复会话、一次性消息、查看历史 | 记忆与计划管理命令 |
| `src/magi/agent/file_tools.py` | 授权目录内的有界读取与目录枚举 | 任意文件写权限 |
| `src/magi/agent/investigation.py` | 证据编号、哈希、结构与引用校验思路 | 当前 harness 已登记完整证据，或结论语义必然正确 |
| `tests/test_harness_config.py`、`tests/test_harness_runtime.py` | 配置、工具边界、调用限制、续聊等基线 | 004 的验收结果 |

当前 checkpoint 会把同一物理 thread 的旧消息继续送给模型，且摘要未启用。**只在 system prompt 追加“忘掉旧偏好”不满足记忆撤销要求。** 必须落实第 5 节的上下文隔离。

早期 `Ballot`、`DecisionCase` 不作为记忆、计划或操作任务的数据模型。旧 runner 和测试保留，不在本模块重写。

## 3. 存储职责与数据对象

建议新增独立业务数据库 `.magi/personal.sqlite3`，配置键为 `[storage].app_db`。与既有 `checkpoint_db`、事件 JSONL 使用不同文件；路径按配置文件解析，拒绝相同目标。业务迁移只操作业务库，不能修改 LangGraph 内部表。验证和演示使用临时配置/临时业务库，不清空用户已有会话。

数据库初始化启用外键，维护明确 `schema_version`，已有数据库使用顺序迁移；不以删库重建代替迁移。所有 SQL 值参数化。时间统一存 UTC，显示时说明时区。标识由程序生成，不让模型决定数据库主键是否可信。

| 建议对象 | 最小数据与不变量 |
| --- | --- |
| AppState | 单用户的 `context_epoch`；初始为 0，有效记忆变化时递增 |
| Memory | `id`、`key`、`scope`、`value`、`status`、`version`、来源、确认/更新时间；`ACTIVE/DELETED`；相同 key 与 scope 最多一个 ACTIVE 项 |
| MemoryChange | 操作 ID、记忆 ID、动作、旧/新版本、时间和来源标识；不在普通审计日志重复保存偏好正文 |
| ConversationBinding | 用户可见会话 ID、epoch、内部 checkpoint thread ID；一个逻辑会话可关联多段历史 |
| Plan | `id`、`title`、当前版本；正文及来源放入不可变 PlanRevision |
| PlanRevision | 计划 ID、版本、正文、来源标识、创建时间、生成时 epoch、关联操作 ID；更新创建新版本，不覆盖旧正文 |
| Operation / Task | 本次显式业务动作的 ID、`request_id`、动作、目标、期望版本、参数摘要、状态、结果 ID/版本、错误码和时间 |

本单元的 Task 指一次同步本地业务操作，不等同于长期 Agent 作业。先实现成功、已确认失败和取消/未提交的明确表达；跨进程长任务的运行、租约、自动续跑等不是 004 的范围。

Memory 来源区分 `explicit_cli` 与用户确认的候选。CLI 直接输入可以没有来源消息 ID，不能伪造聊天来源。模型生成的候选保持未生效，不因输出格式正确而变成 ACTIVE 记忆。

scope 第一版限定 `global` 或 `session:<逻辑会话ID>`。上下文只选择 global 与当前会话项；同 key 的当前会话项优先，属于明确作用域规则，不按更新时间猜测覆盖关系。更新已删除项应拒绝；用户重新添加相同 key 是显式新记忆，生成新 ID，不自动复活旧正文。

记忆删除建议保留 ID、版本、删除时间等 tombstone，将业务表中的当前正文清空。原始聊天可能仍在旧 checkpoint 中供用户查看；本模块实现的是**有效记忆撤销及推理上下文失效**，不承诺从全部历史、备份和磁盘物理擦除正文。

计划来源必须区分用户声明的来源与程序实际读取并核对的来源。候选 JSON 中的 `verified=true` 不可信；只有程序从真实读取记录确认的引用才能标记已读取。引用结构正确不证明计划受原文支持。

## 4. 应用接口与显式操作入口

建议以小型 service/repository 实现，不先建设通用工作流引擎。具体类型可用 dataclass 或已有 Pydantic，业务结果必须可以脱离模型测试。

```text
MemoryService
  add(key, scope, value, request_id, source) -> Receipt
  list_active(scope=None) -> list[Memory]
  get(memory_id) -> Memory | NotFound
  update(memory_id, expected_version, value, request_id) -> Receipt
  delete(memory_id, expected_version, request_id) -> Receipt

PlanService
  create(title, body, sources, request_id, source_epoch) -> Receipt
  get(plan_id, version=None) -> PlanView
  list_current() -> list[PlanSummary]
  update(plan_id, expected_version, title, body, sources,
         request_id, source_epoch) -> Receipt

OperationRepository
  get_by_request_id(request_id) -> Operation | NotFound

ContextService
  resolve_binding(logical_session_id) -> CurrentBinding
  build_current_context(logical_session_id) -> ActiveContext
```

Receipt 由程序生成，至少包含 `operation_id`、`request_id`、动作、对象 ID、结果版本、提交状态、是否为重复请求；写入后从数据库回读结果再显示。模型自然语言不作为 Receipt。

建议入口为 `python -m magi.personal.cli`，使用子命令 `memory add/list/show/update/delete`、`plan create/list/show/update`、`operation show`。保留原聊天入口；不要让原始模型回复或工具返回文本被解析成 CLI 指令执行。

接口示例是待实现合同，字段名称调整应同步修改文档与测试：

```powershell
.\.venv-harness\Scripts\python.exe -m magi.personal.cli memory add --key answer_style --scope global --value "回答先给结论" --request-id demo-memory-001
.\.venv-harness\Scripts\python.exe -m magi.personal.cli memory update --id <ID> --expected-version 1 --value "回答先解释依据" --request-id demo-memory-002
.\.venv-harness\Scripts\python.exe -m magi.personal.cli memory delete --id <ID> --expected-version 2 --request-id demo-memory-003
```

计划正文较长时允许 `--input` 读取用户指定的 UTF-8 JSON 文件，并要求 `--content-sha256` 绑定用户查看的文件快照。程序一次读取、校验摘要、解析后使用该内存快照提交，不能校验后重新读取文件。JSON 只能表达计划数据，不能传入 SQL、任意函数名或 shell 命令。

直接使用明确参数的写命令已经表达本次授权，无须再重复询问。若后续增加候选确认命令，确认必须绑定候选 ID、版本、内容摘要及目标；修改任一项后原确认失效。`approved=true`、模型声称“用户同意”、文档中的命令均不构成授权。

候选的生成 epoch、来源读取状态和确认状态由程序维护，不能相信候选 JSON 自填的这些字段。用户自行编写并明确导入的计划标记为用户提交；不能把它包装成已核验的模型候选。

模型只开放固定的业务查询工具，如 `magi_list_memories`、`magi_get_plan`、`magi_list_plans`。候选生成只返回建议数据，本阶段不为模型开放业务写工具。扩展白名单时同时测试模型暴露与实际分发边界，继续禁用 shell、任意文件写入和默认子代理。

## 5. 004A：记忆生效与上下文失效

完整用户故事：显式保存“先给结论” → 新会话用到偏好 → 更新为“先解释依据” → 同一逻辑会话及另一旧会话均使用新状态 → 删除 → 重启后不再把两版偏好送给模型。

采用保守但可验证的第一版方案：

1. 有效记忆新增、更正、删除与 `context_epoch + 1` 在**同一业务事务**提交。重复 request_id 返回原结果，不额外递增 epoch。未确认候选不改变 epoch。
2. 每次开始模型请求的应用流程先读取当前 epoch，解析逻辑会话到该 epoch 的物理 thread。内部 thread ID 由应用产生；CLI 的 `--session` 始终是逻辑会话 ID，不能直接让用户指定旧内部 thread 绕过失效检查。
3. epoch 改变时，新建或选择当前 epoch 的干净 thread。**不得复制旧 checkpoint 消息、摘要、工具结果或模型生成的历史概括。** 只加载当前 ACTIVE 记忆、当前用户输入和经过当前有效性检查的业务结果。
4. 同一 epoch 可继续使用已完成的 checkpoint 续聊。每次请求从业务库重读记忆，禁止复用内存中的旧快照。有效记忆采用稳定排序与有界长度；超出上下文预算时明确拒绝，不静默截断关键约束。
5. 原 checkpoint 不删除；`--history` 可以按逻辑会话分段展示旧记录，并标记历史段/当前段。历史查看不调用模型，且原始历史查询不作为模型工具暴露。
6. 首次接入 004 的已有 003 会话作为历史段保留，创建受 epoch 管理的新段，不把旧 thread 的任意个人表达直接当作已确认记忆。

这是有意选择的取舍：记忆变更后，当前聊天的短期上下文会重新开始，用户仍能查看旧历史。CLI 必须说明这一点。后续可以研究按消息来源精确过滤，但不能用未经验证的摘要“恢复连续性”同时宣称撤销已生效。

上下文失效还要覆盖候选、计划及缓存：

- 候选绑定生成时 epoch；epoch 不一致时拒绝确认，要求重新生成或由用户重新提交完整内容，不能只替换 epoch 数字。
- 模型不自动加载所有旧计划。计划视图带版本、来源与生成时 epoch；对依赖旧记忆或无法判断依赖的旧计划采用保守策略，标记 `needs_review`，模型查询先返回 ID/状态，不返回可能带旧偏好的正文。
- 用户可以在 CLI 查看旧正文，完成检查后以明确命令提交新的计划版本；这项动作与参数绑定，不由模型自行清除 `needs_review`。若尚未保存依赖关系，可先把旧 epoch 的全部计划正文视为待复核，并记录该限制。
- 本单元不引入摘要或向量缓存。将来加入时必须以 epoch/版本失效，不能另建不受本规则控制的记忆通道。

测试使用只出现于旧偏好及旧历史的唯一标记，捕获模型的实际输入，确认旧标记没有从历史、系统消息、工具结果或缓存进入新请求。用户在新的消息中主动再次输入旧内容属于新输入，不属于系统偷偷恢复旧记忆。

本阶段限定一个活跃应用进程；使用标准库可实现的进程锁覆盖聊天与业务管理入口，第二个进程应提示占用，不并发更改记忆后仍让旧进程继续发送缓存内容。退出/异常必须释放锁。SQLite 本身的写锁仍负责数据库事务，进程锁不能代替事务与版本检查。

因此交互聊天需先退出，再通过管理命令修改记忆，随后恢复同一逻辑会话。运行说明要写清此限制，不宣称已经支持多个窗口同时使用。

## 6. 004B：计划版本、幂等与事务

完整用户故事：用户检查计划候选 → 通过显式命令保存 → 重启后读取相同 ID/版本 → 修改一项 → 重复同一请求仍得到同一结果 → 旧版本更新被拒绝 → 模拟提交前后中断，查询真实状态。

计划初始版本为 1；更新必须提供 `expected_version`，成功只产生一个新版本。更新 SQL 的条件包含对象 ID 和期望版本，并核对受影响行数；冲突返回当前版本和稳定错误码，不自动覆盖或由模型合并。正文和标题设置明确非空/长度上限，查询结果继续计入 harness 上下文预算。

`request_id` 是一次业务操作的幂等键，跨重试保持不变。服务计算包含动作、目标、期望版本及规范化数据的摘要：

- 同键、同摘要、已成功：返回已提交 Receipt，不重复写对象、版本、事件或 epoch。
- 同键、不同摘要：返回 `IDEMPOTENCY_CONFLICT`，不能把新动作当成旧请求成功。
- 内容相同但请求键不同：这是两个明确请求，不按文本相似度悄悄合并。
- 同键已确认失败/取消：返回原终态；修正参数或重新发起必须使用新键。

一次同步本地操作的业务变更、版本记录、Operation 成功记录、必要业务审计及 epoch 更新（如涉及记忆）放入同一个业务库事务。可使用 `BEGIN IMMEDIATE` 串行化写入；锁等待有界，忙碌时明确失败。模型调用、文件转换等耗时外部动作不得放在写事务内。

业务事务未提交就中断时，SQLite 回滚，本次无成功 Receipt。若需要记录失败，在回滚后的独立事务保存失败 Operation；若进程已死亡而来不及记录，查询应返回“没有已提交操作记录，不能判定成功”，不能伪造一条已成功或已失败记录。

事务已经提交但终端输出前中断：重启后用相同 request_id 查询或重试，必须得到原 Receipt。程序不自动重放未知操作；对于本模块全部副作用都在同一业务事务内的动作，可在用户明确重试时根据该事务的持久结果决定返回或执行。未来外部 API 写入不能直接套用此结论。

业务数据库与 harness checkpoint **不做跨库原子提交**。业务库的 Operation/版本是写入事实来源；checkpoint 和 JSONL 事件用于对话及诊断。日志失败不能撤销已经提交的业务事实，也不能把“回答输出失败”说成“计划保存失败”。Receipt 显示失败时仍应提示用 request_id 查询。

当前同步操作不需要持久暴露完整 CREATED/RUNNING 状态机；若实现者增加这些状态，就必须另行解释提交与恢复规则并验证中断场景，不能仅添加几个枚举值。

## 7. 验收矩阵与证明边界

| 编号 | 最低验收 | 本模块可以声明的范围 |
| --- | --- | --- |
| 004A-01 / PA-03 | 新增、查询、更正、删除，重启结果一致；来源、版本、epoch 正确 | 显式管理入口的有效记忆生命周期 |
| 004A-02 / PA-03 | 更新/删除后，当前与其他旧逻辑会话的实际模型请求不含撤销值；新段只含当前有效记忆 | 程序上下文失效，不宣称模型绝不猜测同类内容 |
| 004A-03 / PA-01 | 普通聊天、文档注入和模型伪造批准不能写入 ACTIVE 记忆 | 授权与工具边界 |
| 004A-04 / PA-02 | 同 epoch 正常续聊；epoch 变化后历史可看、推理段重建；重启依然如此 | 有明确失效规则的会话连续性 |
| 004B-01 / PA-04 部分 | 显式保存计划、回读 ID/版本/正文，区分来源声明与真实读取证据 | 业务保存闭环；自然语言提出→确认→执行整合尚未验收 |
| 004B-02 / PA-05 部分 | 重启后按 ID 找回当前计划，以 expected_version 更新，旧版本可查看 | 显式入口的跨进程计划调整；聊天内完整体验另验 |
| 004B-03 / PA-06 | 重复键不重复产物，变更参数冲突，过时版本不覆盖 | 幂等与乐观并发控制 |
| 004B-04 / PA-06 | 提交前故障回滚；提交后响应失败仍可查询原 Receipt；无未知自动重放 | 本地 SQLite 事务边界，不外推为外部系统恰好一次 |
| 004B-05 / PA-03、PA-09 | 旧 epoch 候选不能批准；待复核计划不向模型泄漏旧正文；失败/预算不足不报成功 | 记忆与计划的交叉失效 |

离线测试证明字段、不变量、事务、白名单及**实际发给模型的内容**。真实模型运行证明该模型在给定样例中理解查询、使用工具和表达结果的表现。两者分别记录：一次真实聊天回答“不记得了”不足以证明旧内容未被发送；fake model 通过也不足以宣称真实模型稳定。

未完成自然语言写入整合、长期真实使用及界面验收前，不能把 PA-03～06 全部标为完整产品通过。已知 4B 模型问题如实记录，不用无限增加提示词掩盖业务边界缺失。

## 8. 必要测试与运行安排

004A 至少覆盖：空值/超长/非法版本；同 key/scope 冲突；未确认候选不生效；重复记忆请求只变更一次；删除后查询；同会话/其他旧会话/重启三种上下文失效；旧历史只查看不送模；旧003会话迁移；第二进程占用拒绝。

004B 至少覆盖：创建和回读；版本更新与冲突；同键同参数/不同参数；一次更新只增一个版本；两次顺序使用旧版本不能互相覆盖；事务提交前异常；提交后终端故障；不同进程读取同一业务库；来源标记不可由模型自封；旧 epoch 的候选和计划正文隔离。

所有业务测试使用临时目录与 SQLite 文件；上下文测试用能记录最终模型请求的替身。故障点通过清晰的依赖注入/测试钩子触发，不用概率性的强杀代替可重复断言。另加一个子进程重启验证，避免只重建 Python 对象冒充跨进程持久化。

实施后按新增文件调整命令，示例不是当前测试已存在的证明：

```powershell
.\.venv-harness\Scripts\python.exe -m pytest tests/test_personal_memory.py tests/test_personal_context.py -q
.\.venv-harness\Scripts\python.exe -m pytest tests/test_personal_plans.py tests/test_personal_operations.py -q
.\.venv-harness\Scripts\python.exe -m pytest -q
.\.venv-harness\Scripts\python.exe -m ruff check src/magi/personal src/magi/harness tests/test_personal_memory.py tests/test_personal_context.py tests/test_personal_plans.py tests/test_personal_operations.py
```

每个单元先运行其对应测试，再回归受影响的 harness、文件工具和配置测试；完整回归在单元最终改动稳定后执行。尚未创建的另一单元测试不必提前搭空壳。已有无关失败单独记录，不能修改测试断言以制造全绿。

真实模型演示使用无隐私的固定样例和单独数据库。004A 演示一次偏好新增/更正/删除及重启；004B 演示一次计划查询、显式保存/更新、重复请求。保存命令、版本、事件和结果摘要，测试数据不得混入用户的正式记忆。

## 9. 建议文件清单

只创建本单元用到的文件；命名可以在有理由时合并，但职责不能混入 CLI：

| 路径 | 责任 |
| --- | --- |
| `src/magi/personal/models.py` | 记忆、计划、操作与 Receipt 类型及业务错误 |
| `src/magi/personal/store.py` | SQLite 连接、迁移、事务与 repository |
| `src/magi/personal/memory.py` | 004A 业务服务、确认和 epoch 变更 |
| `src/magi/personal/context.py` | 逻辑会话映射、有效上下文与历史分段 |
| `src/magi/personal/plans.py` | 004B 计划版本、来源和复核规则 |
| `src/magi/personal/operations.py` | 幂等参数绑定与操作查询；规模小时可合入 store/service |
| `src/magi/personal/cli.py` | 显式业务管理入口及 Receipt 展示 |
| `src/magi/personal/tools.py` | 仅查询/候选工具的框架适配 |
| `src/magi/harness/config.py`、`runtime.py`、`cli.py` | 新配置、上下文组装与逻辑会话接入；保持模型接入可替换 |
| `magi.harness.toml`、README | 可运行配置与实际使用说明 |
| `tests/test_personal_*.py` | 第 8 节必要场景；继承现有 harness 回归 |
| `docs/MAGI_模块总结_004A_已确认记忆.md` | 004A 设计对照、证据、学习记录与限制 |
| `docs/MAGI_模块总结_004B_计划与操作任务.md` | 004B 设计对照、证据、学习记录与限制 |

不要在业务层直接依赖 LangGraph checkpoint 表结构。可以通过框架公开接口查看历史，通过 ContextService 管理映射；跨库恢复顺序是“先读取业务 epoch/映射，再选择物理 thread”，不能倒过来用旧 checkpoint 恢复应用当前状态。

## 10. 学习讲解、小练习与留痕

004A 讲解围绕一条完整路径：CLI 参数 → service 校验 → repository 事务 → epoch 变化 → 新上下文 → 模型请求。解释确认与候选的区别、业务记忆与对话历史的区别，以及为什么增加一条“忘记”提示不能保证撤销。

004A 小练习：用户为 `memory list` 增加按 scope 过滤，并补一个跨 scope 不混用的测试；不要求重写整套持久化代码。

004B 讲解围绕同一个 request_id：首次提交、重复提交、旧版本冲突、提交前失败、提交后输出失败。解释事务、唯一约束、幂等键和 expected_version 各自解决什么问题，为什么 JSONL 日志不是业务真相来源。

004B 小练习：用户给计划查询增加标题关键词过滤，并验证它不改变对象版本、操作状态或记忆 epoch。对用户改动集中 Review，说明原因，不只贴替换答案。

每个单元的总结必须写：对应本任务书与 PA 编号；实际新增/修改文件；实际依赖版本；测试命令、范围、结果；真实模型样例与限制；业务数据迁移；用户学习和小练习结果；未通过项与下一步。候选方案、已实现行为和已验证行为分开写。

用户切换执行模型后可直接给出：“阅读任务书 004，执行 004A；完成可运行实现、必要验证和学习讲解，保留已有模块。”执行模型不能因文件中存在 004B 计划而自行宣布整个模块已完成。
