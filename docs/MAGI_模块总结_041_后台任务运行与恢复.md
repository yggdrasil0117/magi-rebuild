# 模块041 后台任务运行与进程恢复

## 先把任务交给服务，再等结果

你想让 MAGI 在准备面试时查资料，或者让三人格评估一份计划。遇到模型、联网工具和长会话摘要一起执行时，浏览器可能已经关掉，服务还在处理。模块040能恢复已登记的聊天轮次，本轮把登记提前到生成之前。勾选「提交为后台任务」再发送，页面拿到的是已保存的任务状态。服务中的执行器会随后领取任务。重连原会话时，展开「后台任务」可以看到进度，结束后页面自动加载聊天结果。

日常聊天和三人格都能提交后台任务。同一会话一次只允许一个待执行或执行中的任务，避免两个任务同时基于同一条旧回复。网页同步发送和终端原有接口仍可使用。后台完成后当前页面不自动触发记忆建议。计划候选仍由用户确认才保存。

## 提交和领取是两次独立写入

`POST /v1/tasks` 接收模式和原有聊天请求。接口返回 HTTP 202，表示任务已登记。重复提交相同会话、消息 ID 和内容，会返回原任务。相同 ID 的模式或输入变化则拒绝。队列最多容纳当前用户 32 条等待任务。提交时冻结会话上一个回复 ID、用户问题、工具开关和当前背景版本，执行时沿用原来的分支检查，并核对背景版本有没有在排队时变化。

任务表是 SQLite 第 11 版迁移新增的 `agent_tasks`。提交事务保存请求和 QUEUED，提交之后才通知执行器。即使通知来不及发，执行器定期查询仍能看到待领取记录。读取任务状态只返回认证用户的元数据。回复、计划候选或工具产物仍通过原轮次读取。

执行器在应用锁保护下启动一个工作线程。下面是 `src/magi/web/tasks.py` 中 `AgentTasks.claim` 的关键代码，省略了前置的空队列检查。

```python
with self.store.transaction() as db:
    task = db.execute(
        """SELECT * FROM agent_tasks WHERE owner=? AND status='QUEUED'
           ORDER BY created_at,id LIMIT 1""",
        (self.owner,),
    ).fetchone()
    if task is not None:
        db.execute(
            "UPDATE agent_tasks SET status='RUNNING',updated_at=? WHERE id=?",
            (now(), task["id"]),
        )
return task
```

`task` 是查询到的冻结请求及身份。查找和更新都在短事务内完成，退出事务才把任务交给 `run_one`。模型和工具在事务外执行，因此一次慢网络调用不会一直持有 SQLite 写锁。模型运行仍通过 `WebService.turn`，包括工具白名单、预算、计划确认以及消息与轮次状态的原子提交。应用正常关闭时等待工作线程退出，再释放进程锁。

如果全局执行门正被另一个会话占用，而当前请求还没建立轮次，执行器会把 BUSY 任务放回队列再检查。这一步还没有开始生成。背景或父回复过期时会保存失败原因，需要用户核对后重新提交。执行已开始后的异常不会自动重跑。

## 中断后看哪份记录

服务启动先恢复网页轮次，再运行 `AgentTasks.recover`。恢复函数只处理之前已领取的任务。它通过 `matching_turn` 用用户、会话、消息 ID 和请求摘要一起找到网页回执。不能只用消息 ID，因为同步入口可能留下 ID 相同但内容不同的记录。

```python
turn = self.matching_turn(db, task)
status = turn["status"] if turn else None
if status not in {"COMPLETED", "FAILED", "INTERRUPTED"}:
    status = "FAILED" if turn and status != "RUNNING" else "INTERRUPTED"
self.finish(
    db,
    task,
    status,
    turn["id"] if turn else None,
    "WORKER_INTERRUPTED" if status == "INTERRUPTED" else None,
)
```

