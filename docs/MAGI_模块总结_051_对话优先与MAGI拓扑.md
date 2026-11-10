# MAGI 模块051：对话优先与 MAGI 拓扑

2026-11-10

上一版网页用六边形编号表达三人格，页眉只写了 NERV 字样，终端实际使用的红色块字符徽章没有出现在网页。新版恢复徽章，并依据用户提供的掌机界面参考图，把 BALTHASAR 放在上方，CASPER 和 MELCHIOR 分列下方，三者连到中央的 MAGI。黑底、橙红框和绿色线条用于区分控制信息和人格拓扑。拓扑是静态识别图，页面上的会话状态仍来自实际操作。

默认页提供对话和发送。初次进入需要连接本机服务，连接后首页的连接按钮收起。研究、计划、记忆、设置和对话开关集中在右上角的「工作区」里。选中某个面板后菜单会收起。开启三人格分析时，对话区会提示本轮采用的模式。原有联网和后台任务等权限仍由用户选择，生成后的保存依旧需要对应确认。

## 实现与操作路径

网页徽章从 [terminal_ui.py](../src/magi/terminal_ui.py) 的 `NERV_BLOCKS` 生成。下面是 `nerv_emblem_svg` 内把字符画转换为像素路径的摘录。

```python
for y, row in enumerate(NERV_BLOCKS):
    for x, char in enumerate(row):
        if char in "█▀":
            path.append(f"M{x} {y * 2}h1v1h-1z")
        if char in "█▄":
            path.append(f"M{x} {y * 2 + 1}h1v1h-1z")
```

一行文字由上、下两行像素组成。全块填上下两格，半块只填对应一格。浏览器从 `/nerv-emblem.svg` 读取这张本地矢量图。终端和网页因此使用同一份图案数据。已有出处记录见[第三方素材声明](../THIRD_PARTY_NOTICES.md)。

如果用户略过连接按钮直接发送，前端会展开嵌套菜单并把焦点移到凭据输入框。以下是 [app.js](../src/magi/web/app.js) 的实际实现。

```javascript
function openConnectionSettings() {
  $('workspaceMenu').open = true;
  $('settings').open = true;
  $('token').focus();
}
```

这个函数只切换界面。后续点击连接仍通过已有接口校验；成功后关闭菜单和连接提示。工作区使用浏览器原生的 `details` 和 `summary`，避免为简单折叠菜单新写一套键盘行为。发出的消息仍由原后端处理，测试使用的替身只在验收进程中注入。图片、菜单和拓扑的展示不创建任务或记忆。

## 验证与学习

```powershell
node --check src/magi/web/app.js
node --check src/magi/web/memory.js
.\.venv-harness\Scripts\python.exe -m ruff check src/magi/terminal_ui.py src/magi/web/app.py
.\.venv-harness\Scripts\python.exe -X utf8 -m pytest -q tests/test_web_mvp.py tests/test_terminal.py tests/test_research_tasks.py
```

63 项相关回归通过，3 条依赖弃用警告。JavaScript 语法、Ruff 和 Git 空白检查通过。真实 Edge 在 1440 × 900 下验证未连接时直接发送会展开并聚焦凭据框，连接后菜单收起，普通对话通过本机 API 获得一条可控回复。打开工作区可找到原有入口，进入记忆后菜单收起，返回聊天恢复对话。390 × 844 下徽章加载成功，菜单关闭，文档宽度与视口均为 390 像素。浏览器控制台无错误。本轮没有调用云端模型、重新验收报告发布或重启恢复。[桌面截图](validation/051-console.png) · [窄屏截图](validation/051-mobile.png)

可先看截图，再读 `openConnectionSettings` 和 `showWorkspacePanel`。复盘时可以想一想，为什么要统一打开两层折叠区域后再聚焦，以及为什么同一份徽章数据可以服务不同渲染介质。
