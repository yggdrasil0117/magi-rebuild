# 模块032：计划执行跟踪与复盘完整模块

日期：2026-11-02。用户希望按大业务模块推进，本轮交付页面、API、数据迁移、执行记录、修订接入、保存边界、真实生成、浏览器与实际进程重启验证，不再拆成连续的局部提示优化。

## 1. 业务目标与使用方法

此前可以生成计划、确认保存、创建工作单、通过聊天反馈修订，但无法直接查看哪些计划在做、哪里卡住、之前记录过什么结果。本轮增加计划版本级的执行跟踪。

正常启动后打开“我的计划”，选择计划，在“执行跟踪”选择未开始/进行中/遇到阻塞/已完成（用户记录），填写实际进展、问题或结果，再点击“确认记录执行情况”。列表显示最新版状态；详情可切换历史计划版本，看对应记录。

这些操作不需要调用模型。若要调整安排，再点击“对最新版提交反馈”，填写本轮目标和约束并发送。三个角色会接收该版本最近3条执行记录，形成修订候选；仍需用户确认才保存新计划版本。新版本默认未开始，旧版本历史不改写。

完成状态是用户自报，不是系统检查了所有产物，也不是自动执行任务。默认未开始表示此版本尚未登记进展，不证明用户现实中一定没有做过。创建工作单、模型COMPLETED和用户记录COMPLETED是不同对象的状态。

本轮没有实现后台自动执行、提醒调度、逐步骤打勾或外部系统完成核验。它交付的是可用的“保存计划 → 记录执行 → 发现阻塞 → 修订 → 新版继续跟踪”业务闭环。

## 2. 请求经过哪些模块

```text
我的计划 → 选择具体版本
   ↓ GET execution
显示当前状态、最近历史
   ↓ 用户填写状态/说明并点击确认
POST execution（请求ID + 计划版本 + 预期记录revision）
   ↓ 身份认证、串行门、字段检查
PlanExecutions.record → SQLite事务追加一条事件 → 返回提交回执
   ↓
页面刷新状态和列表
   ↓ 用户另行选择反馈修订
WebService冻结旧计划 + 对应版本执行记录摘要
   ↓ 三人格生成、程序校验
用户确认修订 → 检查计划版本与执行revision仍匹配 → 正式保存v2
```

新增personal/executions.py负责执行记录；store.py迁移schema8；web/app.py和schemas.py提供受认证API；app.js/index.html提供最小交互；WebService在修订快照中加入执行上下文；council/generation.py让真实模型适配器保留这部分输入。实际计划保存仍复用PlanService，不重写原有事务机制。

## 3. 数据模型与ID说明

新增表plan_execution_events，不修改plan_revisions的旧正文。它是业务日志式追加表，不是引入事件总线或完整Event Sourcing框架。

| 字段 | 标识对象、来源与用途 |
| --- | --- |
| plan_id + plan_version | 用户查看的某份已保存计划版本；外键指向plan_revisions，候选不能直接记录执行 |
| event_id | 后端为一次成功追加生成的执行事件身份；重试返回同一事件 |
| request_id | 客户端在一次用户提交时生成，断线重试保持不变；同owner下唯一 |
| revision | 同owner、同计划版本的执行记录序号，从1递增；无记录时API返回0 |
| request_digest | 规范化业务参数的摘要，核对同请求是否偷偷换了内容；不是来源可信证明 |
| owner | 后端配置的身份作用域，客户端不能自报；本项目仍是本机单用户，不声称全系统多租户隔离 |
| status / note | 用户选择的状态和进展/阻塞/结果说明；说明必填，最多2000字 |
| created_at | 后端UTC记录时间，页面转本地显示，不是任务实际发生时间的独立证明 |

计划v2和执行revision2不是同一概念。一份计划v1可以有3条执行记录，此时计划版本仍是v1，执行revision为3；修订并确认后产生计划v2，但v2没有执行记录时revision重新从0开始。

数据库用UNIQUE(owner,request_id)防重复请求，UNIQUE(owner,plan_id,plan_version,revision)防同版本序号冲突。execution_history索引支持倒序读取；列表用一次查询读取各当前计划版本的最新状态，避免每份计划都加载整段历史。