假设你依次提交了三项任务。第一项模型已经开始，但服务被强制结束，无法判断远端是否已收到请求，已有的工具业务写入也可能独立保存。恢复后第一项标记为 INTERRUPTED，用户应先核对已有结果。第二项网页回复已提交，只差更新任务状态。恢复时直接关联已完成轮次，状态变成 COMPLETED。第三项仍为 QUEUED，证明还未被执行器领取，重开后继续执行。

如果服务刚领取就退出，甚至还没建立网页轮次，也归为中断。这样可能需要用户重新提交一次，但不会在缺少动作回执时盲目重放。当前只有任务级恢复，工具指令中间位置没有可重放的检查点。

| 标识 | 用途 |
| --- | --- |
| 消息 ID | 浏览器发送前生成，同一请求重试保留 |
| 任务 ID | 入队时生成，排队期间已经可查询 |
| 网页轮次 ID | 聊天入口实际建立轮次时生成，保存结果和工具产物 |
| 背景版本 | 排队时冻结，启动执行时检查是否仍适用 |

刷新后页面按会话读取任务列表。活跃任务每约 1.5 秒查询一次，查询结果驱动状态展示。任务终结后重新加载原聊天历史。若入队 POST 回执丢失，页面按消息 ID 在任务列表里定位，读不到时仍保留原提交以供重试。

## 验证

完整回归 463 项通过，3 条已有依赖弃用警告。新增 API 用例验证 202 可以在模型完成前返回，查询可以读取 RUNNING，重复提交沿用原任务 ID，冲突请求被拒绝。另测了背景变化后不调用模型，以及恢复时不关联同 ID 的不同内容。JavaScript 语法、Ruff 和 Git 差异检查通过。[完整回归](validation/041-regression.txt)

```powershell
.\.venv-harness\Scripts\python.exe -m pytest -q --basetemp .magi/test-041
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluate_agent_task_recovery.py --data .magi/acceptance-041 --output docs/validation/041-process-recovery.json
```

第二条命令在隔离数据目录创建三条任务，实际启动并强制结束两个 Python 子进程。第一个停在脚本模型调用中，第二个停在网页轮次提交之后、任务状态更新之前。恢复后得到 INTERRUPTED、COMPLETED、QUEUED，最后排队任务进入 COMPLETED。三条调用日志各出现一次。模型及外部动作使用脚本替身，操作系统进程退出和 SQLite 状态为真实试验。[进程试验](validation/041-process-recovery.json)

浏览器试验使用 `npx --yes --package @playwright/cli playwright-cli -s=magi041 open http://127.0.0.1:8896 --browser msedge`。隔离服务的脚本模型等待 9 秒，浏览器拦截并丢弃服务已经发出的 202 回执。页面按消息 ID 读到已登记任务，处理中刷新后继续观察，最后出现已完成状态和原回答。试验只发了一次任务 POST，SQLite 最终只有一个任务和一条对应轮次。[浏览器记录](validation/041-browser.json) · [完成截图](validation/041-completed.png)

本轮没有调用真实云端模型，也未做多 worker、跨机器故障转移、负载或长时间运行验证。实现沿用本机单进程应用锁，任务队列与业务库一起备份。执行中断后的动作核对依赖原工具的回执，外部写入需要单独设计。

## 面试关联和复述

- **提交与执行解耦。** 202 为什么可以在模型结束前返回？请求已持久登记，执行器独立领取。SQLite 负责提交原子性，MAGI 规定什么时候才算已接收。
- **领取与副作用。** 为什么领取事务不能一直开到工具执行结束？慢调用会占用写锁，数据库回滚也撤销不了外部动作。项目用短事务写领取状态，再在事务外执行。
- **恢复与幂等。** 执行器先查可核实的轮次回执，明确区分未领取、已有完成结果和结果不明三个状态。可追问多实例租约和外部幂等键，这些属于扩展方案。

建议先读三个进程场景，再对照 `claim` 和 `recover`。复述题是：**任务已经是 RUNNING，但网页回复已提交。进程退出后，为什么可以把任务恢复成 COMPLETED？如果只有 RUNNING 且找不到匹配回执，又该怎么办？**
