# MAGI 模块045：三人格研究与有限协作

日期：2026-11-04。对应设计契约：[个人研究与行动助手 v0.4](MAGI_个人研究与行动助手设计_v0.4.md)。

2026-11-10 勘误：045 验证的是研究聊天历史隔离。其已确认记忆查询曾漏掉作用域过滤，现已在[模块046](MAGI_模块总结_046_研究记忆作用域隔离.md)修复。下文涉及记忆读取的描述以该修复为准。

## 1. 本轮解决的业务问题

此前 MAGI 能聊天、审议并保存计划，但没有一条“先查资料、形成可引用报告、由其他人格独立找问题、用户确认后再真正发布”的完整路径。用户如果让模型研究技术方案，模型回复、程序校验、其他人格意见和实际保存容易混在一起：页面显示完成，不代表证据有效；评审说 PASS，不代表程序检查过它；数据库有草稿，也不代表磁盘上已有正式文件。

本轮按照 v0.4 设计建立了新的研究路径。三个入口使用同一个手写循环和同一套只读工具，差异来自稳定的人格配置；共同聊天室可以指定一名主研究员和最多两名评审。主研究员先生成不可变 v1，评审只读该版本并独立给意见；如有问题，主研究员最多修改一次生成 v2，原评审再复核一次，然后无论通过与否都停止。用户看到报告后，仍要显式批准，后端才把精确版本发布为 Markdown 文件。

这里“手写”是指研究编排、预算、版本和退出状态由 MAGI 代码明确控制，并不表示重复实现模型 SDK、Pydantic、SQLite、FastAPI 或已有只读工具。旧聊天、旧审议和 LangGraph 工作单保持兼容，没有在本轮删除或伪装成已经迁移。

## 2. 用一个场景理解完整流程

假设用户在网页共同聊天室输入“比较当前项目继续使用 SQLite 与迁移 PostgreSQL 的取舍”，选择 MELCHIOR 主研究，邀请 BALTHASAR 和 CASPER 评审。

页面先提交一个后台任务。后端为它生成 `task_id`，冻结本次请求、主研究员、评审者、联网开关和显式共享的消息。`ResearchService._context` 只装入当前请求、同一研究聊天室的近期成果、已确认记忆和用户明确分享的消息；另一个私聊里的内容不会因为“可能有用”而自动混入。材料太多时先去掉最旧的可选历史，再去掉较早记忆，并记录省略原因；如果连本次要求、权限和显式分享这些必带材料都放不下，任务在调用模型前失败。

MELCHIOR 通过 `PersonaRunner.generate` 使用只读文件工具，用户开启联网时才增加联网读取工具。模型返回结构化报告，程序再验证 JSON、字段长度和 `[evidence:tool_call_id]` 引用是否来自本次真实工具结果。验证通过后只写 SQLite 草稿，不创建 Markdown 文件。

BALTHASAR 和 CASPER 分别读取同一份 v1。二者只拿到报告、来源和最小任务说明，不会看到主研究员的隐藏推理。程序要求它们分别检查需求覆盖、证据、已完成工作、未知项和权限边界。若任一评审要求修改，主研究员必须逐项说明接受或保留每个 `finding_id` 的理由，产生 v2。v1 正文仍可读，但状态改为 `UNRESOLVED`，因为旧结论不能传播到新版本。原评审再检查 v2；全部 PASS 才标记 `PASSED`，否则标记 `UNRESOLVED` 和 `PARTIAL`，不会进入第三轮。

最后用户批准 v2。请求同时携带 `version=2` 和正文的 `payload_hash`。如果这期间出现 v3，或者正文摘要不一致，后端拒绝旧页面的批准。批准先记录数据库回执，再原子发布固定文件；如果响应丢失，重复同一 `request_id` 返回原回执，不生成第二份文件。页面刷新时读取任务、版本、评审和回执，不会重新触发模型。

## 3. 入口、人格和手写执行循环

三个角色的持久身份是小写 `agent_id`，展示名和提示配置可以随版本演进。以下是交付代码的实际配置：

