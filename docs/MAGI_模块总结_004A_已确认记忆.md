# 模块总结 004A：已确认记忆

日期：2026-10-29。设计基准：产品与技术设计 v0.2、模块 004 开发任务书。

状态：004A 的显式已确认记忆单元已实现并通过离线验证。真实 Ollama 体验、聊天内候选确认及 004B 计划与任务尚未完成。

## 实际交付

- `src/magi/personal/models.py`：记忆、Receipt、会话绑定及稳定错误码。
- `store.py`：独立 SQLite 业务库、`schema_version=1`、外键、有界锁等待与 `BEGIN IMMEDIATE` 事务。
- `memory.py`：新增、查询、更正、删除，request_id 幂等、expected_version 版本检查、tombstone 和 epoch 原子递增。
- `context.py`：逻辑会话到 epoch 物理 thread 的映射，global/会话作用域选择。
- `locking.py`：聊天与管理入口共用的单活跃进程锁。
- `personal/cli.py`：`memory add/list/show/update/delete` 和 `operation show` 显式入口。
- harness 接入：每轮从业务库重读 ACTIVE 记忆；epoch 变化时使用干净 thread；历史分段查看不请求模型。

业务库 `.magi/personal.sqlite3` 与 LangGraph checkpoint 独立，迁移不读写框架内部表。删除保留 ID、版本及时间，清空当前业务正文；不承诺擦除旧 checkpoint 或备份。

## 关键流程与失败边界

CLI 参数先由 service 校验。业务变更、MemoryChange、Operation 成功事实和 `context_epoch + 1` 在同一事务中提交。提交前异常全部回滚；同 request_id 同参数返回原 Receipt，不再增加版本或 epoch；同键不同参数返回 `IDEMPOTENCY_CONFLICT`。

开始模型请求前，ContextService 读取当前 epoch 及 ACTIVE 记忆。记忆变化后，同一逻辑会话获得新物理 thread，不复制旧消息、工具结果或摘要。会话作用域的同 key 记忆优先于 global。记忆块仍进入原 harness 的容量检查，超限拒绝而不静默截断。

当前限定单活跃应用进程。用户需退出交互聊天后再管理记忆。SQLite 事务仍是业务原子性的依据，进程锁不代替事务和版本检查。

## 验证证据

2026-10-29，Windows Python 3.12.3，`.venv-harness`：

- 全仓 `pytest -q`：173 passed in 6.86s。
- Ruff check 和 format check：`src/magi/personal`、`src/magi/harness` 及相关测试通过。
- 跨进程 CLI 测试验证了写入后重启查询及 Operation Receipt。
- 框架集成替身捕获了实际模型输入：新 epoch 不含旧历史唯一标记，删除后不含已撤销记忆唯一标记。

这些测试证明程序的存储、事务与上下文边界，不证明真实模型必然按偏好回答。本轮未调用真实 Ollama，因此 PA-03 的程序边界已验收，真实体验保留为未完成项。

## 学习点与下一步

阅读顺序：`personal/cli.py` → `memory.py` → `store.py` 事务 → `context.py` → `harness/runtime.py` 的 `open_personal_session`。重点区分 checkpoint 历史、已确认业务记忆、本轮实际模型输入。

小练习：使用 `memory list --scope session:<ID>` 与 `--scope global` 分别查询，解释为什么作用域过滤不会改变版本或 epoch。

下一步是用无隐私固定样例完成一次真实模型新增—更正—删除体验，并由用户完成上述小练习。在此之前不开始 004B。
