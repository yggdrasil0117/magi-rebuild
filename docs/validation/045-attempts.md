# 模块045验证尝试与修正记录

本文保留失败、修正和未执行项。通过数量只说明当前代码在这些固定检查中满足程序边界，不能代替报告内容的人工判断。

## 1. 回归与迁移

首次全量回归得到 `480 passed, 6 failed`。六项失败不是研究流程断言，而是旧测试把 schema 版本写死为12；同时，最初的 v13 重建脚本在“数据库已经有部分新结构”的复开场景发生冲突。修正后，迁移通过结构探测兼容现有 v13 表，六个旧测试改为引用 `SCHEMA_VERSION`，全量回归达到 `487 passed, 3 warnings in 47.88s`。

随后补入共享评审预算切分、任务总截止时间、显式跨会话消息分享和上下文容量裁剪四项边界，研究模块达到19项测试。最终全量回归为 `491 passed, 3 warnings in 52.12s`，详见[最终回归原始摘要](045-regression.txt)。

2026-11-04 的最终回归在受限沙箱内先卡于 FastAPI `TestClient` 初始化；单测复现后保留该结果，再经授权在沙箱外执行相同命令得到491项通过。这里区分了环境通信限制和业务测试失败。

## 2. 发布文件的 Windows 差异

最初用临时文件加硬链接实现“不覆盖已存在目标”的发布边界；Windows 当前卷或安全策略拒绝 `os.link`。最终实现保留同目录临时文件、`flush` 和 `fsync`，Windows 改用不会覆盖既有目标的 `os.rename`；非 Windows 仍用硬链接取得排他创建语义。故障注入覆盖“数据库已批准、文件发布中断”的重试：内容相同则补齐为 `SAVED`，内容不同则进入 `RECONCILE_REQUIRED`，不覆盖未知文件。

## 3. 真实 Edge 页面闭环

使用 Playwright 驱动真实 Edge，后端使用可控模型，避免把页面问题和云端内容波动混在一起。页面选择共同聊天室，MELCHIOR 为主研究员，BALTHASAR 和 CASPER 为评审；后台生成 v1，两个评审提出问题，主研究员生成 v2，原评审复核为 PASS。用户批准后得到固定 Markdown 文件，刷新并重新连接后仍恢复 v2、`PASSED`、`SAVED`，控制台错误为0。

浏览器运行共留下1个任务、6次人格运行、2个不可变版本、4份评审和1个确认回执；文件字节与 v2 数据库正文相同，SHA-256 与 `payload_hash` 一致。结构化证据见[浏览器审计](045-browser-audit.json)，截图见[刷新恢复页面](045-browser-restored.png)。

浏览器验收后核对数据库，发现 v1 在产生 v2 后仍显示 `IN_PROGRESS`。这不影响 v2 的发布内容，但会误导版本历史。交付代码已经在 `ResearchArtifacts.add_version` 中将旧版本改为 `UNRESOLVED`，并增加断言；没有篡改旧浏览器数据库来伪造当时结果。

一次 Playwright 命令误写成 `await page.waitForTimeout(2500)`，被 CLI 解析为 `SyntaxError: Unexpected identifier 'page'`。后台任务本身没有因此停止，后续页面快照、刷新恢复和数据库核对完成验收。

## 4. 真实云端固定样本

执行：

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 scripts\evaluate_research_agents.py --data-dir .magi\research-cloud-045 --output docs\validation\045-research-cloud.json
```

结果：`缺少环境变量 DASHSCOPE_API_KEY；尚未调用模型`。脚本在模型请求前拒绝运行，没有生成结果文件，也没有启动本地模型。因而本轮尚未验证三个提示配置在真实内容上的差异、联网证据语义支持或协作是否实际改善报告；离线可控模型只证明流程和程序约束。

## 5. 静态检查

本轮23个 Python 文件通过 Ruff check 与 format check，`app.js` 通过 Node 语法检查。全仓 Ruff 仍有11项早期文件基线问题，本轮未触及且不影响491项回归；未借升级夹带修正，具体位置记录在[回归文件](045-regression.txt)。

最终文档检查第一次把 PowerShell 通配符直接传给 `rg`，Windows 返回“文件名、目录名或卷标语法不正确”；改用 `rg -g` 文件过滤后复测，新增代码与045文档没有行尾空白，JSON可解析，Markdown代码围栏成对，仓库内相对链接存在。