```python
# src/magi/personas.py
PERSONAS = {
    "melchior": PersonaConfig(
        "melchior", "MELCHIOR", "research-v1",
        "优先检查证据、因果和可验证性；明确区分实测、来源陈述与推测。",
    ),
    "balthasar": PersonaConfig(
        "balthasar", "BALTHASAR", "research-v1",
        "优先检查维护负担、用户精力、恢复成本以及失败后的实际影响。",
    ),
    "casper": PersonaConfig(
        "casper", "CASPER", "research-v1",
        "优先检查个人意愿和选择，避免把主流方案或模型推测当成用户偏好。",
    ),
}
```

`get_persona(agent_id)` 只接受这三个内置身份，避免请求临时注入未审计角色。`PersonaRunner._run_loop` 是三个人格共用的执行入口：它根据任务分配到的模型/工具额度缩小 `ChatLoop` 配置，装配人格提示，建立只保存本次事件的 `FrozenSession`，然后调用已有模型和只读工具。工具调用事件用于计算真实消耗；共享截止时间已过时，`ChatDeadlineError` 会转换成研究层的 `BudgetExhausted`。

`generate`、`review`、`revise` 都调用 `_run_loop`，但输出模型不同。生成阶段产出 `ReportPayload`；评审阶段产出 `ReviewPayload`，并拒绝引用本评审没有读过的证据；修改阶段产出 `RevisionPayload`，每个评审问题必须恰好处理一次。证据 ID 来自实际 `tool_call_id`，不是模型自行发明的顺序号。

程序对 PASS 还做一层语义关系校验：

```python
# src/magi/research/models.py, ReviewPayload.validate_semantics
names = {item.name for item in self.checks}
if len(names) != 5:
    raise ValueError("五项共同检查必须各出现一次")
blocking = any(item.blocking for item in self.findings)
if self.verdict == "PASS" and blocking:
    raise ValueError("PASS 不能包含阻塞问题")
if self.verdict == "REVISE" and not self.findings:
    raise ValueError("REVISE 必须给出可处理问题")
if self.verdict == "INSUFFICIENT_EVIDENCE" and not self.findings:
    raise ValueError("证据不足必须说明缺少什么")
```

这只能保证输出内部不自相矛盾，不能证明结论真的被来源支持。真实内容仍要依靠评审和用户阅读。

## 4. 上下文选择与隔离

`ResearchService._context(task, turn, execution)` 在每个任务开始时形成可审计上下文。关键输入来源如下：

| 材料 | 来源 | 是否必带 | 隔离规则 |
| --- | --- | --- | --- |
| 当前研究要求 | `TurnInput.user_text` | 是 | 只属于当前任务 |
| 权限 | 请求的联网开关和系统固定写权限 | 是 | 模型只能读，不能写文件或安装依赖 |
| 已确认记忆 | `memories.status='ACTIVE'` | 否 | 046 起只读取当前业务库内 `scope='global'` 的公共记忆 |
| 同聊天室历史 | 已完成/部分完成的研究任务和成果 | 否 | 必须同时匹配 owner 与 chat |
| 显式共享消息 | `shared_message_ids` | 是 | 先校验消息属于当前 owner 且已完成 |

函数先限制候选数量，再按 UTF-8 字节数加 4096 字节提示保留量估算容量。达到上限时只删除可选材料，删除顺序和数量写进 `selection`；必带材料超过 `num_ctx` 时返回 `CONTEXT_LIMIT`，不会静默截断用户要求。这里使用字节估算是保守、便宜的程序边界，不等于模型提供方的精确 tokenizer；真实 token 利用率是后续可优化项。

评审的交接内容比生成上下文更小：报告精确版本、实际来源和五项检查要求。主研究员的会话消息不交给评审，避免“独立评审”变成顺着原答案复述。用户主动填写消息 ID 时才允许跨聊天室分享；这时 `message_id` 表示一条已经完成的网页轮次，不是 `chat_id` 或研究任务 ID。

## 5. 共享预算和有限协作状态机

