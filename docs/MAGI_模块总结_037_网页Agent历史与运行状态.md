# 037 网页 Agent 历史与运行状态

上一轮接管了网页聊天的模型和工具循环，但一轮结束时还要分别写 LangGraph checkpoint 和网页业务库。用户让 MAGI 读资料，模型答完以后，如果进程在两次保存之间退出，下一轮可能已经读到回答，页面却显示上一轮中断。这轮把完整消息和轮次状态放入同一个 SQLite 事务。网页 Agent 自己决定何时执行、何时重试、哪些消息能进入历史，以及什么条件下可以宣布完成。

## 从一次读文件看保存过程

用户问“读取 note.txt，然后告诉我里面的识别码”。`WebService.turn()` 先查询相同消息是否处理过，为新请求登记 `RUNNING`，然后打开 `WebChatSession`。它按当前背景版本读取历史，把本轮允许的工具交给 `ChatLoop.ask(text)`。模型请求读文件时，循环先校验工具名称、参数和预算，再执行工具。模型收到与原请求 ID 对应的工具结果后，给出最终回答。

循环返回回答和一整批消息。下面摘自 `src/magi/harness/chat_loop.py` 的 `ChatLoop.ask()`：

```python
pending.append(reply)
if not calls:
    if not reply.text.strip():
        raise ValueError("本轮没有生成完整文本回答")
    events.emit("run_completed", scope="execution_only")
    return reply.text, pending
```

`pending` 在这个例子里依次包含用户问题、模型发出的工具请求、工具结果和最终回答。返回时，日志里的 `run_completed` 只表示模型和工具阶段结束。网页完成状态由服务提交事务以后确定。

`src/magi/web/service.py` 的 `WebService.turn()` 在同一个 `with self.store.transaction() as db:` 内执行以下关键步骤，片段之间省略候选写入及 SQL 参数：

```python
if exchange is not None:
    epoch = db.execute(
        "SELECT context_epoch FROM app_state WHERE singleton=1"
    ).fetchone()[0]
    if epoch != binding.epoch or epoch != native_history.binding.epoch:
        raise WebError("CONTEXT_CHANGED", "背景已变化，请新发消息")
    native_history.append(db, turn_id, exchange)
```

随后更新对应的 `web_turns` 记录。SQL 只匹配仍为 `RUNNING` 的轮次，影响行数必须为一，否则抛出 `TURN_NOT_RUNNING`，事务一起回滚。这样背景更新后，旧背景下生成的消息无法混入新的对话历史。三人格审议完成时，综合结论和候选也在同一事务内写入。此时保存的是候选，用户后续确认才会保存正式计划。

`WebChatHistory.append(db, turn_id, messages)` 接受调用方已经开启的数据库连接，按消息协议序列化整轮内容，写入 `web_chat_history`。方法自身不提交事务。SQLite 负责让一批写入同时生效，MAGI 决定哪些写入必须放在一起、何时拒绝。本轮将业务库结构版本升级到 10。

## 失败和重启时会留下什么

如果写入消息以后，更新轮次状态时发生异常，SQLite 会回滚刚写入的消息。网页异常处理把仍在运行的轮次标为 `FAILED`。如果进程直接中断，重启会把遗留的 `RUNNING` 改为 `INTERRUPTED`。两种情况下，重新提交同一消息均会取回原轮次。程序不会自动恢复到某条工具指令继续执行。

如果提交已成功，但响应丢失，原轮次为 `COMPLETED`，整轮消息也已经存在。再次提交同一消息只取回原结果。这里的幂等依赖网页请求去重和数据库记录，模型本身并不负责识别重试。

如果用户在同一轮要求准备工作单，预览工具可以先在自己的业务事务里提交一份 `PREVIEW`。后续模型超时，聊天历史不会写入半轮工具交互，但网页仍能通过原轮次找到预览。最终文件由用户在页面确认后创建。聊天事务保证的是模型历史与轮次状态一起提交，独立完成的工具业务写入仍由工具自己的回执和确认规则管理。

| 标识 | 标识的对象及用途 |
| --- | --- |
| `message_id` | 客户端消息。在同一用户和聊天内去重，重复提交时还要核对输入摘要 |
| `turn_id` | 后端网页轮次。把页面状态、消息批次、预览来源与执行日志关联起来 |
| `thread_id` | 当前逻辑会话在某一背景版本下的历史位置。背景变化后绑定到另一条历史 |
| `epoch` | 背景版本。开始时读取，提交时复核 |
| `call_id` | 模型发出的一项工具请求。工具结果携带同一个 ID 回给模型 |