## 4. API契约与确认含义

GET `/v1/plans/{plan_id}/execution?version=1`读取此版本状态与最近50条记录，含revision、history_truncated、provenance。更早记录仍在库中，页面当前没有分页查看更早历史。

POST同一路径示例：

```json
{
  "request_id":"客户端为这次提交生成的唯一值",
  "version":1,
  "expected_revision":0,
  "status":"BLOCKED",
  "note":"缺少测试账号，无法复现登录问题。",
  "confirmed":true
}
```

version是计划版本，expected_revision是用户读到的执行记录序号；两者分别防止对象混淆和旧页面覆盖。Pydantic拒绝额外字段、非法状态、非严格整数/布尔值，业务层再次校验，避免其他调用者绕过HTTP检查。

confirmed只表达本次请求明确提交用户记录，不是证明真实人类动作的密码学机制。平台模型工具没有新增执行记录写工具，普通生成回复不能自动调用该接口。后端仍依靠本机认证与显式业务接口；用户自报内容不是自动核验事实。

未确认/空说明返回422，不存在版本404，请求ID改正文或revision过期返回409；生成或保存期间串行门忙返回429；存储异常返回稳定503，提示核对历史或用原请求重试，不暴露SQLite异常正文。

## 5. 核心代码一：事务追加、幂等与并发

文件：`src/magi/personal/executions.py`，实际record函数：

```python
    def record(
        self,
        plan_id,
        version,
        *,
        request_id,
        expected_revision,
        status,
        note,
        confirmed,
    ):
        """明确点击记录后事务追加；同请求重放，过期revision拒绝，不修改计划版本。

        note为用户进展/阻塞/结果说明，完成也只代表用户自报。允许追加纠正或重新开始，旧记录不删除。
        """
        if (
            confirmed is not True
            or not isinstance(status, str)
            or status not in STATUSES
        ):
            raise PersonalError(
                ErrorCode.INVALID_INPUT, "必须明确确认并选择有效执行状态"
            )
        if type(expected_revision) is not int or expected_revision < 0:
            raise PersonalError(ErrorCode.INVALID_INPUT, "执行记录版本必须为非负整数")
        request_id = _text(request_id, "request_id", 120)
        note = _text(note, "进展或结果说明", 2000)
        data = dict(
            plan_id=plan_id,
            plan_version=version,
            expected_revision=expected_revision,
            status=status,
            note=note,
        )
        fingerprint = _digest(data)
        with self.store.transaction() as db:
            self._require_plan(db, plan_id, version)
            replay = db.execute(
                "SELECT * FROM plan_execution_events WHERE owner=? AND request_id=?",
                (self.owner, request_id),
            ).fetchone()
            if replay:
                if replay["request_digest"] != fingerprint:
                    raise PersonalError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "此请求编号已用于不同执行记录"
                    )
                return dict(
                    event_id=replay["event_id"],
                    revision=replay["revision"],
                    duplicate=True,
                    committed=True,
                )
            latest = db.execute(
                "SELECT COALESCE(MAX(revision),0) FROM plan_execution_events WHERE owner=? AND plan_id=? AND plan_version=?",
                (self.owner, plan_id, version),
            ).fetchone()[0]
            if latest != expected_revision:
                raise PersonalError(
                    ErrorCode.VERSION_CONFLICT, "执行记录已更新，请刷新后核对再提交"
                )
            event_id = uuid4().hex
            db.execute(
                "INSERT INTO plan_execution_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    self.owner,
                    request_id,
                    fingerprint,
                    plan_id,
                    version,
                    latest + 1,
                    status,
                    note,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return dict(
                event_id=event_id, revision=latest + 1, duplicate=False, committed=True
            )
```

### 参数、调用与返回

plan_id和version来自当前页面所选已保存版本。request_id、expected_revision、status、note、confirmed来自ExecutionRecord请求；self.owner来自WebService初始化。返回包含event_id、revision、committed和duplicate的回执，不返回“任务已由系统验证完成”。