一个研究任务只有一个 `ResearchBudget`。协作任务总上限为24次模型请求、32次工具调用、360秒；单人格任务为12、16、180秒。生成、初评、修改和复核另有阶段上限。两个评审共享评审阶段的6次模型/8次工具预算，每人得到3/4，而不是各自重置成6/8。

```python
# src/magi/research/budget.py, ResearchBudget.allowance
if stage in {"review", "verify"}:
    model_cap //= max(1, participants)
    tool_cap //= max(1, participants)
models = min(
    model_cap,
    stage_models - self.stage_model_used.get(stage, 0),
    self.model_limit - self.model_used,
)
tools = min(
    tool_cap,
    stage_tools - self.stage_tool_used.get(stage, 0),
    self.tool_limit - self.tool_used,
)
if monotonic() >= self.deadline:
    models = tools = 0
```

`ResearchService.run_task` 是业务状态机。它先生成或读取指定成果版本；无评审者时直接以 `NOT_REQUESTED/DELIVERED` 交付草稿。有评审者时执行第一轮；全部 PASS 就停止。否则汇总所有问题，主研究员只修改一次，再让原评审执行第二轮；第二轮无论 PASS、未通过、非法输出、异常或预算耗尽都进入终态，不重新开始无限循环。

正常退出状态是：

| 结果 | 任务状态 | 评审状态 | 含义 |
| --- | --- | --- | --- |
| 单人格交付 | `COMPLETED` | `NOT_REQUESTED` | 有有效草稿，但无人评审 |
| 初评或复核全通过 | `COMPLETED` | `PASSED` | 程序接受了评审输出，不等于内容绝对正确 |
| 修改后仍未通过 | `PARTIAL` | `UNRESOLVED` | 保留有效 v2，停止继续改写 |
| 已有草稿后预算/输出失败 | `PARTIAL` | `INCOMPLETE` | 草稿可读，评审链不完整 |
| 首次生成前失败 | `FAILED` | 无 | 没有可交付成果 |

每个阶段都有独立 `agent_run_id`，开始时写入输入清单、人格配置版本、成果版本，结束时写实际模型/工具次数和结果；失败也保存错误类别。它用于审计一次人格运行，不应与承载整个闭环的 `task_id` 混用。

## 6. 不可变版本、确认和真实发布

`ResearchArtifacts.create` 把初稿写进 `research_artifacts` 和 `research_artifact_versions`，状态仍是 `DRAFT`。`add_version` 要求调用方提供 `expected_version`；数据库当前版本不同就返回 `VERSION_CONFLICT`。新版本用新行保存，旧正文不覆盖：

```python
# src/magi/personal/artifacts.py, ResearchArtifacts.add_version
if artifact["current_version"] != expected_version:
    raise ArtifactError("VERSION_CONFLICT", "成果已有新版本，请刷新后重试")
version = expected_version + 1
db.execute(
    """UPDATE research_artifact_versions SET review_status='UNRESOLVED'
       WHERE artifact_id=? AND version=?""",
    (artifact_id, expected_version),
)
db.execute(
    """UPDATE research_artifacts
       SET current_version=?,status='DRAFT',updated_at=? WHERE id=?""",
    (version, timestamp, artifact_id),
)
```

用户确认由 `ResearchArtifacts.decide(artifact_id, request_id, version, payload_hash, decision)` 处理。四个身份字段分别解决不同问题：`artifact_id` 找到逻辑成果；`version` 固定用户看到的不可变版本；`payload_hash` 防止同一版本身份下正文不一致；`request_id` 识别网络重试。同一个 `request_id` 换成其他内容会被拒绝，同一成果版本重复批准则返回已有回执。

真正发布是数据库和文件系统两个持久化系统，SQLite 事务无法同时包住文件 rename。实现采用“先记录 APPROVED，再写固定文件，最后标记 SAVED”的可恢复顺序。文件写入先在成果目录创建临时文件，`flush`、`fsync` 后原子发布；目标已存在且字节一致视为上次已成功，可继续补齐回执；目标内容不同则拒绝覆盖并标记 `RECONCILE_REQUIRED`。因此“后端保存成功但响应丢失”可安全重试，而磁盘上的未知文件不会被静默抹掉。

