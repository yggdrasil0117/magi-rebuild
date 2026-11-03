# 模块039 长会话续聊的容量与恢复

## 从一次备考聊天开始

第一轮告诉 MAGI「只用 Python 3.12，本周只有 4 小时，协程取消实验还没验证」。随后聊过基础复习，第四轮想让助手复述前面约束。原来的网页循环只在历史正文达到阈值时尝试压缩，但发送给模型的请求还包含提示词、工具定义、消息结构和输出预留。模块038的 8192 字节用例中，容量守卫先拒绝了请求，摘要没来得及运行。

本轮让网页自己的聊天循环用同一套估算决定何时压缩、压缩结果是否可用。原始消息仍由业务历史保存。你正常发送消息即可，摘要发生在模型调用之前，不需要另外确认。开启联网或提供更多工具会占用更多预算。

## 预算与窗口怎么决定

`src/magi/harness/chat_loop.py` 的 `ChatLoop.model_input(history)` 接收旧消息和正在处理的完整一轮，返回本次发给模型的消息列表。系统提示里已经包含本轮选中的记忆。下面是它用来计算预算的实际代码。

```python
def input_cost(self, messages):
    """摘要触发与硬限制共用估算，计入工具规格和输出预留。"""
    payload = {
        "messages": [message.model_dump(mode="json") for message in messages],
        "tools": self.specs,
    }
    size = len(json.dumps(payload, ensure_ascii=False).encode())
    return size + self.settings.num_predict + 512 + 128 * len(messages)
```

`messages` 是带系统提示的完整候选请求，`self.specs` 是本轮工具规格，`num_predict` 沿用输出预留。返回值是一个保守字节估算，包含固定和逐消息预留。它用于本项目的准入控制，实际模型 token 由模型提供方决定。

估算不超过 85% 时直接使用原文。最多三轮且仍放得下时也使用原文，避免只因固定开销偏大而消耗一次摘要请求。超过门槛且存在旧轮次时，程序尝试保留最多三轮原文。若近期窗口加预留仍太大，就缩短窗口，最低保留当前轮。实际摘要大小可能超过预留，所以最终仍要检查完整请求。

当前问题与本轮已经发生的工具交互一起保留。系统不会拿掉一个工具结果来让请求勉强通过。如果只留下系统提示、工具定义和当前轮也超限，程序直接返回 `CURRENT_TURN_TOO_LARGE`，不调用摘要模型。若当前轮本身放得下，但加入旧历史和可用摘要仍超限，就返回 `CHAT_HISTORY_TOO_LARGE`。网页接口返回 422，聊天历史也保存可读原因。失败轮次按已有规则标为 FAILED，发送新消息才开始新运行。

## 分批保存与恢复

`ConversationSummary.compact(messages, keep_turns, max_batches)` 由网页循环调用。前一个参数是原始消息，`keep_turns` 指希望保留的近期整轮数，`max_batches` 是本次运行最多允许尝试的摘要调用数。网页最多三次，工具执行后的下一次模型请求共享此预算。摘要调用预算与正文模型调用预算分别计数。

`next_batch` 从上次覆盖位置开始，尽量选入更多完整旧轮次。每批包含上次成功摘要和新增原文，JSON 正文最多占配置容量的一半。连一轮都放不下时停止，避免把一个工具请求和它的结果拆开。摘要使用现有生成模型。程序拒绝空白、截断、带工具调用或超过 2400 字节的输出。通过这些检查只说明格式和大小可用，语义仍需验收。

例如最早两轮太大，不能放进一批。第一批成功覆盖两条消息，第二批超时。下面是 `src/magi/harness/summary.py` 中每批成功后的实际保存边界。

```python
with db:
    db.execute(
        "INSERT OR REPLACE INTO summaries VALUES (?,?,?,?)",
        (
            self.thread_id,
            end,
            self.digest(messages[:end]),
            candidate,
        ),
    )
covered, body = end, candidate
self.events.emit("summary_saved", covered=covered)
```

`thread_id` 属于当前背景版本的会话，和某次网页请求的 `turn_id` 不同。`end` 是本批覆盖后的消息数，`candidate` 是模型生成且校验通过的摘要。SQLite 先提交文本、覆盖位置和前缀哈希，然后程序才使用新状态。第二批超时时，第一批仍有效，尚未覆盖的原文仍在请求候选中。最终容量守卫决定是否继续。

