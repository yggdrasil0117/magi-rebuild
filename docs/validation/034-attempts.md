# 034 联网资料查询验证记录

日期：2026-11-02至2026-11-03。全部使用独立合成会话，不使用用户正式业务库。以下按实际尝试区分，不覆盖失败文件。生成精确率不是本轮已通过指标。

## 1. 接入发现与安装

- 查阅Exa官方MCP项目与官方Python SDK；实际初始化并执行list_tools，保存[034-mcp-discovery.json](034-mcp-discovery.json)。两个实际工具分别要求query/objective与urls，调用依据实际Schema。
- 首次受限环境pip获取包报找不到版本；允许网络访问后指定`https://pypi.org/simple`检查并安装`mcp==1.30.0`。安装前dry-run显示补入新依赖、不升级已有依赖。随后锁定新增依赖到`requirements-mcp.lock.txt`。本轮不是全新机器安装演练。
- 首次受限环境MCP连接失败，允许网络访问后发现和搜索成功。原始探针搜索返回保存在[034-search-probe.json](034-search-probe.json)；JSON保存成功后，打印包含`¶`的文本触发控制台GBK编码错误，因此那条命令退出非零。文件是UTF-8且结果存在，不能把整条命令称为完全成功。
- 官方服务匿名访问在此次实际调用中可用；没有长期额度与可用性测量，也没有接入Exa私钥。

## 2. 离线测试与修复过程

首次新增测试命令：

```powershell
.\.venv-harness\Scripts\python.exe -m pytest -q tests/test_web_research.py
```

[034-tests-first.txt](034-tests-first.txt)：13 passed，1 warning。覆盖参数和URL拒绝、片段上限、错误分类、预算，以及真实SQLite下模型成功或失败的来源保存与重复提交。传输和模型均为替身，不能据此声称联网服务可靠。

首次完整回归保存在[034-regression.txt](034-regression.txt)：444 passed，1 failed，3 warnings。失败为`test_capacity_check_includes_recovered_history`。原因是无条件加入联网提示挤占普通会话预算；改为只有本轮注入联网工具才追加`WEB_RESEARCH_PROMPT`，没有放宽预算测试。

中断恢复后，受限执行环境再次运行：

```powershell
.\.venv-harness\Scripts\python.exe -m pytest -q --basetemp .magi/test-web-034-final
```

[034-regression-final.txt](034-regression-final.txt)只有进度及失败标记，长时间没有完整结束或失败栈。核实命令行后停止了这次测试进程。确切根因未定位，不能认定这些失败全是环境原因，也不能将此文件叫最终通过记录。文件名保留原样以避免覆盖历史。

随后在允许的本机执行环境运行独立完整回归：

```powershell
.\.venv-harness\Scripts\python.exe -m pytest -q --basetemp .magi/test-web-034-resume
```

[034-regression-resume.txt](034-regression-resume.txt)：**445 passed，3 warnings，38.44s**。警告为Starlette/AnyIO与WebSocket依赖弃用。此时包含条件提示注入、严格网页返回头检查、查询目的持久化与中文状态展示。随后只补充文档、格式化验收服务脚本和改善来源链接颜色，没有改后端业务行为。

另执行`python -m pip check`得到`No broken requirements found`；新模块、测试与两个脚本的`ruff check`通过；`node --check src/magi/web/app.js`通过。新验收服务脚本经ruff格式化。完整通过不代表真实外网、真实模型或长期并发均已验证。

## 3. 真实千问与Exa：只生成一次

