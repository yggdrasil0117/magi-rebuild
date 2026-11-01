# 模块029：硅基流动BGE-M3接入与向量资料审议

日期：2026-11-01。用户明确选择硅基流动与BGE-M3后实施；不启动本地生成或嵌入模型。本轮完成云端稠密向量接入、实际三人格入口、分块缓存、显式降级、真实对照与保存/重启边界验证。关键词基线保留，未引入向量数据库、融合重排或通用知识库平台。

## 1. 用户能用来做什么

用户可能不用资料原词提问。旧版关键词基线对“输出清单算办妥了吗”找不到“计划导出只生成文件，不代表执行完成”这段文字。现在可以把问题和原文分别转为向量，再比较语义相似度，将候选原文送给三人格，而不是让模型凭空补来源。

正常启动仍是`scripts/start_magi.ps1`。当前`magi.qianwen.toml`明确设置`[embedding].enabled=true`：脚本分别隐藏输入千问生成API Key和硅基流动embedding API Key，已有对应进程环境变量时不重复询问。环境变量分别为DASHSCOPE_API_KEY和SILICONFLOW_API_KEY；网页只填写本机MAGI服务凭据，不填写这两个平台密钥。

授权资料仍来自`[tools].root`，当前是docs。启用云端向量意味着本轮选入向量预算的授权文本片段和问题会发送硅基流动；千问继续接收审议快照。没有上传全电脑文件。启动脚本不自动读取D盘混合凭据文件；本轮验收只在用户授权下读取明确标注BGE-M3的完整值，千问值单独提供，未保存到Git或报告。不要再用前缀正则裁剪密钥。

如要关闭向量，将`enabled=false`，即使用原关键词基线。直接启动Python服务未配置硅基流动凭据时，本轮检索会显式退回关键词；程序和页面都标记降级，不假装语义检索成功。

## 2. 哪些机制变化，哪些事实仍需区分

```text
用户问题 + 授权文本
        ↓ 有界扫描、文档版本、160字原文块
        ↓
Embedding Provider：硅基流动 BAAI/bge-m3
        ↓ 查询向量 + 原文块向量（优先读取缓存）
余弦相似度 → 阈值过滤 → 前5个候选原文
        ↓
同一份证据快照 → 三人格意见 → 综合候选
        ↓ 程序检查引用
用户确认 → 正式计划及其版本写入数据库
```

embedding只把文本变成数字，不生成行动计划；千问负责意见和候选生成。向量相似不意味着原文支持结论。引用合法不意味着资料真实或当前适用。用户确认不意味着任务已执行完成。资料索引和向量缓存可以在确认前写入，正式计划仍需确认。

本轮在`WebService.turn`组装三人格快照时接入。普通日常聊天原有字面搜索工具仍保持旧行为；没有把所有聊天工具暗中改成向量检索。没有引入关键词/向量融合：启用时按稠密向量排序，失败时才明确降级关键词。

## 3. 使用的成熟组件与自有适配