拒绝决定只写数据库，不创建文件。备份模块现在会核对并收集所有已批准研究报告；已批准却缺文件的源实例会被备份检查指出，恢复时报告与业务库一起还原。

## 7. 页面与 API

网页新增三个独立人格入口和共同聊天室。共同聊天室显示主研究员、评审选择、后台任务状态、报告版本、评审问题和确认按钮；内部 ID 默认收起，用户需要跨聊天室显式分享时才展开消息 ID 输入。刷新通过以下 API 恢复数据库事实：

- `POST /v1/tasks`：`mode=research` 时必须包含 `execution`，相同消息和相同执行配置幂等返回原 `task_id`。
- `GET /v1/chats/{chat_id}/tasks`：读取后台任务及终态，不重新运行。
- `GET /v1/chats/{chat_id}/artifacts`：列出该聊天室成果。
- `GET /v1/artifacts/{artifact_id}?version=N`：读取指定不可变版本、评审和回执。
- `POST /v1/artifacts/{artifact_id}/decision`：批准或拒绝精确版本。

研究任务由原有单 worker 队列认领。进程重启时，已是 `RUNNING` 的研究任务标记为 `INTERRUPTED`，不会自动重放模型和重复写版本；已完成任务、成果和回执正常读回。重复提交依赖冻结请求身份，不能把另一条普通聊天轮次误当成研究恢复回执。

## 8. ID、对象和生命周期

| ID | 标识对象 | 生命周期与用途 |
| --- | --- | --- |
| `owner` | 本机业务数据所有者 | 研究历史、任务和成果使用它过滤。个人记忆表依赖单用户业务库及记忆作用域 |
| `chat_id` | 一个研究房间或私聊 | 容纳多次任务；同房间历史可被后续任务选择 |
| `message_id` | 用户提交的一条网页消息 | 用于任务幂等和显式分享；不等于模型运行 |
| `task_id` | 一次完整研究闭环 | 从 QUEUED 到终态，包含生成、评审、修改和复核 |
| `agent_id` | MELCHIOR/BALTHASAR/CASPER 稳定身份 | 长期稳定；展示名和配置版本可变化 |
| `agent_run_id` | 某人格某阶段的一次运行 | 每个 generate/review/revise/verify 各一条，记录输入和用量 |
| `artifact_id` | 一份逻辑研究成果 | 可拥有多个不可变版本 |
| `version` | 成果正文快照编号 | 从1递增；评审、确认和后续复核都绑定精确版本 |
| `finding_id` | 某版本上的一条评审问题 | 由程序保存时分配；修改阶段必须逐项处理 |
| `tool_call_id` / `evidence_id` | 本次真实工具返回 | 报告引用身份；只在允许的来源集合内有效 |
| `request_id` | 一次用户确认操作 | 网络重试幂等键，不能换绑另一版本或决定 |

## 9. 模型、程序、用户和持久化各负责什么

| 层次 | 本轮职责 | 明确不代表什么 |
| --- | --- | --- |
| 模型生成 | 写报告、提出问题、逐项回应修改 | 不自动获得事实性或保存权限 |
| 程序工具与校验 | 实际读取资料、分配证据 ID、校验结构/引用/预算/版本 | 引用合法不等于语义真的支持结论 |
| 用户确认 | 选择是否批准当前精确版本 | 点击前数据库草稿不等于正式文件 |
| SQLite 写入 | 保存任务、运行、版本、评审和确认回执 | `APPROVED` 时文件可能仍待恢复 |
| 文件发布 | 创建固定 Markdown，完成后标记 `SAVED` | 不会执行报告里的行动，也不代表研究建议正确 |

## 10. A1 至 A13 验收对应

