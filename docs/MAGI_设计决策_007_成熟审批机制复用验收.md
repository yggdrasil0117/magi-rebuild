# 设计决策007：成熟审批机制复用验收

日期：2026-10-31。第22轮为完整兼容性验收，不将试验入口接到生产网页。

按用户最新方向，优先复用成熟组件，在其上实现MAGI业务适配与验证，不为学习重复实现通用基础设施。当前优先验证现有Deep Agents 0.7.32的`interrupt_on`、LangGraph中断与SQLite checkpoint，评估替代未来“暂停等待批准”通用机制的适合程度。

范围：独立合成计划/工作单、脚本模型、真实Deep Agents图与持久checkpoint。用现有TaskDocuments.confirm作为实际工具执行，不复制文件写入、内容摘要与冲突处理。验证未批准不执行、拒绝不执行、批准后执行、不同进程恢复、摘要/owner/过时审批拒绝、已完成审批不重放。模型只负责提出工具请求；审批由试验宿主显式提供，不能从模型文字生成批准。

现有Harness的open_session增加可选interrupt_on透传，默认None；已有Web/CLI仍不启用，HarnessSession.ask的未完成保护保持不变。试验使用独立适配器直接驱动图的暂停/恢复，不把未完成的中断误报为完整回答。

审批请求绑定程序生成的thread_id、工作单document_id、不可变payload_hash和当前interrupt_id。owner由宿主传入并在业务读取前核对，恢复前核对实际待审动作。只允许approve/reject，不开放编辑工具参数。框架承担暂停、checkpoint与恢复；MAGI仍承担身份、确认对象及文件幂等。checkpoint不能替代业务保存回执，也不能保证跨数据库/文件系统恰好执行一次。

当前不增加真实模型调用、不升级依赖、不迁移用户数据，不把验收通过表述成生产网页已接入框架审批。根据证据留下明确接入结论与缺口；完整业务回归通过后交付，等待用户阅读。

资料：Deep Agents[人工审批](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)、LangGraph[中断与恢复](https://docs.langchain.com/oss/python/langgraph/interrupts)。以本机锁定版本的实际API和测试为准。
