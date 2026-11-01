# 模块025：Yggdrasil检索复用评估

日期：2026-11-01。用户要求先判断企业文档检索项目能否复用，不能复用再扩建MAGI。

## 1. 结论与范围

**可以有条件复用检索和证据底座，当前不需要另造一套RAG。** 复用的是“找到候选资料并保留原文与来源”的组件，不是对回答质量的背书；MAGI仍须补资料进入审议、版本选择和证据充分性判断。

审查源仓库提交：`ab50aadda13fb169b1f50f3743cdc08b80a9ab6e`。检索、鉴权、证据包、模型适配源码及历史评测已检查；针对性Java测试在MAGI忽略目录中的独立源码副本执行，没有修改Yggdrasil源码或正式知识库。MAGI生产代码、本地正式数据、密钥与既有用户改动均未纳入本轮变更。

本轮是完整复用评估，尚未将Yggdrasil接入MAGI网页、三人格或工具列表。设计见[决策009](MAGI_设计决策_009_Yggdrasil检索复用边界.md)。

## 2. 值得保留的实现

- 已有多格式摄取、Canonical Chunk与检索文本分离、全文/中文子串/向量候选及RRF融合、可选BGE精排。默认FeatureHash是轻量特征哈希，不是BGE语义模型。
- 检索SQL按组织、工作区、资料范围、密级及文档/来源状态过滤；返回正文前重新核对权限；精排只能选择授权候选。
- 检索接口直接返回真实正文、结构路径、source/document/version/chunk ID及正文摘要，不必把Yggdrasil生成的答案当原始资料。
- 冻结证据包保存原文和摘要；读取时重新检查来源/文档状态、密级及实际chunk归属。
- Core检索调用与Ollama回答生成分离，可让MAGI继续用云端千问。向量与精排的模型资源需求仍要单独评估。

## 3. 真实缺口及其业务影响

| 发现 | 依据 | 对MAGI的影响 |
| --- | --- | --- |
| 搜到不等于能回答 | 历史20题检索报告的无答案空结果准确率为0，整体门禁为false | 需要核对正文是否支持具体结论；这是候选检索指标，不能推导整个Yggdrasil问答都不拒答 |
| 嵌入失败阻断词法检索 | 新增离线探针模拟embedding异常，确认尚未调用repository就失败 | 不可承诺向量故障时自动关键词降级 |
| 冻结不判断相关性 | 新增探针将极低分、不回答问题的正文冻结成功 | FROZEN说明已保存证据，不说明问题已解决 |
| 冻结包缺评分执行来源 | 搜索响应有provider/精排状态，冻结对象没有这些字段 | 不能把不同评分体系套同一个阈值，也不能把前一次search的元数据当成freeze那次检索的元数据 |
| 默认不保证只取当前版本 | SQL无最新版本过滤；新增同文档双版本探针 | 需要区分历史比较与当前规则，保留冲突和版本信息 |
| 工程测试不等于语义质量 | 现有测试覆盖大量mock边界，历史数据量小 | 复用接入后仍要用实际任务评估 |

上述是能力边界，有些行为本来适用于候选召回或历史比较，不应全部称作程序bug。本轮未修复这些缺口，不将其记入question.md。

## 4. 历史质量证据复核

`chinese-retrieval-v1`只有10份短资料、20个问题，其中19个有目标文档，1个无答案；所有forbiddenDocumentIds均为空。因此该数据上的“越权数0”没有真正挑战跨权限边界，权限结论必须另看专项测试。

保存的BGE-M3、未启用精排结果中：Recall@5为100%，MRR@10约0.804，Precision@5为0.20，无答案空结果准确率为0，检索门禁失败。前五条平均混有不少无关文档，也不能拿10份短资料的成绩推导大知识库表现。此次重算已存排名，不是重新调用BGE或重新检索。

另有2026-10-04 GPU问答行为门禁：2轮、每轮16例，12例回答和4例拒答通过。它证明历史固定配置及样本的问答边界，与上一段检索层空结果指标并不矛盾。没有将这份历史报告称为当前版本真实模型复测。

2026-10-10分块消融是15份合成长文、50题的先导实验，文档明确提示尚未逐题人工复核，词法检索为Python近似实现，不能当作真实Core/PostgreSQL的正式成绩。

## 5. 本轮验证记录

最终测试和历史重算数据见[结构化证据](validation/025-yggdrasil-reuse.json)。测试通过中的新增审查探针表示“边界/缺口已复现”，不是已经修复。

最终为**49项专项测试通过、0失败、0错误、0跳过**，来自两组范围不同的执行，而非重跑MAGI全量388项测试：

| 类型 | 数量 | 实际证明范围 |
| --- | ---: | --- |
| 上游检索/证据/鉴权/索引与文档处理单元测试 | 41 | mock与轻量逻辑边界，首次离线Maven运行14.232秒 |
| 新增复用边界单元探针 | 2 | 向量故障阻断、低分证据可冻结及冻结元数据缺失 |
| 上游证据撤销数据库集成测试 | 5 | 真实PostgreSQL上的来源停用、文档状态/密级、跨范围/错误版本与SQL过滤 |
| 新增双版本数据库探针 | 1 | 同一文档v1为600元、v2为800元时，两个版本均被检索并物化，未默认选择最新版 |