| 条目 | 本轮证据 | 结论边界 |
| --- | --- | --- |
| A1 三人格 | 参数化测试确认三人格均进入同一 `PersonaRunner` 并使用工具 | 可控模型，不证明人格内容差异 |
| A2 双入口 | 页面三个入口与共同聊天室均提交 research execution | 浏览器重点验共同聊天室 |
| A3 研究成果 | 报告结构、来源、未知项和版本落库 | 未做真实云端内容复核 |
| A4 用户确认 | 版本+摘要绑定，批准后才发布文件 | 单用户本机边界 |
| A5 独立评估 | 评审只接收冻结报告和来源，运行记录分离 | 提示隔离不等于模型绝不受先验影响 |
| A6 一次改进 | v1问题汇总，逐项 handling，生成v2 | 最多一次，不追求自动修到通过 |
| A7 最终复核 | 原评审复核v2，无论结果均停止 | 离线状态覆盖充分，真实内容未测 |
| A8 历史隔离 | 私聊标记不泄漏；显式分享需有效消息 ID | 当前 owner 内显式分享 |
| A9 共享预算 | 总预算、阶段预算、评审均分和总截止时间测试 | 没有长期耗时分布数据 |
| A10 手写边界 | 新研究路径直接使用 `ChatLoop`，不调用完整框架 Agent 执行器 | 旧入口仍保留 LangGraph |
| A11 PASS/退出 | 初次 PASS、复核失败、非法输出、异常、预算耗尽均有终态 | PASS 仍需人工判断内容 |
| A12 上下文 | 必带保留、可选裁剪、省略原因、超限前置失败 | 字节估算不是精确 tokenizer |
| A13 版本并发 | 请求幂等、版本冲突、精确评审绑定、发布中断恢复 | 未验证多进程高并发 |

## 11. 验证结果与失败过程

最终全量回归为 `491 passed, 3 warnings in 52.12s`，其中研究模块19项。受限沙箱内 pytest 会卡在 FastAPI `TestClient` 的 Windows 通信初始化，保留该中断后，经授权在沙箱外执行同一命令得到最终结果。本轮23个 Python 文件通过 Ruff check 和 format check，前端通过 `node --check`；全仓 Ruff 的11项早期基线问题没有夹带修复。

真实 Edge 配合可控模型完成共同聊天室、v1、两份初评、v2、两份复核、批准、文件发布和刷新恢复。数据库有1个任务、6个运行、2个版本、4份评审、1个回执，文件与 v2 正文逐字节相同；[刷新恢复截图](validation/045-browser-restored.png)已随验证记录保存。浏览器验收后发现 v1 状态仍为 `IN_PROGRESS`，交付代码已修正为 `UNRESOLVED` 并由回归覆盖；原浏览器数据库如实保留，不回写伪造。

真实千问六任务评测脚本已准备，但当前进程缺少 `DASHSCOPE_API_KEY`，脚本在模型调用前退出，未生成结果，也未启动本地模型。因此本轮不能声称三个人格产生了真实差异，不能声称协作改善内容质量。完整命令、失败尝试和证据入口见[验证记录](validation/045-attempts.md)、[回归摘要](validation/045-regression.txt)和[浏览器审计](validation/045-browser-audit.json)。

## 12. 当前限制

- 真实云端固定六任务和逐条内容人工复核尚未执行；这是当前最大的未验收项。
- 上下文容量使用 UTF-8 字节近似，不是模型精确 token 计数。
- 当前队列是单 worker、本机单用户设计；没有验证多进程竞争、持续负载或磁盘损坏。
- 联网引用能验证来源身份和实际读取范围，但程序不判断每个自然语言结论是否被来源充分支持。
- 页面提供最小可用研究界面，没有进行原作风格视觉重设计。
- 旧聊天、审议和工作单框架仍存在；本轮只保证新研究路径不依赖完整 Agent 框架。
- 发布恢复处理可预期中断和目标冲突，不覆盖任意文件系统损坏、恶意本机修改或跨设备同步冲突。

## 13. 面试知识关联

### 13.1 幂等、乐观并发与双写恢复

