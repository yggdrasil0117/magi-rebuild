# MAGI 模块047：记忆召回离线基线

2026-11-10

用户问“复习可以投入多久”，普通聊天可以把当前可见的所有有效记忆交给模型，也可以打开语义召回，只注入与问题相关的记录。两种方式都可能找到备考时间。区别在于通勤方式等无关记忆是否一起进入上下文，以及检索花费多少时间。本轮给这两条实际读取路径建立固定基线，便于后续判断调整阈值或排序规则带来的变化。

## 从一条备考记忆看整轮评测

脚本在临时目录创建真实 SQLite 业务库和本地 Qdrant 索引。它通过 `MemoryService` 写入合成记忆。全局“备考时间”是每周 10 小时，备考会话另有同键的 2 小时记录。其他会话还有一条专属项目记忆，库中同时放入全局讲解风格和通勤记录作为干扰项。

第一题在普通会话问“复习可以投入多久”，期望命中全局时间 v1。接着使用没有匹配资料的问题，再用相同备考问题检查会话同键覆盖。专属项目问题在会话内外分别执行，验证一条事实可以在授权场景中命中，同时在另一个会话里被排除。最后将全局备考时间改为 4 小时，再删除，检查旧版是否还能从索引返回。每道题两组各执行一次，实际返回项、失败状态及耗时都随结果保留。

## 两组怎样运行

下面是 [evaluate_memory_recall.py](../scripts/evaluate_memory_recall.py) 中 `evaluate` 的内层 `run_case` 摘录。省略了结果归一化和记录步骤。

```python
default = measure(
    lambda: {
        "status": "OK",
        "items": context.build_current_context(chat, include_identity=True)[1],
    }
)
semantic = measure(
    lambda: recall_module.recall(context, chat, question, settings)
)
```

`chat` 和 `question` 来自固定案例。默认组读出普通聊天可注入的记忆，保留 ID 和版本供计分。语义组走产品原有的 `recall`，同样先限定有效作用域，再更新索引并在 Qdrant 中按相似度和阈值查询。两组读取同一个用例的业务状态。测试替身只提供固定向量，例如把“备考时间”和“复习可以投入多久”映射到同一主题轴。全局和会话里的两条备考记忆向量相同，所以正确选出其中一条仍依赖 MAGI 的作用域和版本逻辑。SQLite 负责业务数据存储，Qdrant 执行相似度查询，MAGI 决定候选范围及当前有效版本。

每条实际返回项转换为 `study:v1` 这样的稳定标签，并保留正文。标签 `study` 在报告里代替随机的记忆 ID，版本标明同一事实经历哪次更新。会话 ID 仅用于决定哪些范围可读，创建和变更时的 `request_id` 用于识别重复操作。这些身份与评测案例名各有用途。相同 ID 返回旧版本时，该项仍会被记为误召回，并同时触发禁止项检查。

## 结果怎么算

`summarize(attempts, arm)` 逐题取实际结果与标准答案的交集。下段为函数内摘录。

```python
actual = {item["identity"] for item in result["items"]}
target = set(attempt["expected"])
hits += len(actual & target)
returned += len(actual)
expected += len(target)
empty_ok += result["status"] == "OK" and not actual and not target
boundary_violations += len(actual & set(attempt["forbidden"]))
```

把所有题的命中数除以返回总数得到微平均准确率，除以标准答案总数得到微平均召回率。期望为空的题还单独计数。比如嵌入服务失败时，现有召回接口返回 `UNAVAILABLE` 和空列表。这次尝试会计入失败，不能取得“正确返回空结果”的分数。禁止项用于单独查看跨会话或陈旧版本问题。有错误时脚本先保存完整报告，再返回非零退出码。真实云端模式会记录内容命中与漏召回，退出状态主要检查调用状态和禁止项。已有输出路径会被拒绝，复测需要新文件。

## 验证与数据边界

| 7 个固定案例 | 默认注入 | 固定向量召回 |
| --- | ---: | ---: |
| 命中数 / 标准答案总数 | 4 / 4 | 4 / 4 |
| 返回总数 | 21 | 4 |
| 误召回数 | 17 | 0 |
| 正确返回空结果的案例 | 0 / 3 | 3 / 3 |
| 禁止项或失败次数 | 0 | 0 |
| 单次运行中的耗时中位数 | 5.976 ms | 26.018 ms |

固定向量本身预设了主题关系，结果用于验证对照流程与产品边界。默认组设计为提供所有可用记忆，表中的误召回表示它送入了评测题当前用不到的记录。耗时只覆盖上下文读取或检索，包含数据库和索引工作。第一次语义读取约 694 ms，包含冷启动、导入和初建索引。中位数混合了冷启动、热查询、会话补索引及变更清理，样本太小，无法作为真实线上延迟或性能提升依据。逐题结果见[原始报告](validation/047-memory-recall-offline.json)。

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluate_memory_recall.py --output docs/validation/047-memory-recall-offline.json
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluate_memory_recall.py --cloud --output docs/validation/047-memory-recall-cloud.json
.\.venv-harness\Scripts\python.exe -X utf8 -m pytest -q tests/test_memory_context_035.py tests/test_personal_context.py tests/test_personal_memory.py
.\.venv-harness\Scripts\python.exe -m ruff check scripts/evaluate_memory_recall.py
.\.venv-harness\Scripts\python.exe -m ruff format --check scripts/evaluate_memory_recall.py
```

离线评测退出码为 0，记忆专项测试 15 项通过，Ruff 检查和格式验证通过。云端尝试退出码为 2，提示缺少 `SILICONFLOW_API_KEY`，请求尚未发出，未生成云端结果。配置凭据后可用 `--cloud` 评测同一批合成文本。本轮没有生成模型调用、用户确认或正式数据库写入。合成记忆的写入和索引同步发生在临时目录，只有 JSON 报告长期保存。未运行真实嵌入质量评测、浏览器、服务重启、完整回归或在线 A/B 测试。记忆有效期和自动遗忘仍待开发。

## 面试知识关联

评测需要同时知道“该找的有没有找到”与“找回的有多少相关”。这分别对应召回率和准确率。业务还需要观察应该留空的题，以及绝不能返回的旧版本和会话专属项。这里先用手工标注的小集合，使用 SQLite 和 Qdrant 实际执行读取，把嵌入的不确定性替换为固定主题向量。这样程序边界失败时容易定位，但模型的理解能力要靠真实嵌入与更多独立标注样本另测。若被问到是否做了 A/B，当前做法是在同一批题上逐题配对对照。在线 A/B 还涉及用户或流量分组、预先定义指标及观察期，本轮没有这类数据。

建议先读原始报告中“同键会话覆盖”和“记忆更新”两个案例，再看 `run_case` 与 `summarize`。之后可以自查两个问题。为什么两组都命中 4 条目标记录，准确率仍然不同？为什么 `UNAVAILABLE` 和正常空结果必须分开计数？
