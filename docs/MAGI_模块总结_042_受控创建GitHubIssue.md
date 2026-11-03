# 模块042 受控创建 GitHub Issue

## 从一次待核对的超时现象开始

你在调试后台任务时看到「页面仍显示执行中」，希望先整理成 Issue，稍后复现。日常聊天勾选「启用 GitHub Issue 草稿」，说明观察、缺少的证据和下一步。Agent 可以调用工具把草稿保存下来。回复下方展开「GitHub Issue · 待确认」，你能看到目标仓库、标题和实际发送正文。核对无误后点击「批准并在 … 创建 Issue」。如果内容有误，可以拒绝草稿，再发消息要求重写。

选 GitHub 是因为它能承接 MAGI 自己的开发反馈。服务固定目标仓库，草稿支持一项具体动作。每轮最多一份草稿。后续若想让助手说明结果，保持开关开启并询问「查一下刚才草稿的实际回执」。有回执可用时，Agent 可引用编号和链接。每轮开关是调用工具的授权，点击批准才是写入的授权。

启用时由本机服务进程提供 `MAGI_GITHUB_ISSUES_REPO`（格式 `owner/repo`）和 `MAGI_GITHUB_ISSUES_TOKEN`。推荐为单个目标仓库授予 Issues 写权限的细粒度凭据。配置目标之前，工具不可用。审批页面显示的仓库可能是公开的，点击前应核对正文和目标。GitHub REST 承接创建和按编号读取，MAGI 负责草稿、批准、限制重复发送和持久回执。[GitHub 官方接口](https://docs.github.com/en/rest/issues/issues#create-an-issue)

## Agent 如何提出动作

`WebService.turn` 在已登记的日常轮次中，根据 `issue_enabled` 注入 `IssueActions.tools(turn_id)`。工具只提供两个动作。草稿工具接收标题和正文。回执工具接收先前返回的动作 ID，只读取本机已有状态。仓库、令牌、发送和审批由业务服务管理，模型没有批准接口。三人格请求不能使用此开关。背景任务沿用同一个聊天入口，队列可以执行生成草稿，发布仍需要网页批准。

`IssueActions.draft` 只在当前用户的运行中轮次创建草稿。重复传同一正文复用记录，改变同轮草稿要求新发消息。SQLite 第 12 版迁移新增 `issue_actions`，保存仓库、标题、正文、背景版本和摘要哈希。生成后模型得到的是 DRAFT 和下一步提示。即使聊天最终回复失败，已落库草稿仍可在历史中查看。用户可判断要拒绝还是批准。

| 标识 | 在何时产生 | 怎么使用 |
| --- | --- | --- |
| 网页轮次 ID | 聊天开始 | 把草稿挂到实际聊天历史 |
| 动作 ID | 草稿保存时 | 标识草稿与回执，作为正文核对标记的一部分 |
| 内容哈希 | 草稿保存时 | 核对用户提交的批准是否针对看到的那份草稿 |
| Issue 编号 | 外部平台确认创建时 | 定位目标仓库的实际 Issue |

实际发送正文的末尾有服务生成的 HTML 注释，形如 `<!-- magi-action:动作ID -->`。网页预览展示它。这个标记让用户能从 GitHub Issue 原始正文找回对应草稿，模型不能在正文里另造一个同名标记。每个 Issue 编号只在对应仓库内有意义。

## 批准后怎样防止重复发送

`src/magi/web/issues.py` 的 `IssueActions.decide(action_id, command)` 接收网页确认的动作 ID、决定和原内容哈希。它检查身份、哈希、当前状态。批准待审草稿时还检查背景版本和目标仓库。这里摘录同一个写事务末尾的真实代码。

```python
token = self.token(action)
db.execute(
    "UPDATE issue_actions SET status='SENDING',approved_at=? WHERE id=?",
    (now(), action_id),
)
```

令牌只从环境读取。先持久化 SENDING 和批准时间，出事务后才发送网络请求。另一个点击如果在此时到达，只能读到 SENDING，直接返回原状态。程序用同样的规则处理 UNKNOWN 和 VERIFIED，已经开始过发送的草稿无法借再次批准触发第二次 POST。拒绝只对 DRAFT 生效，状态改成 REJECTED，外部请求为零。网络等待在 SQLite 事务外。

HTTPX 固定访问 `api.github.com`，关闭自动重定向，单次请求有超时，不设置重试。创建响应必须是 201，且正文、标题、编号和仓库都能与冻结草稿相符，才能保存 VERIFIED。返回链接由固定仓库与编号构造，应用不会直接使用外部响应提供的任意 URL。

## 结果不明时怎样核对

如果请求超时，GitHub 可能已经创建 Issue，本地却没拿到回执。状态转为 UNKNOWN。进程退出导致 SENDING 遗留时，启动恢复也转成 UNKNOWN。这个状态不会自动触发写入，连远端拒绝或无法解析回执也按保守方式留待核对。

在目标仓库找到可能对应的 Issue 后，在页面填编号。下面是 `IssueActions.reconcile` 调用远端的关键部分：

```python
try:
    result = self.github.request("GET", action["repo"], token, number=number)
    self.checked_number(action, result, number)
except Exception:
    raise WebError(
        "ISSUE_NOT_VERIFIED", "未核对到匹配的 Issue，状态仍未知", 409
    ) from None
return self.verify(action_id, number, "UNKNOWN")
```

`checked_number` 拒绝仓库、编号、标题或正文不一致的记录，也拒绝 Pull Request。匹配后 `verify` 在事务内保存编号、核对时间和 VERIFIED。外部 Issue 已被编辑、被删除或者用户填错编号时，仍然无法核对。查不到不证明远端没有创建，程序也不会把 UNKNOWN 改回 DRAFT。轮次的 COMPLETED 说明模型生成已完成，动作 VERIFIED 才说明收到了与这份内容匹配的外部回执。

## 验证结果

完整回归 469 项通过，3 条已有依赖弃用警告。专项覆盖真实聊天循环的工具调用、审批摘要冲突、仓库和背景变化、用户拒绝、恢复遗留 SENDING、重复批准、远端成功和远端已创建但回执超时。超时用例先填无关 Issue 编号，再用正确编号核对，MockTransport 最终记录一次 POST 和两次 GET。模拟 SENDING 恢复是在数据库写入该状态后重新构造服务，未实际中途强杀发送进程。[回归输出](validation/042-regression.txt)

```powershell
.\.venv-harness\Scripts\python.exe -m pytest -q --basetemp .magi/test-042
node --check src/magi/web/app.js
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluate_issue_actions.py --data .magi/acceptance-042-a --output docs/validation/042-cloud-a.json
```

最后一条命令用真实千问生成一次草稿并询问一次结果。模型分别调用了草稿与回执工具，批准前外部写入次数为零。验收脚本代替人工批准，GitHub 传输全部由本机替身处理，得到模拟 Issue #17。草稿保留「原因尚未查明」和后续复现的要求，却新增了「后台任务已应超时」。原输入仅确认页面显示执行中，这句话缺少依据。程序的回执核对验证内容一致，无法证明草稿事实。原样保留这一缺口。[真实千问与模拟外部动作](validation/042-cloud-a.json)

真实 Edge 通过 `npx --yes --package @playwright/cli playwright-cli -s=magi042 open http://127.0.0.1:8897 --browser msedge` 连接隔离服务，使用脚本模型和模拟 GitHub。查看草稿后点批准，观察到结果待核对。输入编号后看到 Issue #12，刷新后仍能读取回执。模拟远端仅收到一次 POST 与一次 GET。浏览器试验无真实 GitHub 写入。尚未测试真实仓库权限、外部服务限流或多人部署。[浏览器落库核对](validation/042-browser.json) · [回执截图](validation/042-verified.png)

## 面试关联与复述

- **工具与权限。** 模型负责生成待审内容，业务服务负责权限与实际调用。追问「提示词能否防发布」时，可以对照模型注册的两个工具，以及独立于模型工具的认证决定接口。
- **状态机与副作用。** SQLite 短事务可以原子写批准和 SENDING，远端 POST 在事务外。若服务在两者之间退出，无法可靠区分请求是否已经发出，所以保留 UNKNOWN。外部幂等键若由上游支持，可以减少这种不确定性。
- **证据和质量。** 内容哈希保护批准目标，远端正文核对保护回执匹配。真实样本的事实扩写说明二者都不评估正文语义。用户审核和后续内容评测各有责任。

建议先读真实千问样本，再对照批准与核对代码。复述题是：**用户点批准后请求超时，重开服务时为什么不能再次 POST？填 Issue 编号进行核对，程序具体比较哪些内容？**
