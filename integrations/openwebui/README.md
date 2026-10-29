# Open WebUI 0.12.5 与 MAGI

实际验收版本：Windows Python 3.12.3、Open WebUI 0.12.5。保留上游品牌与许可；MAGI 仅为自有 Pipe/Action，未修改上游源码。安装包内 `open_webui-0.12.5.dist-info/licenses/LICENSE` 为本轮核对原文。`requirements-webui.lock.txt` 是该 Windows 环境的 245 项依赖解析快照，不保证跨平台适用。

## 安装与启动

在仓库根目录的两个 PowerShell 窗口运行：

```powershell
# 窗口一：MAGI，凭据由启动脚本生成并仅在本窗口显示
.\scripts\start_magi.ps1

# 首次安装 Open WebUI，使用独立环境
.\.venv-harness\Scripts\python.exe -m venv .venv-webui
.\.venv-webui\Scripts\python.exe -m pip install -r requirements-webui.lock.txt
# 窗口二
.\scripts\start_webui.ps1
```

如果本机策略禁止直接执行脚本，可用 `powershell -ExecutionPolicy Bypass -File .\scripts\start_magi.ps1`，另一个脚本同理；只对该进程生效。浏览器打开 `http://127.0.0.1:8080`，创建本机用户。API 默认本机 `8765`，不启用多 worker 或 reload。上游离线启动可能报告本地 embedding 未缓存；本轮不使用其 RAG，不下载新的 embedding 模型。

## 导入

1. 管理员进入工作空间的 Functions，创建并粘贴 `magi_pipe.py`，保存、启用。两个模式应出现在模型选择器。
2. Pipe Valves：`MAGI_URL=http://127.0.0.1:8765`；`MAGI_TOKEN` 填 MAGI 启动窗口的凭据；`OWNER_USER_ID` 填此本机用户真实 ID（管理面板用户详情可查）。不填时拒绝调用，不接受客户端任意 owner。
3. 同样导入、启用 `magi_action.py`，填写相同 Valves；设为 Global 或给两个 MAGI 模式配置该 Action。按钮名称取决于导入时设置的 Function 名称，建议“确认保存 MAGI 候选”。
4. 保存一条真实聊天。先选日常模式发送背景，再选三人格发下一条；不要使用临时会话、编辑、重新生成或旧节点分叉。后台标题/标签任务直接返回简单标题，不进入模型服务。

真实标识：Pipe 采用注入的 `__chat_id__`、`__metadata__.user_message_id`、`__message_id__`；Action 使用 `body.chat_id` 和被点击消息的 `body.id`。本轮在网页实际往返与数据库中核对过。只转发本轮新文本，不回灌 UI 历史。插件从上游服务端消息树校验所选分支，再由 MAGI 检查最新父回复。

## 完整演示

日常模式发送：

> 本周论文实验和求职准备共六小时，请简短回应，不要制定计划。

三人格模式发送（显式分钟预算会进行确定性总时长校验）：

```text
/预算 360 总预算360分钟。仅安排实验180分钟、求职120分钟、休息60分钟三个步骤，给出完成条件，比较取舍。
```

出现 `COMPLETED` 和候选 ID 后，在该回复下点击保存 Action；核对 `committed=true`、真实 `object_id`、`result_version=1`、`operation_id`。再次点击应返回同一回执与 `duplicate=true`。也支持新发 `/保存 <proposal_id>`；它从服务器查找候选，不从消息正文提取保存参数。

```text
/计划
/计划 <plan_id>
/计划 <plan_id> 1
/预算 120 /修订 <plan_id> 1 实验卡住，只剩两小时。缩减为诊断30分钟、简历60分钟、休息30分钟，解释保留与放弃的理由。
```

确认修订消息得到 v2；查询 v1、v2 对照。`/修订 <id> <旧版本> <反馈>` 也可单独使用；建议加 `/预算` 明确硬性时间上限。预算不会自动从自然语言抽取，未指定预算时模型仍可能算错。按钮返回的正文是确定性候选与业务回执，进度事件不能替代正文保存。

模型有质量波动：`PARTIAL` 不提供候选；新发更具体请求属于新 run，不会自动重试旧 run。BUSY 时稍后重试确认，不必重跑审议。接口超时后先查历史/计划；同一候选重复保存不会创建重复计划。UI 删除聊天不会删除 MAGI 业务记录。删除/修改记忆先停 API，运行现有 CLI，再重启。

两个服务均用启动窗口的 Ctrl+C 停止。Open WebUI 数据在 `.webui`，MAGI 在配置文件指定目录；凭据、历史和演示数据不要提交到 Git。最终真实验证及限制见 `docs/MAGI_MVP演示与验收.md`。