## 旧记录如何接上

`src/magi/web/chat_history.py` 的 `WebChatHistory.load()` 在首次访问某个 `thread_id` 时检查旧库。只有该会话确实存在旧记录才构造兼容图。旧适配器读取并重建历史，过程使用空的测试模型适配器，无需云端凭据，也不执行模型或工具。直接读取最新 checkpoint 的消息字段不足以恢复历史，因为当前 Deep Agents 会把部分消息作为增量写在旧 checkpoint 之间。

导入按用户消息划分轮次。每组都要有完整最终回答，中途出现的每组工具请求和结果必须对应，未完成或不符合协议的组留在旧库。历史导入与导入标记同事务提交，重开服务时不会重复导入。旧系统可能留下完整模型回答但网页轮次状态不同步的记录，旧消息没有统一的网页轮次 ID，导入按消息完整性判断，无法可靠地把每一条旧问答与网页轮次逐一核对。

切换后，网页从业务库读取已导入的历史和新增消息。旧审议如果已在业务库完成但还没续接，恢复时按当前用户查询，原背景仍有效才补写，补写与 `synced` 一起提交。原终端模式和独立工作单审批仍有自己的框架入口。网页循环复用 LangChain 的模型、消息和工具协议，使用 Pydantic 校验与 SQLite 事务，执行顺序、预算、重试和运行状态由 MAGI 定义。

## 验证记录

新增用例走网页默认服务入口，使用真实 SQLite、真实文件工具和脚本模型：

- 在插入消息后，用 SQLite 触发器故意阻止 `COMPLETED` 更新。重开服务后确认轮次失败、消息表为空，重试没有额外模型调用。
- 模拟提交后取回响应失败。重新打开服务后，重复提交得到原答案和同一份消息记录。
- 用旧运行时生成一轮包含工具结果的真实 checkpoint，再生成一轮未完成的历史。新入口只接入完整轮次，审议结果可以继续进入聊天。导入后将创建旧图的函数替换成必失败函数，网页仍能从业务库继续聊天。

迁移试验最初直接从最新 checkpoint 解码消息，遗漏了旧框架的增量历史。改用旧适配器重建后，迁移用例触发了原有的上下文容量守卫。将该试验的容量改为 32768 字节后，完整历史进入模型。容量守卫本身保留。

```powershell
.venv-harness/Scripts/python.exe -m pytest -q tests/test_web_chat_loop.py tests/test_council_checkpoint.py tests/test_harness_runtime.py tests/test_qianwen_provider.py tests/test_personal_memory.py
```

上述组合 60 项通过。证据入口为 `tests/test_web_chat_loop.py`。重新构造服务验证了落盘后的读取和重放，提交边界通过故障注入验证。此次未做真实云端、浏览器手工验收或强制杀进程试验。

另执行 `.venv-harness/Scripts/python.exe -m pytest -q tests/test_web_mvp.py -k 'full_create or busy_and_restart or sync_failure or epoch_change or interrupted_run or same_id_changed'`，6 项通过。改动文件通过 Ruff 检查，`git diff --check` 通过。未取得全量回归通过结果。

## 面试知识关联

**事务边界。** 用户看见“已完成”时，模型历史也应能读到回答。项目用一个事务写两张表，并在更新前检查背景版本。SQLite 提供原子提交，项目负责选择提交边界。若被问为什么把工具也包在事务里不行，可以用模型超时的例子说明。工具可能访问外部网络或提交独立业务，长事务会占用锁，SQLite 的回滚也无法撤销外部结果。

**迁移和幂等。** 旧历史需要能读，新历史需要有唯一的写入路径。项目以会话绑定为迁移单位，在同一个事务登记导入标记与消息。用例验证真实旧 checkpoint 的增量历史和重复访问。若被问能否回退到旧版本继续使用新增历史，当前实现只有向前导入，没有反向同步。旧终端与新网页也不共用同一套新增消息。

建议先读 `ChatLoop.ask()` 返回完整消息的位置，再看 `WebService.turn()` 的最终事务，最后对照 `WebChatHistory.load()` 和三个故障用例。可以用下面的问题复述：

1. 模型答完以后，什么时点网页可以显示已完成？
2. 完成状态更新失败和提交后响应丢失，数据库分别留下什么？
3. 旧历史为什么不能直接读最新 checkpoint 的单个消息字段？
4. 工作单预览为什么可能在失败的聊天轮次里仍然可见？
