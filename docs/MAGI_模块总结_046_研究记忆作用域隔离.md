# MAGI 模块046：研究记忆作用域隔离

2026-11-10

假设你在 MAGI 保存了一个公共偏好“技术解释先举例”，又在个人会话里保存了一条只供该会话使用的备考安排。你打开共同聊天室，请 CASPER 调查技术方案时，公共偏好可以帮助它组织报告。备考安排应留在原会话。独立人格入口也遵守同一规则。v0.4 规定研究只使用已确认公共记忆，并将同会话研究历史和显式共享消息作为另外两类材料。

045 中，研究历史已经按会话过滤，个人记忆却只检查了 `ACTIVE`。一条专属记忆同时可以是有效记忆，因此这个检查会把它当成公共材料交给研究员。普通聊天走自己的上下文服务，查询会匹配全局和当前会话范围。此前的隔离测试把标记放进研究历史，没有把它保存为一条专属记忆，因而未覆盖这条读取路径。

## 任务从哪里拿到记忆

网页提交研究任务后，`AgentTasks.run_one` 认领任务并调用 `ResearchService.run_task`。后者在开始生成前调用 `_context(task, turn, execution)`。其中 `task` 是已登记的任务，`turn` 提供聊天身份、用户要求和联网选项，`execution` 提供负责人及显式分享清单。函数返回供后续研究阶段使用的上下文字典，`confirmed_memories` 是其中的公共记忆列表，`selection` 记录候选数与省略数。

关键改动在 [ResearchService._context](../src/magi/research/service.py)。以下摘录省略了连接和后续历史查询。

```python
memory_rows = db.execute(
    """SELECT id,version,key,value,COUNT(*) OVER () AS eligible_total
       FROM memories WHERE status='ACTIVE' AND scope='global'
       ORDER BY updated_at DESC,id ASC LIMIT 20"""
).fetchall()
memory_total = memory_rows[0]["eligible_total"] if memory_rows else 0
```

SQLite 先筛出仍然有效的公共记录。窗口计数 `COUNT(*) OVER ()` 计算筛选后、限制条数前的总量。然后按更新时间从新到旧排列，时间相同用 ID 固定顺序，最多返回 20 条。比如有 21 条符合条件的记录，候选数就是 21，取回 20 条。只属于某个会话的记录既不会占这 20 个名额，也不会进入候选统计。用同一条 SQL 计算总量和结果，让两个数字来自同一次读取。

取回的记录只保留 `id`、`version`、`key` 和 `value` 交给上下文装配。记忆 ID 对应一个事实槽位，版本号对应这个事实的一次变更。任务 ID 对应一次研究，聊天 ID 标识历史归属，二者都不改变记忆的作用域。程序仍可能为容量预算省略较早的公共记忆。如果本次要求、权限和显式分享材料已经超限，现有逻辑返回 `CONTEXT_LIMIT`，模型调用不会开始。

在例子里，如果库中只有一条有效公共偏好、同会话和其他会话各一条有效专属记忆，以及一条已删除的公共记忆，研究员只收到公共偏好，候选数为 1，容量足够时省略数为 0。显式分享按消息 ID 验证和读取对应对话，不改变专属记忆的范围。

## 保存与恢复边界

本轮过滤发生在模型生成前。记忆的创建、修改和删除依旧经原有 `MemoryService` 写入，自动提取的候选依旧要用户确认才会成为有效记忆。研究读取不会复制、修改或删除任何记忆行，旧数据库可以直接使用。报告草稿和批准后的 Markdown 仍由原研究流程保存。任务重试和中断恢复沿用原队列规则，本轮没有新增写入步骤。已经生成并保存在历史中的旧报告不会被改写。

## 验证

先把同会话、其他会话、有效公共和已删除公共记忆写入独立测试库，再通过真实后台任务入口运行研究。可控 runner 捕获实际交给研究员的上下文。修复前，独立人格和共同聊天室两个参数案例均失败，研究员收到两条专属记忆。命令如下。

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 -m pytest -q tests/test_research_tasks.py -k only_injects_active_global_memories
```

修复后，两条回归均通过，验证了公共记忆仍可用、专属和已删除记忆不进入输入、候选统计准确且专属记忆仍保存在库中。测试位置见 [test_research_only_injects_active_global_memories](../tests/test_research_tasks.py)。

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 -m pytest -q
.\.venv-harness\Scripts\python.exe -m ruff check src/magi/research/service.py tests/test_research_tasks.py
.\.venv-harness\Scripts\python.exe -m ruff format --check src/magi/research/service.py tests/test_research_tasks.py
git diff --check
```

全量回归 493 项通过，耗时 54.90 秒，有 3 条依赖弃用警告。两个 Python 文件通过 Ruff，Git 空白检查通过。本轮未调用真实模型，未重验浏览器或服务进程重启。该用例验证读取权限，语义召回排序和模型如何使用偏好仍需各自评测。有效期、自动遗忘及 A/B 评测仍未实现。

## 面试知识关联

这个问题对应数据作用域与检索前过滤。SQLite 负责执行过滤、计数与排序，MAGI 决定研究的可见范围并在测试中检查模型输入边界。常见追问是为何先做过滤再排序。以本项目为例，先取最近 20 条再删除私有项，会让私有记录占掉公共记录的名额，也容易误报省略数量。把规则放在查询中，排序只处理业务允许的候选。当前验证范围是单用户业务库内的作用域隔离。若未来同库支持多个用户，还需要为记忆建立用户归属，并在查询和索引处应用同样的过滤。

建议先读回归用例的四类记忆，再看 `_context` 如何得到候选数，最后对照普通聊天的 `ContextService.build_current_context`。有空可复述两个问题。为什么 `ACTIVE` 不能单独决定一条记忆是否能用？为什么在 `LIMIT 20` 前过滤作用域？