重新开启服务后，根据同一会话身份读取覆盖位置，并校验前缀哈希。仍匹配时，下一批从未覆盖的原文开始，连同上次成功摘要一起交给模型。摘要提交属于独立的缓存写入，网页聊天完成仍走用户改造后的整轮消息事务。一次聊天失败可以留下较早轮次的有效摘要。摘要不会修改历史、确认长期记忆或写正式计划。

## 验证与发现

离线验收使用网页默认入口、脚本模型和真实 SQLite。8192 字节的四轮用例成功续聊，最后发给模型的是摘要和当前问题，第一轮原文仍能从网页历史读取。这个预算下近期窗口缩小到一轮。首次实现过早压缩仍放得下的短对话，破坏了现有工具历史用例的预期，随后加入短对话保留规则。最初的专项脚本还假设只调用一次摘要，后来按实际请求类型区分摘要与回复。

另一个用例模拟第二批超时，确认 SQLite 的覆盖位置仍为 2。重新打开摘要库后，新调用从第二批开始并推进至 8，输入与超时批次相同。当前消息单独超限的用例确认没有任何模型调用，历史里保留对应原因。

```powershell
.\.venv-harness\Scripts\python.exe -m pytest -q --basetemp .magi/test-039
.\.venv-harness\Scripts\python.exe -m pytest -q tests/test_native_chat_memory.py tests/test_memory_context_035.py tests/test_web_chat_loop.py --tb=short
```

完整回归 459 项通过，3 条依赖弃用警告。完整回归之后对摘要提示做了内容修正，再运行同一组专项用例，13 项通过。Ruff 和 Git 差异检查通过。[完整回归记录](validation/039-regression.txt)

真实调用使用 `scripts/evaluate_chat_capacity.py` 与 8192 字节配置，数据和凭据与用户的正式会话隔离。首次生成四轮，随后退出并在新 Python 进程继续第五轮。首次试验的四轮及恢复后的第五轮都完成，恢复前读到四条历史。真实内容未通过三项约束检查。摘要保留 Python 3.12 和实验未验证，却漏掉本周 4 小时。随后将摘要提示改为明确保留历史中的版本、时限、数量和单位，同时保留它们的历史身份。原结果完整保留。[首次生成](validation/039-cloud-a.json) · [首次恢复](validation/039-cloud-resume.json)

复测使用独立目录和同一组问题。第四轮与恢复后的第五轮都保留了 Python 3.12、本周 4 小时和协程取消实验未验证三项信息。新进程在第五轮发送前读到四条历史，随后摘要覆盖位置从 6 推进到 8。[复测生成](validation/039-cloud-b.json) · [复测恢复](validation/039-cloud-b-resume.json)

首次试验使用 `.magi/acceptance-039-a` 和对应的两份 A 报告。复测在环境变量提供千问凭据后，执行：

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluate_chat_capacity.py --data .magi/acceptance-039-b --output docs/validation/039-cloud-b.json
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluate_chat_capacity.py --data .magi/acceptance-039-b --output docs/validation/039-cloud-b-resume.json --resume
```

本轮未验收浏览器操作，也未在网络调用中强杀进程。真实恢复检查是进程正常退出后重新打开原数据库。大到单轮无法进入摘要输入的历史会报告容量不足。摘要和回复内容都有模型误差，有限样本只能说明这些案例发生了什么。

## 面试知识与复述

- 容量估算。为什么需要统一触发和硬限制？两个口径会造成摘要尚未启动而请求先被拒绝。项目在 `input_cost` 汇总预留，在 `model_input` 复用。可追问字节估算和真实 token 的差异，回答时应把项目守卫与服务端实际计费分开。
- 事务与进度。为什么每批完成就提交？第二批失败时可以沿用第一批结果，减少重做。SQLite 提供原子提交，业务代码选择提交哪些字段。读回时校验前缀，避免给已经变化的历史错误套上旧摘要。
- 有界执行。为什么一次请求最多尝试三批？压缩本身需要模型调用，不能让补救消耗无上限。若一批失败或容量仍不足，保存进度后退出，由新请求决定是否继续。

建议先看 8192 字节用例，再对照 `model_input` 和摘要事务。复述时可以先答：第一批覆盖两条消息，第二批超时，下一次摘要模型会收到什么？如果只剩当前轮仍超限，为什么一次摘要都不应发？