凭据仅在本机从已授权文件读取完整token，放进该进程环境；不输出、不写入报告。Exa客户端没有认证头，不复用千问和硅基流动凭据。调用命令主体：

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluate_web_research.py --data-dir .magi/web-034-a --output docs/validation/034-cloud-a.json
```

脚本拒绝覆盖已有目录或报告，设置独立SQLite路径、空本地资料目录、关闭embedding、生成上限1400。生产配置仍为768，不能把验收结果直接推广到默认输出预算。框架沿用原有模型与工具调用上限，无自动重试。

实际问题要求查Python 3.12官方TaskGroup异常处理，先搜索再读取，不写计划或记忆。[034-cloud-a.json](034-cloud-a.json)保留完整业务回执与历史。结果为1轮COMPLETED，3次千问请求，1次`magi_web_search`、1次`magi_web_fetch`。两次调用均得到来源片段。

内容人工复核**未通过完整语义支持要求**：

- 搜索混入CPython主分支和当前版本文档，未限定为3.12；程序没有版本过滤。
- 搜索摘录2000字符与网页读取4000字符没有提供完整目标章节，模型却说关键异常描述已完整获取。
- 回答混入GeneratorExit特殊处理，且“CancelledError不传播”“3.12.x系列行为一致”泛化过度，当前来源不足以支持。
- 正确的若干概括不抵消这些问题；真实链接、工具OK和COMPLETED均不构成答案正确证明。

人工复核另看[Python 3.12官方章节](https://docs.python.org/3.12/library/asyncio-task.html#task-groups)，该复核步骤没有补写或修改模型原始依据。没有为了获得更漂亮的回答再次生成。最后增加的返回头检查和objective持久字段仅有离线验证，真实报告来自此前一次运行；旧记录没有回填新字段。

## 4. 跨进程恢复与数据库核对

真实生成进程退出后，另起Python进程，只恢复并读库：

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluate_web_research.py --data-dir .magi/web-034-a --output docs/validation/034-restored.json --restore
```

[034-restored.json](034-restored.json)的history与原报告逐字段相同。随后数据库只读统计和事件计数保存在[034-persistence-audit.json](034-persistence-audit.json)：web_turns=1；plans、plan_revisions、memories、operations均为0；模型请求3次，工具2次。浏览器刷新后事件仍为12行，未新增模型或工具运行。

这个试验验证**完成后退出进程再读取**，没有真实模拟网络等待时杀进程、SQLite磁盘满、系统断电或多worker竞争。模型失败后保留来源与重复提交不重查由离线业务集成测试覆盖，不混称真实故障注入。

## 5. 浏览器检查

使用Playwright CLI和真实Edge；已有CLI首次受限npx报ENOTCACHED，允许安装访问后成功。首个受限环境启动的验收服务未监听，浏览器访问8894报ERR_CONNECTION_REFUSED；未能定位其内部阻塞根因。核对进程命令行后停止该挂起进程，重新在允许环境启动仅监听127.0.0.1的隔离服务，随后正常访问。

服务命令（MAGI_API_TOKEN为单独的临时验收令牌，不是个人模型密钥）：

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 scripts/serve_web_research_review.py --data-dir .magi/web-034-a --port 8894
npx --yes --package @playwright/cli playwright-cli -s=magi034 open http://127.0.0.1:8894 --browser msedge
```

实际检查：设置连接、选择webcheck会话、关闭自动记忆建议；展开“本轮联网资料 · 2次查询或读取”，检查真实搜索与读取来源；勾选联网，再切换三人格，选项变为未勾选且禁用。刷新页面，重新连接并选择同一会话，来源恢复。

首次截图发现默认蓝色链接在深色背景不清楚，增加来源区域链接颜色；刷新后截图已目视检查：[最终来源显示截图](034-browser-sources.png)。原始截图保留在本机`output/playwright/034-sources.png`，最终截图也在`output/playwright/034-restored-sources.png`。来源展开后较长，需要滚动；本轮没有视觉重设计。

浏览器控制台仅记录密码框未包含在form内的DOM建议，没有观察到JavaScript运行错误。本轮浏览器没有重新提交真实云端生成，也没有网络丢包注入，不把历史展示验收说成完整浏览器生成验收。

## 6. 交付范围与限制

可确认：官方SDK与实际服务连接、两个工具实际调用、来源有界保存、完成记录跨进程读取、网页展示及模式开关、离线失败与重放边界。不能确认：长期免费可用、来源新鲜度、语义正确率、敏感信息自动防泄漏、完整提示注入防御、所有DNS和跳转安全、远端响应字节总上限、多人生产并发。

原用户改动`src/magi/council/service.py`空行与`docs/validation/markdown`不属于本轮，保留并排除提交。没有将一般接入或页面修正写入question.md以凑工程成果。
