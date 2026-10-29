# 模块总结 004B：计划与操作任务

日期：2026-10-29。设计基准：产品与技术设计 v0.2、模块 004 开发任务书。

状态：004B 的显式计划保存、版本更新、幂等操作与模型只读查询已实现并通过离线验证。自然语言候选→用户确认→写入的聊天内整合尚未实现。

## 实际改动

- `models.py` 新增 `PlanView` 和 `PlanSummary`。
- 业务 schema 迁移到版本 2，新增 `plans` 和不可变 `plan_revisions`。
- `plans.py` 新增计划创建、当前/历史版本查询、乐观版本更新、request_id 幂等和 epoch 复核规则。
- `personal/cli.py` 新增 `plan create/list/show/update`，支持直接参数和绑定 SHA-256 的 JSON 快照导入。
- `harness/runtime.py` 新增 `magi_list_plans` 和 `magi_get_plan` 只读工具。没有向模型暴露计划写入工具。
- `tests/test_personal_plans.py` 及新增 CLI/harness 用例覆盖该单元。

## 设计与失败边界

`plans` 只保存当前版本指针；每次创建或更新都写入新的 `plan_revisions`，旧正文不被覆盖。更新 SQL 同时约束计划 ID 和 `expected_version`，影响行数不是 1 就拒绝。

计划变更、revision 和 Operation 成功记录在同一业务事务中提交。同 request_id/同参数返回原 Receipt，不新增版本；同键/不同参数拒绝。已提交请求即使其来源 epoch 后来变旧，重试仍返回原事实。

CLI 提交的来源只标记为 `user_declared`，不冒充程序已读取和核对。记忆 epoch 改变后，旧 epoch 计划返回 `needs_review=true`。CLI 可查看旧正文；模型工具只返回元数据及 `body_withheld=true`，防止撤销偏好经计划正文回流。

## 验证

2026-10-29，Windows Python 3.12.3，`.venv-harness`：

- 全仓 `pytest -q`：185 passed in 7.82s。
- 计划单元测试覆盖创建/重启/更新/旧版本、幂等冲突、过时版本、过时 epoch、事务回滚与输入边界。
- 子进程 CLI 测试覆盖 SHA-256 绑定导入、版本更新和旧版本查询。
- harness 替身模型实际调用计划查询工具；当前 epoch 可读正文，旧 epoch 正文被扣留。

本轮未调用真实 Ollama。离线证据证明确定性边界，不证明真实模型总能选对查询工具或正确解释计划。

## 学习与下一步

主路径是：`plan create` → `PlanService.create` → `plans + plan_revisions + operations` 同一事务 → Receipt；更新时由 `expected_version` 防止旧读取覆盖新结果。

小练习：为 `plan list` 增加标题关键词过滤，并验证它不修改计划版本、Operation 数量或记忆 epoch。

下一步应先运行固定样例和完成小练习，再开始模块 005 三人格审议。