数据库使用独立pgvector/PostgreSQL 17.11容器，实际应用48个迁移；没有建立BGE向量，双版本探针验证的是词法检索SQL。新增及数据库8项最终Maven运行51.147秒。测试副本的全部上游src与pom.xml均与原仓库比较摘要，具体文件数见结构化证据；本轮新增探针不冒充上游原有测试。

所有尝试保留，不能只看最终通过：

1. attempt-1：41项通过。
2. attempt-2：新增2项单元探针通过，两个数据库测试类初始化均因Windows连接WSL的55435端口被拒而报错；无真实SQL断言执行。
3. 切换容器执行时有一次PowerShell逗号参数未加引号的解析失败，测试没有启动。修正引号后attempt-3仍因临时数据库容器已不存在而未启动Maven。不能把这两次记为测试通过；未确定容器消失根因。
4. attempt-4：改为同一次WSL调用内创建数据库、测试、清理，5项上游数据库测试和2项新增单元探针通过；新增版本测试被`artifact_key_check`拒绝，因为本轮测试夹具使用了错误对象键。失败SQL及当时夹具副本保留在忽略目录。
5. attempt-5：仅修正测试夹具对象键为上游规定的组织/工作区/来源/摘要路径，8项通过。未修改Yggdrasil检索或数据库约束以让测试通过。

本机原始日志、失败XML和测试副本位于忽略目录`output/approval-rag-reuse-025`；提交的JSON保留各次日志摘要、数量和错误类别，不导出可能带环境信息的完整Surefire报告。临时数据库由脚本清理。查询WSL/Docker时观察到既有业务容器按启动策略启动；本轮未对它们执行停止、重建、删除或资料修改。

历史排名重算与原报告candidate指标完全一致，保留其`passed=false`；本轮未通过调阈值或挑选样本将失败改成通过。证据汇总脚本实际执行、`--help`、Ruff检查和格式检查通过。

未进行真实BGE质量复测、千问/Ollama生成、浏览器或MAGI接入验收；本轮测试没有启动BGE、下载模型或向Ollama发送生成请求。生产MAGI代码未变，未重复执行其全量回归。

## 6. 新增工具与复现

- `scripts/fixtures/rag_reuse/RagReuseAuditTest.java`：向量故障和证据冻结语义探针，使用mock，不连接模型。
- `scripts/fixtures/rag_reuse/RagReuseDatabaseAuditTest.java`：真实PostgreSQL验证同文档旧新版本同时召回，使用合成数据。
- `scripts/fixtures/rag_reuse/run_database_audit.sh`：在同一次WSL调用内启动独立数据库、运行SQL测试、清理自建容器；使用已有镜像，不启动业务栈。
- `scripts/evaluate_yggdrasil_reuse.py`：重算历史结果、收集去除环境信息的测试结果和源码摘要；拒绝覆盖已有证据。不输出原始Surefire properties、登录会话或用户文档。

复测先将上游`services/core-api`中的pom.xml与src复制到一个全新的`output/approval-*/core-api`，把本轮两份Java探针复制进副本的`src/test/java/com/yggdrasil/knowledge`。不要把探针写入上游工作目录。

使用已有JDK21/Maven缓存离线运行8组上游测试：RetrievalServiceTest、EvidencePackageServiceTest、FeatureHashEmbeddingProviderTest、SourceAuthorizationServiceTest、TenantContextResolverTest、TenantIsolationTest、DocumentProcessingServiceTest、EmbeddingIndexServiceTest。实际命令中的`-Dtest=...`参数在PowerShell中应整体加引号。

数据库测试通过WSL执行上述shell脚本，依次传入隔离core-api目录和Maven缓存的Linux绝对路径；要求已有`pgvector/pgvector:pg17`及`maven:3.9.11-eclipse-temurin-21`镜像。该脚本只创建自己的临时数据库，不开放宿主机端口，并以trap清理自身容器。

最后收集：

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluate_yggdrasil_reuse.py `
  --source D:/yggdrasil-project `
  --audit-dir output/approval-rag-reuse-025 `
  --revision ab50aadda13fb169b1f50f3743cdc08b80a9ab6e `
  --output output/approval-rag-reuse-025/new-review.json
```

源码指纹检查要求编译副本与源仓库一致。脚本无一键启动正式服务或调用模型的功能。

## 7. 下一轮边界

建议交付“资料驱动的三人格审议”：复用Core API，保留来源与版本，以固定问题验证命中、缺口、冲突及引用。身份连接方式和嵌入部署需结合可运行配置确定；外部资料只做本轮依据，不自动转为长期记忆。当前MVP仍可独立使用。

本轮不新增生产工具、不修改审批机制、不重建知识库，不将组件复用评估包装为RAG接入完成。完成后等待用户阅读和复述。