本轮业务问题是用户重复提交或批准时不能重复生成，旧页面也不能批准已经变化的正文。基础知识包括幂等键、乐观并发控制和数据库/文件系统双写一致性。项目证据是 `message_id` 冻结任务身份、`expected_version` 拒绝竞争修改、`request_id + version + payload_hash` 绑定确认，以及 APPROVED 到 SAVED 的可恢复发布顺序。

设计取舍是没有引入分布式事务：本机 SQLite 与文件系统无法共享事务，采用可重试状态机更简单，也更符合单用户 MVP。面试追问“数据库提交后文件写失败怎么办”时，应回答：保留 APPROVED 回执；重试先核对固定目标，内容相同补齐 SAVED，内容不同进入人工协调状态，不直接覆盖。已实现并用故障注入验证；多机对象存储方案尚未实现。

### 13.2 Agent 循环与确定性护栏

业务问题是模型需要自主决定是否读资料，但不能无限调用或自行宣称完成。对应知识是工具调用循环、结构化输出、预算和状态机。成熟组件负责模型 SDK、Pydantic 校验、FastAPI 和 SQLite；个人实现负责人格共用循环的业务适配、证据身份、共享预算、最多一次修改和终态。

面试追问“为什么不用提示词写最多一次”时，应说明提示词不是可靠控制面；真正的轮次上限在 `run_task` 的控制流中，第二轮后直接返回终态。已用可控模型覆盖所有退出分支；真实云端内容效果尚未验证。

### 13.3 上下文工程与数据隔离

业务问题是长历史既可能超容量，也可能把私聊资料泄漏给另一个任务。研究历史按 owner 和 chat 选择。跨聊天室只有用户显式分享、程序校验通过的已完成消息才加入。个人记忆通过单用户业务库和 scope 控制读取范围。可选历史按规则裁剪，必带内容不静默丢失。

面试追问“向量检索是否更好”时，应区分阶段：当前 MVP 用确定性近期候选便于审计，尚未为研究上下文接入语义召回；未来可以用检索生成候选，但 owner/chat/显式授权仍必须作为硬过滤，不能交给相似度决定。

### 13.4 不可变快照与评审身份

业务问题是评审意见必须明确针对哪份报告，否则 v1 的 PASS 可能错误传播给 v2。对应知识是不可变版本、外键身份和审计日志。项目用 `(artifact_id, version)` 标识快照，`research_reviews` 外键绑定精确版本，新版本创建时旧版状态变为 UNRESOLVED，复核重新写新版本的评审。

面试追问“为什么保留旧版本”时，应回答：它使评审、用户确认、故障恢复和争议追溯都有稳定对象；代价是存储增加，但个人文本报告成本很低。该机制已由版本冲突与后续评审测试验证，不应包装成大规模事件溯源系统。

## 14. 建议阅读顺序与复述问题

建议先按本总结第2节走一遍用户场景，再读 `src/magi/research/service.py` 的 `_context` 与 `run_task`，理解“选择材料”和“控制状态”是两件事；随后读 `runner.py` 和 `models.py`，观察模型生成与程序拒绝的边界；最后读 `personal/artifacts.py` 和 `web/tasks.py`，理解草稿、确认、文件发布和重启恢复。

阅读后请尝试回答：

1. 为什么评审说 PASS 仍不能等同于报告事实正确？程序实际检查了什么、没检查什么？
2. `task_id`、`agent_run_id`、`artifact_id + version` 和 `request_id` 各标识什么，能否互换？
3. 用户看到 v2 并批准时，为什么后端还要同时核对 version 与 payload hash？
4. 如果数据库已经 APPROVED，但响应或文件发布中断，重试怎样避免重复写入和覆盖未知文件？
5. 哪些上下文可以因容量不足被省略，哪些必须保留？另一个私聊如何才能被显式分享？
6. 为什么修改最多一次必须由程序状态机控制，而不能只依赖提示词？

本轮到此停止。请先阅读并复述上述边界；若复述把“模型输出”“结构校验”“评审通过”“用户确认”和“实际文件 SAVED”混为一件事，需要先纠正理解，再决定下一轮。