调用顺序是：字段检查 → BEGIN IMMEDIATE事务 → 核对计划版本存在 → 查请求是否已提交 → 检查摘要 → 读取当前执行revision → 拒绝过期写入 → 插入事件 → 提交事务。这里复用PersonalStore.transaction和PlanService已有的文本检查/参数摘要辅助，不额外实现通用事务引擎。

### 为什么先重放，再检查revision

第一次提交revision0，后端已经写出事件1，但网络丢了响应。客户端仍拿着revision0重试。如果先检查revision就会错把成功请求当旧写入；因此先查request_id，参数相同就返回原event_id和duplicate=True。之后其他记录已追加，也不影响原回执恢复。

同一request_id换note不是重试，而是冲突，必须拒绝。若另一个窗口以新request_id但旧revision0提交，则返回VERSION_CONFLICT，让用户刷新并核对，不静默替换最新状态。

事务使用BEGIN IMMEDIATE，读取最大revision和插入在同一写事务内完成；两线程测试只有一个成功，另一个明确冲突。这是当前SQLite本机业务场景的保证，不把它称为跨多实例分布式事务或吞吐量成绩。

### 为什么追加而不是覆盖一条status

用户从进行中变阻塞、再标记完成，或发现问题重新开始，都是独立观察。每次必填说明，旧记录保留；允许重新开始或纠正，不强制先点击开始才能记录已完成的实际工作。

“状态转换自由”不是让模型决定执行。状态只能通过显式记录请求写入；程序不从工作单文件存在、候选生成成功或聊天中的“完成了”自动推断。

## 6. 核心代码二：执行反馈真正进入修订

文件：`src/magi/personal/executions.py`：

```python
    def context(self, plan_id, version):
        """取最近3条、每条最多300字给修订模型；保留裁剪标记，不扩充证据白名单。"""
        view = self.view(plan_id, version, limit=3)
        shortened = any(len(event["note"]) > 300 for event in view["events"])
        for event in view["events"]:
            event["note"] = event["note"][:300]
        view["notes_truncated"] = shortened
        return view
```

plan_id、version来自已选定且读取时为最新版的旧计划。返回有界用户记录摘要：最近3条，每条说明前300字；分别标记历史和说明裁剪。它读取的是本轮最新执行记录，不是模型记忆。

原始2000字说明保存在数据库，裁剪只发生在模型输入。view返回的新字典再裁剪，不修改原始事件。记录不自动成为有效记忆，不增加context_epoch，也不登记成外部独立资料证据。

WebService.turn在原有old_plan快照上加入：

```python
snapshot["old_plan"]["execution"] = self.executions.context(old.id, old.version)
```

真实模型路径model_snapshot保留old_plan.execution，因此不是“离线假模型看得到、真实调用却丢掉字段”。三个角色读取同一份冻结输入；当前question优先，执行记录是用户自报数据，不能作为新的程序指令或执行授权。

生成后记录再变化，不会回头改写这份快照。若尚未保存的修订候选所依据execution.revision已过期，WebService.confirm拒绝确认，提示重新生成。已提交请求优先保留幂等重放，不因用户后来又记录进展而撤销真实保存回执。旧历史候选没有execution字段时保持兼容。

该确认检查与执行写入共享现有服务串行门，适用于当前单worker应用；独立进程同时绕过应用直接写库不在本轮支持范围。已有ApplicationLock继续限制本机服务多实例启动。

## 7. 页面行为与不确定提交结果

计划列表展示最新版的执行状态。详情当前查看版本有独立表单和历史；切换v1/v2不会把状态串在一起，迟到请求只在viewTicket仍对应当前选择时更新页面。

点击记录时冻结请求ID、计划版本、revision和正文。响应不确定时锁住原正文，显示“重试原执行记录”；重试保持相同ID和参数。用户可以放弃重试，但页面明确提示“放弃不撤销可能已经保存的记录”，并重新读取历史核对。

收到成功回执后，记录已确定提交；如果后续刷新失败，不再假称未保存，而提示重新打开核对。revision冲突会读取新状态、保留原说明供用户核对后再次提交，而不是自动把旧写入强行变成新revision。未确认结果的执行记录要先处理，再准备修订，减少上下文混淆。

