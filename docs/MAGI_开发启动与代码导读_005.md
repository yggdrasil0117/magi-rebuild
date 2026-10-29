# MAGI 开发启动与代码导读 005

日期：2026-10-29。阶段：整体 MVP 开发准备完成；Web、三人格及聊天确认尚待实现。

## 1. 本次实际验证

在仓库根目录运行：

```powershell
.\.venv-harness\Scripts\python.exe --version
.\.venv-harness\Scripts\python.exe -m pytest -q
```

结果：Python 3.12.3；185 passed in 9.46s。此结果是本轮重新运行所得，测试覆盖范围沿用现有测试集，不代表 Web 或三人格已通过。

读取本机 Ollama `/api/tags` 成功，返回 `qwen3.5:4b`、`qwen3.5:9b`。默认配置仍为 4B；本轮没有调用模型生成，服务可达与模型列表存在不等于推理质量已验证。最初受沙箱网络限制的探测未成功，随后获准进行只读探测并成功。

当前已安装 Deep Agents 0.7.32、LangGraph 1.2.18、langchain-ollama 1.1.0、httpx 0.28.1。FastAPI 和 Uvicorn 未安装；Open WebUI 未部署。实际实施时增加经过兼容性验证的 Web 可选依赖，界面环境独立安装。

工作区存在大量已有暂存、未暂存和未跟踪文件。执行时保留，不能用清空工作区或整体覆盖来建立基线。本轮只添加准备记录，不修改业务代码。

## 2. 先理解现在的一条消息怎样运行

```text
harness/cli.py
  → load_config：读取模型、目录、存储与预算
  → ApplicationLock：避免多个管理进程同时操作
  → open_personal_session
      → ContextService：选择当前有效记忆与 epoch 对应的 thread
      → open_session：建立框架 Agent 和 SQLite checkpoint
  → HarnessSession.ask
      → 新增本轮 HumanMessage
      → Agent 调用模型，必要时调用允许的只读工具
      → 检查输出与调用预算
      → 返回回答；保存 checkpoint 和执行事件
```

框架负责执行循环和 checkpoint。MAGI 负责开放哪些工具、注入什么有效记忆、怎样判定失败，以及哪些操作属于真实业务写入。旧 `agent/runner.py` 是手写循环学习基线，当前产品入口在 `harness`，两者不要混为一条运行路径。

Web 化后，HTTP 每次新请求都要重新解析有效上下文。不能把 CLI 长会话对象永久缓存给网页会话，否则修改记忆后仍可能使用旧 system prompt。

## 3. 记忆与计划的调用链

```text
显式记忆修改 → MemoryService → 事务提交 + context_epoch 变化
下一次聊天 → ContextService → 当前记忆 + 新物理 thread

显式保存计划 → PlanService.create
              → plans + plan_revisions + operations 同一事务
              → Receipt（真实 ID、版本、提交状态）
计划修订 → PlanService.update(expected_version, source_epoch, request_id)
         → 检查旧版本/上下文 → 新 revision + 操作回执
```

`request_id` 用于识别同一次提交；重复相同请求返回原回执，不新增计划。`expected_version` 防止旧客户端覆盖已更新内容。`source_epoch` 防止把过时个人背景生成的候选当成当前有效计划。三者解决不同问题。

模型生成“已保存”不构成保存证据。当前只有显式 CLI 可写计划，聊天中的确认入口是下一阶段需要接上的产品能力。

## 4. 第一阶段实施及整体顺序

实施基准：[独立产品与学习主线](MAGI_设计决策_002_独立产品与学习主线.md)、[MVP 整体计划](MAGI_MVP整体设计与七天改造计划_v0.3.md)。不再讨论新定位或迁移框架。

1. 增加单用户 MAGI HTTP 服务，复用现有个人聊天入口；实现健康检查、身份边界、会话标识、重复请求与错误处理。单 worker，生命周期持进程锁，连接在执行线程内创建使用。
2. 接通独立界面：优先验证 Open WebUI Pipe 的真实 ID、任务分流与正文保存。得到可用网页聊天后集中解释调用链。
3. 实现三人格有界工作流，核对快照隔离、预算、完整及部分失败的展示。
4. 接入候选确认、真实计划保存、重启查询、反馈修订与新旧版本对照。
5. 真实模型和界面演示，生成模块 005 整合总结及 MVP 验收记录。

持续推进整个闭环；阶段拆分用于理解与验证，不要求用户逐行确认或完成练习后才能继续。新增能力只在真实验证后标记完成。Yggdrasil 集成和新的模型服务不阻塞独立 MVP。

## 5. 代码阅读与可选练习

阅读顺序：`harness/cli.py` → `harness/config.py` → `personal/context.py` → `harness/runtime.py` 的 `open_personal_session`、`HarnessSession.ask` → `personal/plans.py` → 对应测试。

先尝试解释：同一个用户会话为什么可能对应多个内部 thread？计划写入后响应丢失，重试为什么不会创建两份？不必先读完框架源码。

执行阶段每个完整功能需附核心参数、正常流程、一个失败流程和可运行命令。练习在临时数据上进行，不清空现有 `.magi` 数据。

## 6. 切换执行模型后的启动指令

> 阅读 README、设计决策 002、v0.3 整体计划和开发启动与代码导读 005。开发基线为 185 项离线测试通过，本地 Ollama 列表可达，Web 依赖未安装。保留现有工作区改动，从独立 MAGI API 与网页聊天开始，连续实现三人格、确认计划、重启查询和反馈修订。复用现有记忆、计划和 harness；按完整阶段解释，不逐行停顿。遵循计划中的上下文、幂等、版本和权限边界，使用临时库验证。最终提供运行方法、真实演示记录、设计对照总结及明确未完成项。不要把第一阶段完成当作整个 MVP 完成。
