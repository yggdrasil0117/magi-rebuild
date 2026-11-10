# MAGI 模块050：NERV 终端风格网页

2026-11-10

打开 MAGI 网页时，用户先看到 NERV / MAGI 标识、三人格编号和工作区导航。默认进入对话终端，可以直接输入，也可以在「工具与运行选项」中找到原来的联网、语义召回、Issue 草稿和后台任务开关。切换到研究、计划或记忆时，主区显示相应面板，导航同步标出当前所在位置。窄屏将侧栏放在面板上方，保留原来的中文按钮。

配色与编号沿用 [MAGI 终端](../src/magi/terminal_ui.py) 的识别方式，并参考 [NERV-CODE 的暗色与六边形视觉语言](https://github.com/Ax1i1om/NERV-CODE#第参話-nerv-theming--the-test)。网页使用 CSS 和自绘 SVG，三枚六边形是人格标识。真实连接及任务状态继续由页面上的文字回报。

## 页面怎样切换

[index.html](../src/magi/web/index.html) 为同一页面提供导航、会话状态条和四个面板。CSS 变量统一背景、文字及点色。桌面工作区为双列，媒体查询在窄屏将其改为单列。工具开关收在原生 `details` 中，浏览器负责展开、折叠和键盘操作。对话消息在 [app.js](../src/magi/web/app.js) 的 `show(turn)` 中分成「你」和「MAGI」两块，并继续用 `textContent` 写入用户和模型正文。

导航状态由 `showWorkspacePanel(panelId)` 统一设置。以下摘录省略对话按钮的当前项标记，展示其余三个面板的同步过程。

```javascript
for (const [panel, toggle] of [
  ['plansPanel', 'togglePlans'],
  ['memoriesPanel', 'toggleMemories'],
  ['researchPanel', 'toggleResearch'],
]) {
  const open = panel === panelId;
  $(panel).hidden = !open;
  $(toggle).setAttribute('aria-expanded', String(open));
}
```

`panelId` 是页面内部的 DOM ID，由现有入口按钮、返回按钮或打开成果的动作提供。函数修改可见性及导航属性。比如正在看研究成果时点击个人记忆，会显示记忆面板，同时关闭研究面板并取消其展开标记。页面仍调用原来的记忆列表接口，切换本身没有模型调用或数据库写入。用户从研究面板提交任务、确认草稿或批准保存时，原请求、业务 ID 和确认边界继续适用。既有后台任务可以在隐藏的面板外继续执行。

## 验证

```powershell
node --check src/magi/web/app.js
node --check src/magi/web/memory.js
.\.venv-harness\Scripts\python.exe -X utf8 -m pytest -q tests/test_web_mvp.py tests/test_research_tasks.py
git diff --check
```

JavaScript 语法和 Git 空白检查通过。网页及研究接口回归 53 项通过，有 1 条依赖弃用警告。

浏览器验收使用真实 Edge、Playwright CLI 和 `serve_research_demo.py` 的独立合成库。1440 × 900 检查桌面布局及入口可见性。连接后打开共同聊天室，用可控模型提交研究任务并看到完成状态、报告草稿和保存入口。随后依次切换到记忆、计划和对话，页面快照各显示相应的主面板。390 × 844 检查窄屏，视口宽度与文档宽度均为 390，控制台错误数为 0。[桌面截图](validation/050-console.png) · [窄屏截图](validation/050-mobile.png)

本轮未调用真实模型，未在浏览器提交报告保存或重启恢复。现有接口回归覆盖相关的任务与持久化边界。

## 学习关联

这次改动的前端问题是多个按钮和面板需要保持一致。原生 HTML 提供语义与键盘行为，CSS 负责视觉响应，`showWorkspacePanel` 把可见面板和按钮状态放在同一个函数里更新。可自查：为什么只隐藏面板还不够，导航的 `aria-expanded` 也需要同步？为什么在展示模型回答时继续使用 `textContent`？