内容使用textContent显示，执行说明中的HTML不会被执行。本轮只做最小交互接入，没有视觉重设计。历史只显示最近50条；目前没有附件、自动耗时、逐步骤核验、历史删除/编辑或提醒。

## 8. 验证：真实模型、离线边界、浏览器分开

### 8.1 真实千问闭环

脚本scripts/evaluate_execution.py使用独立合成计划和用户自报记录，不读真实个人计划。先创建测试v1，记录进行中和阻塞，再真实调用一次三人格修订，最多4次模型请求，无重试。embedding明确关闭以隔离执行反馈机制，没有云端向量请求。

[原始结果](validation/032-execution-cloud-first.json)：真实生成COMPLETED，4次调用，输入6222、输出671、合计6893个已知tokens，未核实价格不计算金额。模型读到BLOCKED、缺少账号和30分钟约束，给出三步各10分钟的静态日志核对与缺口记录，不要求必须定位根因。

模型列出日志可访问、静态核对无需额外权限等假设，并把日志不完整列为未知。这次主要方向符合用户要求，但这些假设并没有被系统验证；计划分钟数是估计，不是实际执行计时或自动完成证明。单例不能推导普遍质量。

测试显式确认修订后保存v2，再给旧v1追加COMPLETED自报。重建真实WebService后：v1为COMPLETED、v2为NOT_STARTED，生成时冻结的旧执行状态仍是BLOCKED，旧正文不变。这里是服务对象重建；实际进程重启另见浏览器部分。

### 8.2 程序边界与迁移

新增执行服务、HTTP和修订集成测试覆盖生命周期、重新开始、同请求重放、摘要冲突、旧revision拒绝、两个并发写者、存储触发器拒绝后的回滚与原请求重试、owner记录作用域、plan v1/v2隔离、上下文裁剪不改历史、认证与字段拒绝、忙时429、修订确实接收记录、过期候选拒绝和已保存回执继续重放。

schema7真实结构升级测试保留旧计划正文并初始化空执行记录。旧schema3—6迁移测试继续运行。第一轮409 passed/5 failed，原因是历史测试用最新库模拟旧结构时未删除新增表、以及仍断言schema7；修复测试结构模拟，没有把业务迁移改成忽略重复表来掩盖问题。首轮失败日志保留。

补齐过期修订保存边界后的最终完整回归为**429 passed、44.21秒、3条既有第三方弃用警告**，见[最终日志](validation/032-regression-final.txt)。命令：`.venv-harness/Scripts/python.exe -m pytest -q --basetemp .magi/test-execution-032-complete`。此前428项回归也保留在[中间日志](validation/032-regression.txt)，没有覆盖。Ruff/格式、JS语法及git diff检查通过。未为新模块添加镜像提示词测试，不把离线假向量或脚本模型当真实语言理解成绩。

### 8.3 真实Edge页面与实际重启

[scripts/serve_execution_demo.py](../scripts/serve_execution_demo.py)使用真实HTTP/SQLite，生成和checkpoint是明确脚本桩；本轮另有真实模型脚本，两个验证维度分开。

[浏览器与数据库记录](validation/032-execution-browser.json)验证：记录进行中、记录阻塞、完成自报；在fetch已经收到真实服务响应后模拟前端丢失回执，原请求重试。实际库中最终只有3条不同进展，重试未产生第4条；每条request_id唯一。

随后点击反馈，脚本模型读取冻结执行记录并生成修订候选；确认后计划从v1到v2，v2执行状态为未开始。实际停止并重启服务，再连接同一数据目录，页面能读取v1完成和3条历史，v2仍未开始。重启前后PID及数据库事实保留在证据中。

[阻塞记录截图](validation/032-execution-blocked.png)、[新版未开始截图](validation/032-execution-v2.png)、[重启恢复截图](validation/032-execution-restarted.png)已查看，页面布局可读。首次route.abort故障注入工具报已处理异常，并出现一次HTTP429资源错误；改用fetch返回层模拟后完整通过。首次历史版本getByLabel定位超时，改用已核对的版本选择器完成，未重复生成或提交。这些尝试均保留在[尝试记录](validation/032-attempts.md)，不宣称全过程无失败。

