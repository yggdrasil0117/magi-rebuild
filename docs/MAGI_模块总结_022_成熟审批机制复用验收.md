# 模块022：成熟审批机制复用验收

日期：2026-10-31。按用户“优先复用成熟内容，在其上做业务改造”的方向完成一轮兼容性验收。先写[设计决策007](MAGI_设计决策_007_成熟审批机制复用验收.md)，再实现独立适配器与验收脚本。

## 1. 本轮结论与实际交付

**当前锁定的Deep Agents可复用来暂停工具、保存待审状态和恢复执行。** 不需要自己再写通用暂停/恢复引擎，也没有为此升级框架。

实际使用：Deep Agents 0.7.32、LangChain 1.4.7、LangGraph 1.2.18、SQLite checkpoint适配器3.1.2。Deep Agents的`interrupt_on`会配置HumanInTheLoopMiddleware，暂停和恢复由框架实现；MAGI适配器负责绑定当前工作单、owner和摘要，再调用已有TaskDocuments.confirm执行文件业务。

本轮是**完整验收轮，不是网页审批上线轮**。新增独立试验入口、18项回归测试和可复现的跨进程证据；生产网页、终端和Open WebUI没有注册创建文件工具，仍走之前的预览/页面确认流程。

协作约定已补充优先复用成熟组件、区分上游与自有贡献的要求，后续不为学习重复实现通用基础机制。

## 2. 请求怎样流转

```text
独立试验宿主准备已有工作单预览
→ 固定脚本模型提出创建工具请求
→ Deep Agents在工具执行前中断
→ SQLite checkpoint保存待审动作
→ 原进程退出
→ 新宿主进程读取相同thread_id与interrupt_id
→ MAGI核对当前动作、owner、文档编号及摘要
→ 宿主明确提供approve/reject
→ Command恢复框架
→ 批准：现有TaskDocuments.confirm核对并创建文件
→ 拒绝：框架跳过工具，业务预览仍未创建文件
```

这里的批准/拒绝由CLI命令和测试驱动器模拟用户操作；没有声称真实浏览器按钮已经完成审批集成。模型只提出动作，没有权力给自己批准。

### 上游与自有部分

| 机制 | 由谁负责 |
| --- | --- |
| 暂停工具、等待外部决定 | Deep Agents / LangChain审批中间件 |
| 持久化暂停位置、重新打开、Command恢复 | LangGraph + SQLite checkpointer |
| 哪些工具需要审批、只允许批准/拒绝 | MAGI配置 |
| owner、冻结文档、摘要、当前中断匹配 | MAGI试验适配器 |
| 文件不覆盖、冲突、重复确认和真实写入核对 | 原有TaskDocuments服务，未重新实现 |

`open_session`只增加可选`interrupt_on`透传，默认None。现有入口不启用中断；试验直接驱动图，不能使用原有ask把中断误当完整回答。显式model参数拒绝None，不隐式调用云端或本地模型。

## 3. 哪些状态与ID不能混淆

| 对象 | 含义 |
| --- | --- |
| document_id | 已有工作单预览/产物，不是新的计划版本 |
| payload_hash | 需要审批的完整冻结文档摘要 |
| thread_id | 程序根据owner和文档生成的框架状态指针，用于读回同一暂停流程 |
| interrupt_id | 当前这一次具体暂停位置的编号，不能随便用旧值或别人的值替代 |
| phase | 试验适配器根据框架状态给出的NOT_STARTED/PENDING/COMPLETED/BLOCKED |
| document_status | 原业务库的PREVIEW/CREATED，反映文档业务事实 |

**COMPLETED不是文件已创建的同义词。** 拒绝后框架同样能完成，但工作单仍是PREVIEW；文件创建成功后最终模型回答失败，框架仍可能BLOCKED，而业务库已经CREATED。不能因回答失败就删除真实文件或重试整段模型调用。

BLOCKED在本适配器中表示仍有未完成节点但没有可审批中断，包含工具失败或最后回答失败；它不是持久业务失败表的新枚举，也不自动触发重放。重复start只是读回状态，重复审批返回NO_PENDING_APPROVAL。

框架拒绝的是本次工具调用，**不自动删除或作废业务预览**。如果将来产品需要“拒绝后永久撤销预览”，应由业务生命周期定义，不能从框架reject推断已经完成。

