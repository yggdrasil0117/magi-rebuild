# 模块043 独立部署、备份与恢复

## 备考会话换一个目录以后还能接着用

在原实例里，你确认过「每周只能安排 4 小时备考」，聊过 Python 3.12 和待验证的协程取消实验，保存过计划与工作单。聊天太长时已经生成摘要。另外，一条 GitHub Issue 有核对回执，另一条处于结果未知。恢复到新目录后，你应当能查原记录，也能换一种说法请 Agent 继续准备面试。

本轮提供独立实例配置、离线归档和新目录还原。它复用当前 Python 安装和单执行器部署。实例拥有自己的数据库、资料目录与监听端口。健康检查确认服务可以访问业务库。千问、硅基流动、网页和 GitHub 的凭据仍由运行环境提供。

## 如何启动与迁移

在项目目录使用已装好的 MAGI Python 环境。全新安装可沿用当前指南的锁定依赖安装步骤。下面的 `.magi` 目录需先存在。源与目标使用同一个 `MAGI_OWNER`，否则新实例查询的是另一个用户范围。

```powershell
.\.venv-harness\Scripts\python.exe -m magi.deployment init --config magi.qianwen.toml --directory .magi/instance-a
.\.venv-harness\Scripts\python.exe -m magi.web --config .magi/instance-a/config.toml --port 8766
```

在另一窗口查询 `http://127.0.0.1:8766/health`，看到 ready 后，用环境里设置的本机服务凭据连接网页。原来的启动命令仍使用 8765。新实例的 `docs` 初始为空，可以放入允许 Agent 读取的资料。

如果希望沿用 PowerShell 启动脚本隐藏输入模型凭据，可以执行 `.\scripts\start_magi.ps1 -Config .magi/instance-a/config.toml -Port 8766`。

停止源服务后执行备份。还原使用一个有效的模型配置模板。它可以来自仓库，原实例目录丢失时也能用模板生成新的路径。

```powershell
.\.venv-harness\Scripts\python.exe -m magi.deployment backup --config .magi/instance-a/config.toml --archive .magi/instance-a.zip
.\.venv-harness\Scripts\python.exe -m magi.deployment restore --config magi.qianwen.toml --archive .magi/instance-a.zip --directory .magi/instance-b
.\.venv-harness\Scripts\python.exe -m magi.web --config .magi/instance-b/config.toml --port 8766
```

备份的含义是保存停服时已有的业务状态。把恢复实例作为正式实例使用时，让旧实例保持停止。如果还原的是更早的快照，快照之后执行过的外部动作需要另行核对。用户聊天和记忆本身会进入备份，归档应保存在个人可控的位置。

## 哪些数据会恢复

| 归档文件 | 恢复后承担的职责 |
| --- | --- |
| business.sqlite3 | 已确认记忆、计划版本、聊天历史、任务队列、工作单记录与 Issue 回执 |
| checkpoint.sqlite3 | 旧会话与终端入口依赖的 LangGraph 数据 |
| checkpoint.summaries.db | 用会话身份及原始消息前缀校验的滚动摘要 |
| business.approvals.sqlite3 | 工作单审批流程的 checkpoint |
| task-documents 下的已登记文件 | 恢复真实工作单文件及其现有编辑 |

业务库必须存在。其他数据库存在才归档。源实例已缺失的工作单会在清单中标出，恢复后仍能查看原业务记录。工具授权资料目录、日志、Qdrant 向量、RAG 缓存和凭据独立管理。授权资料可另行复制到新实例的 docs。新 Qdrant 目录会在开启语义召回后由有效记忆逐步建立。工具检索缓存会在后续请求中补建。

## 备份怎样拿到一致的文件

`src/magi/deployment.py` 的 `backup(settings, archive)` 接收已解析配置和新的归档路径。它用和 Web、CLI 相同的应用锁保护快照。

```python
with ApplicationLock(settings.business_db_path.with_suffix(".lock")):
    manifest = snapshot(settings, staging)
```

源实例在运行时占有应用锁，此时备份拒绝启动。锁内先使用 SQLite 的 backup API 创建每个数据库副本，再从副本读取工作单 ID，复制对应文件。即使数据库启用了 WAL，也能读取已提交的数据。读取副本用的是真实路径映射，归档内部文件名由程序固定。快照完成后释放应用锁，再对暂存内容压缩。归档发布要求目标文件不存在。Windows 使用同卷重命名，其他系统使用硬链接。目标目录所在卷需支持对应操作。

## 还原怎样避免半套数据

`install(directory, template, archive)` 接收新的实例目录、模型模板和可选的备份。初始化时只创建配置及目录，还原时先验证归档。暂存目录和最终实例在同一卷，只有校验都通过后才发布。它返回新配置的路径。

`unpack` 先核对清单和条目名，只接受已知数据库和合法工作单名。随后把每个文件写入暂存目录。下面摘自实际代码：