## 9. 面试知识关联：从完整业务模块讲工程机制

以下为本项目可解释的通用后端/Agent追问，不声称某家公司真题或高频面经，也不虚构生产并发和性能比例。

### 9.1 网络超时为什么不能直接认定保存失败

业务场景：用户提交完成记录，服务端提交了事务，响应丢失。知识点是客户端观察与服务端提交不是同一事实。代码证据为record先检查request_id、匹配request_digest后返回原事件，以及页面冻结pending请求。浏览器fetch丢回执后重试，库中事件数未增加，是实际验证。

回答要点：用业务请求ID实现可恢复提交；相同ID不同参数拒绝。不能仅在按钮禁用防重，也不能以“没收到200”推导没写库。这里没有宣称全系统exactly-once：保证的是指定身份/请求ID下这条业务记录幂等，其他外部副作用不在这个事务里。

### 9.2 幂等与乐观并发为什么都需要

幂等处理同一次操作重发；expected_revision处理不同操作基于旧状态写入。只有幂等，两窗口用不同ID仍可能覆盖彼此意图；只有revision，已成功但丢回执的重试会被误判过期。

代码把重放检查放在revision检查前，事务保护读取序号与插入；并发测试一成功一冲突。追问隔离级别时，应说明本项目用SQLite BEGIN IMMEDIATE串行化写事务及唯一约束，不冒充使用了分布式锁、消息队列或线上高并发治理。

### 9.3 为什么计划版本和执行记录revision必须分开

计划版本表示用户确认的不可变安排；执行revision表示观察不断追加。进展变化不应制造一份新的计划正文，而修订计划也不能继承完成状态。外键和独立状态视图保持关系，v1完成/v2未开始的浏览器证据证明这一业务选择。

追问是否Event Sourcing：这里只是追加业务记录、从最新事件取状态，没有实现事件总线、通用投影重建或复杂聚合框架。复用SQLite和已有PlanService，贡献是把状态/版本/幂等边界适配到个人Agent闭环。

### 9.4 Agent“知道进展”为什么不等于自动完成任务

模型读取的是用户自报、有界且冻结的执行上下文，能据此调整建议；记录不是工具执行证据，也不是外部核验。正式计划仍需用户确认，实际执行仍由用户或已有明确批准的工作单动作完成。

追问如何避免旧建议保存：比较生成时执行revision与确认时当前revision，拒绝过期未保存候选；已提交回执仍可重放。上下文不是无限拼接，最近3条/每条300字及裁剪标记是当前预算策略；长历史治理属于后续使用证据驱动的升级。

## 10. 当前限制与交付节奏

本轮核心模块已经完整接入，不再把页面、数据库和实际修订分别拆成后续轮。它支持本机单用户、单worker，执行记录整体绑定计划版本，未实现逐步骤任务树或多账号授权体系。历史更早于50条的记录留在数据库但页面无分页；执行说明和状态均是自报，不验证附件或实际产物。

真实模型样本仅一轮、浏览器生成是桩，不能据此称为生产可靠性认证。此前RAG误召回、提示边界等质量缺口继续保留，没有因本轮完整功能而宣称全部解决。question.md不为这次常规业务模块和成熟机制适配新增条目。

建议阅读顺序：业务操作与ID表 → executions.record事务顺序 → context与model_snapshot → WebService确认前revision检查 → app.js丢回执重试 → 两组测试 → 真实报告和截图。

复述问题：

1. 记录“已完成”和保存计划v2，分别写入什么对象？为什么新版本不能自动继承完成？
2. 服务端可能已保存但客户端没回执时，为什么必须保留同一request_id和正文？
3. request_id幂等与expected_revision分别解决什么问题，为什么先重放再检查revision？
4. 模型读取执行记录后生成候选，用户又追加进展，此时旧候选为何不能直接确认？为什么已保存回执仍可重放？
5. 三人格看到的最近3条记录是什么性质，为什么不能把它说成系统已经核验了任务完成？

本轮结束后停下等待用户阅读和复述；后续按完整业务模块推进，质量问题按影响集中治理。
