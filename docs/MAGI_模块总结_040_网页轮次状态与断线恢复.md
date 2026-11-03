# 模块040 网页轮次状态与断线恢复

## 一次回复慢下来时，页面怎样处理

假设你在备考会话里提问，模型要先处理旧摘要，再访问资料，最后生成回答。发送后，页面一开始只知道浏览器已经发出请求。服务端创建并提交了轮次后，页面才显示「服务端已接收，正在处理」。这个状态对应数据库里的 RUNNING，涵盖摘要、模型、工具和最终提交期间的等待。页面当前只展示轮次状态，具体阶段仍可从后端运行日志排查。

如果服务器保存完回复，但浏览器没收到原 POST 响应，页面用发送前就生成的消息 ID 查询原轮次，再读取聊天历史。若刷新时原请求仍在运行，重新连接同一会话后，页面从历史识别 RUNNING 轮次，并继续查询。原轮次结束后自动加载答案。用户可以从原页面看到保存结果，无需手工再发送一次。

## 为什么用消息 ID 查询

`message_id` 由浏览器在发送前生成，原请求重试沿用。服务端接收后生成业务轮次 ID，也就是 `web_turns.id`。回执可能没到页面，因此只拿轮次 ID 查询会遇到先后依赖。新增的 GET 接口用当前认证身份、会话和消息 ID 找到对应轮次。

`src/magi/web/service.py` 的 `WebService.turn_status(chat, message_id)` 核心代码如下：

```python
with self.store.connect() as db:
    row = db.execute(
        """SELECT id, status, created_at FROM web_turns
           WHERE owner=? AND chat=? AND message=?""",
        (self.owner, chat, message_id),
    ).fetchone()
return {"turn": dict(row) if row is not None else None}
```

`chat` 来自路径，`message_id` 来自查询参数，`owner` 由服务端确定。结果含轮次 ID、状态和创建时间。消息未登记时返回 `{"turn": null}`，页面无法据此断言提交成功。认证由原有 FastAPI 依赖处理。这个查询不会获取生成用的串行门，也不会调用模型、追加历史或确认计划。

## 两种恢复路径

发送期间，页面每隔约 1.5 秒发一次小查询。一次状态查询最多等待 5 秒。原提交完成或报错时结束并行查询。原提交异常后立即再查一次，已查到持久轮次时加载历史。若这次查询也失败，或者还查不到记录，页面保留原请求体。用户再次点击发送时沿用原消息 ID，服务端仍会用请求摘要核对是否是同一次提交。

刷新后连接时，`load` 发现历史最后一轮仍在运行，就调用 `followTurn(chat, message, ticket)`。它先确认页面仍跟踪同一会话和同一批加载，再处理状态结果。核心片段来自 `src/magi/web/app.js`：

```javascript
while (current()) {
  await new Promise(resolve => setTimeout(resolve, 1500));
  if (!current()) return;
  const turn = await readTurnStatus(chat, message);
  if (!current()) return;
  if (!turn || turn.status !== 'RUNNING') {
    await load();
    return;
  }
}
```

`ticket` 是页面内的加载序号，生命周期只覆盖一次查看历史。切换会话或再次加载会使旧查询失效。`load()` 读取业务历史，展示数据库已保存的答案或失败说明。刷新后的状态查询失败时，用户可以重新加载历史继续查看。发送中的 POST 仍由浏览器等待，恢复路径覆盖明确的请求报错及重新加载页面。

原有后端事务负责提交完整聊天消息与完成状态。页面只有看到持久的 COMPLETED 才展示完成结果。服务重启把遗留 RUNNING 标为 INTERRUPTED，刷新可以看到这条记录，执行本身不会自动续跑。三人格候选依然需要用户确认才成为正式计划。

## 验证记录

接口测试用两个并行请求构造真实的状态转换。脚本模型被阻塞期间，GET 已经能读到 RUNNING。释放模型后读到相同轮次 ID 的 COMPLETED。无凭据返回 401，其他用户读不到该轮次，重复原 POST 仍只产生一次模型调用。见 `tests/test_web_turn_status.py`。

真实 Edge 验收使用隔离的本机 FastAPI 服务，脚本模型固定等待 7 秒。先用 Playwright 拦截 POST，请求抵达服务端并提交后故意丢弃回执。页面显示过服务端正在处理，之后自行从历史加载回答。再发送第二条消息，看到服务端接收提示后刷新页面，重新连接同一会话。页面先显示仍在处理，之后自动显示第二条回答。浏览器断言整个流程只有两个聊天 POST。控制台出现的 net::ERR_FAILED 是第一次故意中断的回执。没有调用云端模型，没有验证物理断网或进程中途强杀。

```powershell
.\.venv-harness\Scripts\python.exe -m pytest -q --basetemp .magi/test-040
node --check src/magi/web/app.js
```

首次在受限环境运行专项组合时未取得完成输出，确认进程身份后停止该次试验。在允许本机 HTTP 的环境执行完整回归，460 项通过，3 条依赖弃用警告。JavaScript 语法、Ruff 和 Git 差异检查通过。[完整回归](validation/040-regression.txt)

浏览器通过 `npx --yes --package @playwright/cli playwright-cli -s=magi040 open http://127.0.0.1:8895 --browser msedge` 打开页面。通过 `run-code` 拦截一次 POST 回执，并在第二次发送中刷新。两个断言场景结束后，查询独立 SQLite 只读核对两条记录均为 COMPLETED。[浏览器与落库核对](validation/040-browser.json) · [恢复后截图](validation/040-recovered.png)

## 面试知识关联与复述

- **写入和可见性。** 为什么模型回答结束就显示完成会有问题？保存失败时，页面会宣告不存在的结果。本轮页面读已提交的业务状态，事务由既有后端负责。
- **查询和重复提交。** 为什么 GET 用消息 ID？它在回执丢失前已由客户端持有，可以定位原轮次。GET 负责观察，同一 POST 的幂等仍由业务库和请求摘要保证。
- **异步 UI。** 切换会话后旧响应才返回，为什么还要检查序号？旧请求可能比新请求晚完成，序号和会话共同判断是否可更新当前页面。

先对照浏览器两个场景，再看状态查询和刷新后的跟踪循环。试着回答：**回执丢失时，页面怎么知道该读哪一轮？如果 GET 还没找到记录，为什么要保留原消息 ID？**