```python
with source.open(name) as current, target.open("xb") as output:
    shutil.copyfileobj(current, output, CHUNK)
if file_record(target) != expected:
    raise ValueError(f"归档校验失败：{name}")
if name in DATABASES:
    check_database(target, business=name == BUSINESS)
```

`name` 是程序允许的相对文件名，`expected` 是清单里的字节数和 SHA-256。还原后实际内容不匹配就停止。SQLite 还需通过完整性快检，业务版本必须在当前程序支持范围内。清单上限 1 MiB，数据解压上限 2 GiB。清单和摘要用于发现损坏和意外改动，备份来源由使用者保证。校验失败时最终目标目录不会出现。归档或还原过程都不迁移原库。

恢复后，聊天消息和已确认记忆沿用保存时的标识。网页启动先调用既有恢复逻辑。UNKNOWN 的 Issue 继续等待人工只读核对，已完成后台任务继续关联原轮次。恢复摘要仍会核对消息前缀，避免使用与聊天原文不匹配的摘要。

## 验证与学习

第一组专项用例覆盖运行锁、WAL 数据、排除可重建索引、重复目标拒绝、路径越界和损坏归档。首次尝试时，WAL 用例只打开了连接，提交后没有观察到 WAL 文件。修正为显式保持读取事务，验证的是尚留在 WAL 中的已提交记忆。同次受限环境对 ZIP 发布的硬链接返回 WinError 5，Windows 路径改用同卷重命名。修正后 3 项专项通过。

```powershell
.\.venv-harness\Scripts\python.exe -m pytest -q tests/test_deployment.py --basetemp .magi/test-deploy-first --tb=short
.\.venv-harness\Scripts\python.exe -m pytest -q --basetemp .magi/test-043
```

完整回归 472 项通过，3 条已有依赖弃用警告。[回归输出](validation/043-regression.txt)

独立实例验收脚本第一次执行时错误导入了只在 pytest 环境可用的测试包，退出为 `ModuleNotFoundError: No module named 'tests'`，没有准备数据或调用云端。修正后第一次复测在服务启动期间遇到本机健康探测 ConnectTimeout，报告保留。允许就绪探测等待短暂超时后，用全新目录再次执行。[就绪探测失败](validation/043-deployment-b.json)

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluate_deployment_restore.py --data .magi/acceptance-043-c --output docs/validation/043-deployment-c.json
```

命令通过环境使用千问和硅基流动。脚本用替身准备源数据和 Issue 回执。两个实际 Web 进程从各自配置目录启动，健康检查成功。运行中备份被拒绝，停止源进程后归档包括四个数据库和一份工作单。恢复的工作单逐字节相同，首次启动前向量目录为空。还原实例找回七轮历史、已完成后台任务和两个 Issue 状态 VERIFIED、UNKNOWN。没有向真实 GitHub 发请求。[独立部署验收](validation/043-deployment-c.json)

随后从还原实例发起真实千问续聊。硅基流动召回记录显示版本 1 的「每周只能安排4小时备考」进入本轮。模型选择 `magi_get_issue_receipt`，回答给出已核对的模拟 Issue #21 和协程取消实验待验证。回答遗漏了用户要求复述的 Python 3.12，虽然这项内容仍在摘要中。数据恢复、工具选择和内容复述完整性是三项不同的验证。此处保留真实遗漏，不重跑挑选回答。

第三个实际进程重新打开同一还原目录，健康检查成功，读回续聊后的八轮历史。SQLite 中的摘要覆盖到 10 条消息，仍有 Python 3.12，Issue 状态仍为 VERIFIED、UNKNOWN。语义召回后 Qdrant 目录已重建。此次重新启动只读查询，没有新增模型调用。[重启核对](validation/043-restart-audit.json)

另实际执行 `magi.deployment init`、`backup`、`restore` 三条命令。首次命令组合在既有验收目录的子目录发布时遇到 WinError 5，未发布目标目录，确切原因未定位。改用 `.magi` 下的新同级目标逐条运行后成功，归档仍为五个文件。

本轮使用现有 Python 环境验证新数据目录和独立进程，未做全新机器依赖安装、浏览器操作、远程部署或磁盘故障试验。快照只包含备份当时的本机业务状态。

阅读顺序建议从原实例停服开始，接着看备份锁、归档清单、新实例发布，最后看恢复后的模型回执。可复述两个问题：

1. 为什么即使 SQLite 能在线备份一个数据库，MAGI 仍要先拿实例的应用锁？
2. 恢复后 Qdrant 目录为空，为什么仍能找回确认过的记忆？旧 Issue 的 UNKNOWN 又凭什么保留下来？

这两题分别对应跨文件一致性、派生索引与业务事实。面试可能追问归档是否支持完整灾难恢复。可用项目证据解释本机离线归档、新路径恢复与 Agent 续接，再区分线上多实例、异地副本、恢复点之后的远端动作与磁盘级故障。