- 硅基流动提供BGE-M3推理服务，生成1024维稠密向量；MAGI没有训练模型或实现推理服务。这里只使用接口返回的稠密向量，不声称接通BGE-M3的稀疏或多向量能力。
- HTTP使用已有httpx，缓存使用已有SQLite；无需部署新向量数据库或增加模型下载。
- MAGI实现接口适配、输入预算、响应校验、当前版本候选选择、缓存身份、余弦排序、失败状态与原有确认链路接入。
- [硅基流动官方接口](https://siliconflow.readme.io/reference/createembedding)列出POST `https://api.siliconflow.cn/v1/embeddings`、模型`BAAI/bge-m3`、批量字符串input和float编码。官方文档列出BGE-M3输入上限8192 tokens；本轮采用更保守的字符预算，并不声称实现了精确token计数。

## 4. 配置与接口参数

| 参数/对象 | 来源 | 意义与边界 |
| --- | --- | --- |
| embedding_enabled | TOML `[embedding].enabled` | bool，默认False；当前千问产品配置显式True |
| embedding_min_score | TOML `[embedding].min_score` | 0—1有限数，默认0.45；经验候选阈值，不是45%正确率 |
| EmbeddingProvider.embed(texts) | 检索程序传入文本列表 | 返回与输入顺序一致的1024维向量列表，不接受模型工具随意改预算 |
| SiliconFlowEmbeddingProvider.identity | 程序固定字符串 | 绑定端点、模型、1024维和本地格式版本，区分不同缓存空间 |
| client | 构造函数可选参数 | 仅用于注入HTTP离线替身；真实使用10秒超时httpx客户端，无自动重试 |
| calls / tokens | Provider运行时统计 | 实际请求数和已返回用量；未返回用量不等于免费 |
| request.query | 当前用户问题 | 业务允许最多8000字，向量阶段只取前2000字并标记裁剪 |

`embedding.py`的EmbeddingError仅携带稳定错误码，不把请求头、平台异常正文或资料写进异常报告。缺凭据、401、403、429、超时、网络失败和非法响应分别转为稳定状态。固定维度是当前适配契约，将来更换不同维度模型需要同时更新校验与缓存身份，不是改个模型名就可混用。

真实HTTP请求的核心与当前代码一致：

```python
response = client.post(
    "https://api.siliconflow.cn/v1/embeddings",
    headers={"Authorization": "Bearer " + key},
    json={
        "model": "BAAI/bge-m3",
        "input": texts,
        "encoding_format": "float",
    },
)
```

`key`仅来自SILICONFLOW_API_KEY；`texts`由查询或原文分块组成。响应按index排序，拒绝重复、缺失或非整数index；核对条数、模型名（若返回）、向量维度、有限数和非零长度。某些平台可能乱序返回，不能直接把响应数组第0项当成输入第0项。

## 5. 核心代码一：归一化为何要做

文件`src/magi/embedding.py`，实际函数：

```python
def normalize(vector, dimension=1024):
    """校验维度、有限数与非零长度并归一化，缓存与响应使用同一边界。"""
    if not isinstance(vector, list) or len(vector) != dimension:
        raise EmbeddingError("INVALID_VECTOR")
    if any(
        type(v) not in (int, float) or abs(v) > 1e100 or not math.isfinite(v)
        for v in vector
    ):
        raise EmbeddingError("INVALID_VECTOR")
    norm = math.sqrt(sum(v * v for v in vector))
    if not math.isfinite(norm) or norm == 0:
        raise EmbeddingError("INVALID_VECTOR")
    return [v / norm for v in vector]
```

`vector`来自平台响应或本地缓存，`dimension`默认为1024。返回单位长度的向量；拒绝错误维度、布尔值、NaN/Infinity、过大数和零向量，避免无意义相似度或数值异常进入排序。

余弦相似度比较向量方向。归一化之后，两个向量的逐维乘积之和就是余弦分数。教学例子`[3,4]`归一化为`[0.6,0.8]`；这里用2维帮助理解，真实接口固定1024维。`0.65`表示在当前模型空间更接近某些内容，不表示65%的事实可靠性。

## 6. 核心代码二：当前块、缓存与排序

文件`src/magi/embedding.py`，实际函数：

```python
def vector_rank(db, candidates, query, provider, min_score):
    """只向量化当前扫描前128块；按provider+chunk缓存，不查询历史块。

    db来自本轮资料索引连接。只保存完整校验成功的一轮新向量；异常交给调用方显式降级。
    小规模直接计算余弦，不引入向量数据库。min_score是可配置经验阈值，不是事实概率。
    """
    db.execute(
        "CREATE TABLE IF NOT EXISTS embeddings(provider TEXT NOT NULL, chunk_id TEXT NOT NULL, vector TEXT NOT NULL, PRIMARY KEY(provider,chunk_id))"
    )
    pool = candidates[:128]
    if not pool:
        return [], dict(calls=0, tokens=0, cache_hits=0, pool_truncated=False)
    query_vector = normalize(provider.embed([query[:2000]])[0])
    vectors, missing = {}, []
    for item in pool:
        row = db.execute(
            "SELECT vector FROM embeddings WHERE provider=? AND chunk_id=?",
            (provider.identity, item["chunk_id"]),
        ).fetchone()
        if row:
            try:
                vectors[item["chunk_id"]] = normalize(json.loads(row[0]))
            except (ValueError, EmbeddingError):
                missing.append(item)
        else:
            missing.append(item)
    for start in range(0, len(missing), 32):
        batch = missing[start : start + 32]
        generated = provider.embed([item["quote"] for item in batch])
        if len(generated) != len(batch):
            raise EmbeddingError("INVALID_RESPONSE")
        for item, vector in zip(batch, generated, strict=True):
            vectors[item["chunk_id"]] = normalize(vector)
    for item in missing:
        db.execute(
            "INSERT OR REPLACE INTO embeddings VALUES (?,?,?)",
            (
                provider.identity,
                item["chunk_id"],
                json.dumps(vectors[item["chunk_id"]]),
            ),
        )
    ranked = []
    for item in pool:
        score = sum(
            a * b for a, b in zip(query_vector, vectors[item["chunk_id"]], strict=True)
        )
        if score >= min_score:
            ranked.append(
                dict(item, keyword_score=item["score"], score=round(score, 6))
            )
    ranked.sort(key=lambda m: (-m["score"], m["path"], m["line"], m["offset"]))
    return ranked, dict(
        cache_hits=len(pool) - len(missing),
        embedded_chunks=len(missing),
        pool_truncated=len(candidates) > 128,
        query_truncated=len(query) > 2000,
    )
```

### 6.1 参数、调用与返回

`db`来自本轮本地检索打开的retrieval.db连接。`candidates`在向量开启时包含当前扫描读到的所有原文块，不要求先命中关键词；否则仍会漏掉完全换词的问题。`query`是用户本轮问题。`provider`是可替换向量能力。`min_score`来自配置。

返回`(ranked, diagnostics)`：ranked每项仍保留文档、版本、分块ID、原文、路径和位置，score改为余弦分数，原关键词计数留在keyword_score用于解释；diagnostics记录缓存命中、新嵌入块数以及分块/问题预算是否截断。

### 6.2 为什么只取当前候选

SQLite可能存着旧版本，但向量排序只遍历本轮扫描形成的candidates。缓存只为这些候选查向量，不拿历史表全量搜索。因此文件修改会产生新版本/分块ID，文件删除后即使向量仍在历史缓存，也不会作为当前材料被召回。

缓存表主键是`(provider, chunk_id)`。chunk_id已绑定文档版本和原文位置；provider identity再区分模型与格式。换模型命名空间不能复用旧向量，修改文档也不能拿旧版本向量替代新块。查询向量每轮重新生成，没有实现查询缓存。

如果上游在同一模型名下改变权重，当前identity无法自动感知；需要运维更新缓存命名空间或清理重建。没有声称已锁定上游模型权重版本。

### 6.3 固定预算与排序含义

当前最多前128块，每批最多32项，查询单独1次，所以非空语料冷启动最多5次embedding HTTP调用。每条原文仍是最多160字；长问题只取前2000字。原有200条目、40文件、总262144字节、单文件32768字节扫描预算继续有效。分块预算和查询裁剪会使complete=False。

缓存全部命中时仍需1次查询向量请求；不是“命中缓存就完全不调用云端”。没有候选原文时不调用云端。前128块按现有扫描顺序选择，不保证相关内容已经在这128块内，限制会明确记录。每次HTTP超时10秒，最多5次串行请求；没有另实现跨5次请求的总截止时间，最坏等待会累积，后续可根据实际规模改善。

满足阈值的候选按分数降序，同分按路径、行号、行内位置排序，最终Provider最多返回5条。默认0.45是起步经验值，本轮没有根据试验结果事后调阈值或宣称全场景可靠。

## 7. 异常降级与写入边界

`retrieval.py`向量阶段使用SQLite保存点：

```python
db.execute("SAVEPOINT vector_cache")
candidates, diagnostics = vector_rank(
    db, candidates, request.query, self.embedding, self.min_score
)
db.execute("RELEASE vector_cache")
```

EmbeddingError时回滚并释放该保存点，只保留已读取的文档版本记录，重新用本轮候选的关键词分数排序。程序返回`embedding.degraded=True`、稳定error_code和method=关键词；页面明确显示“向量服务失败，已明确退回关键词检索”。已有旧缓存不用于冒充当前向量请求成功。

若第一批成功、第二批失败，不提交本轮新向量缓存；不让部分结果被误当作完整向量轮。当前实现所有新批次校验完成才写缓存，保存点还保护数据库阶段的边界。数据库自身错误则返回整体FAILED，不把存储失败伪装成正常关键词结果。

这里写入的是资料版本/缓存，仍不是正式计划。候选冻结到web_turn后，三个角色读取同一原文；source目录、引用校验与用户确认事务继续使用原机制。确认后的计划正文保留当时选中资料的路径、版本和原文；embedding分数不会自动批准计划或证明任务完成。

## 8. 固定对照、真实生成与人工内容复核

最小真实探针：[029-embedding-probe.json](validation/029-embedding-probe.json)，1次请求、2条1024维向量，23个已知tokens。仅证明端点接通和返回形态，不评估召回质量。

固定语料为三份合成资料：计划导出、SQLite恢复、休息建议。固定四例一次执行，不重试，见[全部原始结果](validation/029-embedding-first.json)。

| 问题 | 关键词 | BGE-M3实际结果 | 判断 |
| --- | --- | --- | --- |
| 计划导出 | guide.md | guide.md，0.735685 | 两者本例均命中 |
| 输出清单算办妥了吗 | 无结果 | guide.md 0.656501；rest.md 0.464009 | 换词能召回目标，但低排名出现休息资料误召回 |
| 量子引力的实验测量结果 | 无结果 | 无结果 | 本例无关文本未过阈值，不代表任意无答案问题都能拒绝 |
| SQLite restart recovery | database.md | database.md，0.753314 | 两者本例均命中 |

这不是独立盲测集，而是开发验收样本。关键词在纯同义改写问题无命中、向量命中目标，是本例实际收益；不能推出总体准确率提升。原始误召回保留，未通过重试、删结果或调阈值掩盖。

同一脚本进一步使用真实WebService、真实Harness会话存储、真实BGE-M3和真实千问生成，综合状态COMPLETED。模型选择了guide.md原文及当前用户请求，候选两步分别核对文件和实际产物，没有把导出当成完成；未知项列出具体清单和验收标准。检查文件大小等细节是模型新增建议，不应说成原文明确规定。

确认保存仅发生在独立合成测试库：生成前没有正式计划，测试显式确认后保存v1；修改guide.md后，新检索读取新版，重建WebService对象读回的旧计划正文和旧快照不变。这是服务对象重建，不冒充该脚本做了真实进程重启。新旧版本、原文、缓存信息都在原始结果中。

对照脚本自己的Provider共7次embedding调用、125个已知tokens；WebService另用独立Provider做查询，首版结果尚未记录其逐次embedding用量，不能把125称为整轮全部用量。真实千问共4次生成，输入10562、输出779、合计11341 tokens。最小探针、浏览器调用和生成用量分开记录；没有核实平台单价，不计算金额，也不声称免费。

后续补充的诊断将每轮embedding.calls和known_tokens写入快照；脚本也补充源码指纹功能。首次原始对照报告没有指纹，不伪造为首跑已有。可通过`scripts/evaluate_embedding.py --output <新路径> --data-dir <新目录>`复现，需预先设置两类环境变量；输出和目录均拒绝覆盖。脚本包含真实生成及合成计划确认保存，会产生平台用量。

## 9. 浏览器、失败分支与完整回归

[浏览器记录](validation/029-browser.json)、[向量来源截图](validation/029-embedding.png)、[降级截图](validation/029-embedding-fallback.png)：真实Edge、HTTP、SQLite和云端embedding；生成与checkpoint是明确脚本桩。页面用纯同义提问召回原文、显示相似片段与向量方法、确认保存1份计划，刷新恢复保存状态。

实际停止并重启验收服务，刻意不提供SILICONFLOW_API_KEY：旧回合保留向量方法和原文，新的字面问题显示关键词降级，error_code=MISSING_API_KEY、网络调用0。新候选未确认，因此数据库仍只有1份正式计划。这证明已启用不等于已成功调用，用户能看到实际方法。

两张截图已查看，没有JS错误，只有既有密码框不在form提示。首次标签刷新检查因旧JS缓存超时，绕过旧缓存后成功，没有重跑生成；实际更新页面时可强制刷新。首次演示服务runpy搜索路径错误及凭据文件格式变化引起的请求前拒绝都在[尝试记录](validation/029-attempts.md)保留。

完整回归从408 passed开始，补充预算和缓存回滚后410 passed；最终响应结构增强后的完整回归仍为**410 passed，37.79秒，3条既有第三方弃用警告**，见[最终日志](validation/029-regression.txt)。命令：`.venv-harness/Scripts/python.exe -m pytest -q --basetemp .magi/test-embedding-029-complete`。改动文件Ruff检查与格式检查、JS语法、PowerShell启动脚本语法、git diff检查通过。

新增13项向量测试覆盖乱序响应重排、鉴权与限流、超时、坏维度/NaN/零向量/重复index、缺凭据、无字面重合仍进入向量候选、缓存命中、文件修改/删除、Provider隔离、128块和2000字预算、部分失败回滚。离线向量为固定假向量，证明程序机制，不证明语义质量。

## 10. 当前限制与下一步决定依据

- 本轮是小规模稠密向量检索，没有关键词/向量融合、重排、SQLite FTS5/BM25、持久向量索引服务或多账号隔离。
- 原有按行160字分块和边界漏召回限制仍存在；短标题可能因相似度过阈值进入候选。阈值需要更多独立样本校准，不能用本轮四题决定生产可靠性。
- 缓存只追加，缺少容量治理；网络期间持有索引写事务，适用于当前本机单用户，不声称适用于多实例并发。
- 有界扫描与前128块选择不是全库语义召回。complete只能在声明预算范围内理解，无结果不能推导资料没有答案。
- 模型结构通过、相似度高、原文引用合法、用户确认、计划实际执行完成仍是不同事实。

本轮未新增question.md条目：保留真实效果和失败边界，不将少量合成样本包装为普遍质量提升。完成本轮后等待用户阅读与复述，不继续自动扩展重排或知识库平台。

## 11. 建议阅读顺序和复述问题

先读本总结业务链路与参数表 → embedding.py的embed响应校验 → normalize和vector_rank → retrieval.py保存点/降级 → WebService的配置接入 → test_embedding.py → 固定真实报告与截图。优先理解每一步输入输出，再看细节。

1. embedding和三人格生成分别做什么，分别需要哪个平台密钥？
2. 为什么文件内容修改后不能直接复用旧分块向量？为什么缓存还要区分Provider？
3. 所有原文向量都命中缓存后，为什么仍有一次云端请求？
4. 余弦分数0.65为什么不表示65%的正确率？休息资料误召回说明了什么？
5. 第二批向量失败时，为什么要回滚缓存但仍保留已读取的文档版本？页面应该显示什么？
6. 写入向量缓存、冻结候选、用户确认保存正式计划、执行任务完成，分别发生在什么边界？