## 4. 验证事实

### 离线真实框架与业务服务

固定模型替身，真实Deep Agents执行图、HumanInTheLoopMiddleware、SqliteSaver和现有工作单文件服务。第一轮15项针对性测试全部通过，增加非字符串决定及模型不得回退的边界后，本轮共18项新增测试。

覆盖未批准不执行、明确批准才执行、拒绝不执行、重复start、重复决定不重放、编辑/响应/字符串true/布尔/空值/容器决定拒绝、过时中断与摘要变化不消耗原待审状态、其他owner拒绝、重新打开恢复同一中断、不匹配的模型动作拒绝、框架不允许编辑动作、用户文件冲突保护，以及工具成功后回答失败的业务事实保留。

完整回归先375项通过；补充model=None禁止回退检查后，最终：

`.venv-harness/Scripts/python.exe -m pytest -q`

**376 passed，40.47秒，3条第三方弃用警告。** 本轮5个Python文件Ruff检查及格式检查通过。完整回归确认原日常工具、三人格、候选/计划、网页和终端已有测试仍通过。

### 跨进程固定验收

初步手工CLI探针先完成一次init→start→新进程approve，暂停时无文件，批准后文件摘要与预览一致，保留在[初步探针记录](validation/022-approval-initial-probe.json)。

随后固定脚本启动15个独立命令进程，三组流程均通过：

| 案例 | 实际结果 |
| --- | --- |
| 批准 | 新进程恢复相同中断，批准后1个文件，摘要一致 |
| 拒绝 | 新进程恢复相同中断，拒绝后0文件，预览保持PREVIEW |
| 过时与重复 | 旧中断编号拒绝，原审批仍可使用；合法批准后1个文件，重复批准拒绝且无第二文件 |

全部命令结果，包括预期非零退出的拒绝，记录在[跨进程证据](validation/022-approval-process.json)。不是同一Python对象里重新调用函数来冒充进程重启。

证据保留执行时的源码指纹。跨进程验收后只追加model=None显式拒绝，固定命令均使用脚本模型，最终完整回归覆盖新增检查；保留原指纹与最终指纹，不覆盖原命令结果。

没有调用真实千问或Ollama，没有读取密钥，没有调用生产数据库或变更schema，没有浏览器验收、真实模型审批行为评测或多worker竞争测试。脚本模型不证明真实模型不会误选动作，也不证明业务数据与checkpoint之间具有跨库原子提交。

## 5. 采用结论和生产接入缺口

采用结论：**后续出现需要暂停并继续的模型行动时，优先使用现有框架审批，不再自研通用暂停引擎。** 现有网页确认已经有自己的可靠业务路径，不为“用了新机制”立即替换。

生产接入前仍要完成：

- 将审批绑定真实认证用户和当前请求，不能直接公开裸Command恢复接口。
- 持久化产品层待审/拒绝状态，和页面显示、请求重试、版本检查协同。
- 明确并发决定、跨恢复预算以及框架状态与业务提交不一致时的恢复策略。
- 将网页/终端入口接入并执行真实浏览器与模型验收。

本轮适配器仅适合独立单进程串行试验，不把owner字段检查宣传成多账号隔离。模型呼叫、框架中断、宿主批准、业务写入仍是不同事实。

## 6. 可复现与阅读顺序

批量验收（输出到新文件，保留旧证据）：

```powershell
.\.venv-harness\Scripts\python.exe scripts/evaluate_approval.py --output output/approval022/new-report.json
```

手工试验：`probe_approval.py init --data-dir <独立试验目录>`返回文档编号和摘要，再依次start、status、approve或reject；恢复需提供实际interrupt_id和payload_hash。脚本的`--help`列出参数。不使用生产`.magi`目录进行此试验。

先读`harness/approval_trial.py`的status/start/decide：哪些交给框架，哪些必须自己核对。再看工具内部如何直接复用TaskDocuments.confirm，最后看跨进程脚本和拒绝案例。

官方资料：[Deep Agents人工审批](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)、[LangGraph中断](https://docs.langchain.com/oss/python/langgraph/interrupts)。仅借助通用概念，实际兼容以本机锁定版本测试为准。

本轮为兼容性验收，不新增question.md重大工程条目，不夸大复用节省比例。完成后等待用户阅读复述。
