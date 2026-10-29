# 模块 005：Web MVP 整合

日期：2026-10-29。实现基准：开发启动 005、整体计划 v0.3、设计决策 002。保留既有工作区修改，未重写通用 Agent 框架或建设知识库平台。

## 阶段一：API 与网页聊天

调用链：Open WebUI Pipe → FastAPI → WebService → `open_personal_session` → ContextService → Deep Agents/LangGraph → Ollama。API 自带简易页面，作为独立入口及界面降级；Open WebUI 仍为主要聊天容器。

核心参数：`MAGI_API_TOKEN` 至少 24 字符；固定服务 owner；默认 `127.0.0.1:8765`；单 worker。生命周期持 `ApplicationLock`，模型任务和确认写入共享非排队串行门。连接在执行线程内创建和关闭，不跨线程缓存会话。后台任务不请求 MAGI 模型。

WebTurn 的唯一键是 owner/chat/用户消息 ID；相同输入回读原结果，不再调用模型；不同输入冲突。父回复必须是该会话最新回复；上游消息树还要证明当前用户消息延续该分支。显示历史来自业务库或 WebUI，推理历史来自当前 epoch 对应 checkpoint；记忆变化后每次请求重新解析绑定。

正常：网页发送普通消息，刷新后仍有正文；普通模式不运行三人格。失败：缺少凭据返回 401；忙返回 429；旧消息分叉返回冲突。中断任务保留为 INTERRUPTED，不在重启时自动重放。

阅读：`integrations/openwebui/magi_pipe.py` → `web/app.py` → `web/service.py` → `harness/runtime.py` → `personal/context.py`。可选练习：用同一消息 ID 提交不同文本，解释为何需要新消息。

## 阶段二：三人格与续聊

调用链：WebService 冻结 TaskSnapshot → Council 顺序运行 MELCHIOR / BALTHASAR / CASPER → 综合 → 服务端渲染。身份、状态、耗时和调用数由程序赋值。首轮输入完全相同，不含另两人的意见；三个视角不等于三个独立证据来源。

参数：人格输出 768 token，综合 1536（按真实截断记录从 1024 校准）；各调用最多配置超时，整体不超过四倍单次超时且封顶 240 秒；最多 3+1 次，无自动修复或重试、无写工具。快照有容量上限，引用只接受快照登记 ID。列表和文本长度受 Pydantic 限制。显式 `time_budget_minutes` 存在时，各步骤必须有分钟数，合计不得超限。

快照与每个已完成人格随阶段提交业务库；综合失败保留已有意见并明确 PARTIAL，没有候选。成功后先保存业务结果，再将有界综合候选写入 checkpoint；不把完整三人格意见反复注入日常历史。使用 turn 派生固定消息 ID，补写可重复执行；未同步结果先修复再接受新轮次。旧 epoch 结果不写回新段。

正常：日常提供背景 → 审议 → 日常追问候选第一步。失败：JSON 非法、超时、截断、未知引用、超预算均不伪造成功。首轮真实试验发现完整意见注入导致容量拒绝，已改为只同步综合候选。后续长会话仍可能触发既有保守容量上限，需要新建聊天；未实现无限历史摘要。

阅读：`council/service.py` 的结构、run、render → `web/service.py` 的快照与 `_sync` → 对应测试。可选练习：比较“同一快照”与“多个独立证据来源”的区别。

## 阶段三：确认、恢复与反馈修订

调用链：综合成功 → SQLite 不可变 proposal → 网页展示 → Action 携带 ID/摘要/回复归属 → WebService → PlanService.create/update → 同事务计划 revision 和 Operation → 回读核对 → 真实回执。

候选冻结 title/body/hash/epoch/owner/chat/reply。更新候选另绑 target ID 和 expected_version。确认不接受模型正文或 `approved=true`，不请求模型。request_id 固定为 `proposal:<id>:save`；即使响应丢失或已提交后 epoch 改变，重试仍回原操作。未保存旧 epoch 候选拒绝。版本过时不能覆盖，旧 revision 始终可查。

来源增加程序控制的 `confirmed_web_proposal`，纳入 Web 幂等摘要；保留 CLI 默认来源及旧请求摘要兼容。业务 schema 从 2 升到 3，新增 web_turns 和 plan_proposals，已有记忆/计划表保留。读连接退出上下文时主动关闭，修复 Windows 临时文件占用。

反馈使用原计划与本次约束组成新快照；修订理由进入不可变计划正文，不能只停留在模型临时输出中。网页和 API 都能读 v1/v2；模型查询仍遵守 needs_review 正文隔离规则。

正常：确认 v1 → 重启 API 查询相同 ID → 两小时反馈生成更新候选 → 确认得到 v2 → 对照 v1/v2。失败：错归属/摘要、过时 epoch、旧版本均拒绝；BUSY 不冒充保存成功。重复确认只产生一个 revision。

阅读：`web/service.py` 的 confirm → `personal/plans.py` → `personal/store.py` → `tests/test_web_mvp.py`。可选练习：解释 request_id、expected_version、source_epoch 各防止哪一类错误。

## 验证与交付

安装与运行：README 和 `integrations/openwebui/README.md`。实测结果、真实 ID、耗时及失败记录见《MAGI_MVP演示与验收》。离线测试证明边界，真实浏览器与模型证明接通，两者不替代建议质量评估。

仍需后续完善：长会话摘要、流式输出、网页自由编辑计划、记忆 UI、更多真实对照案例、模型提供方扩展。单用户与本机部署是本版范围，不提供多租户或互联网发布承诺。
