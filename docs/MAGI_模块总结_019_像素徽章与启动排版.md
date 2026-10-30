# 模块 019：像素徽章与启动排版

日期：2026-10-30。用户反馈原叶片线条字符画与参考截图差距很大，本轮按截图调整终端 UI，不改模型、后端、网页或保存规则。

## 改动与使用

- 去掉手绘线条占位图，采用参考项目中 19 行块字符 NERV 徽章。仅提取 `Clawd.tsx` 中的静态字符素材，没有引入 React/Ink、Claude Code 或 CCSourcemap 核心。
- 宽屏左侧暗红徽章、右侧产品信息；中等宽度上下排列，极窄窗口退化为文字标识，避免徽章折行。产品信息显示真实 MAGI 名称，不冒用参考截图里的模型、版本或订阅信息。
- 启动时恢复完整会话回复链与最近候选，但不展开历史；通过 `/history` 查看。首次命令提示缩为一行，帮助按需查看。生成完成不再输出内部 `COMPLETED`，未完整完成仍提示状态。
- 普通输入以 `❯` 开头，MAGI 回答用六角形标识。启动画面无需更换凭据或重启后端，退出客户端重新启动即可。

无凭据预览：

```powershell
.\scripts\start_terminal.ps1 -Preview
```

正式聊天：

```powershell
.\scripts\start_terminal.ps1
```

新增 `src/magi/terminal_ui.py` 集中静态徽章和响应式排版；入口 `Terminal.welcome` 只输出视觉内容，`history(recent=0)` 只改变启动展示，不删历史、不重跑模型。

## 素材与验证

素材来源：[NERV-CODE Clawd.tsx](https://github.com/Ax1i1om/NERV-CODE/blob/main/src/components/LogoV2/Clawd.tsx)。已读取其 LICENSE，并在仓库根目录 `THIRD_PARTY_NOTICES.md` 保留完整 MIT 许可及原有声明。徽章仍为 EVA 同人视觉元素，未宣称原创徽章。

验证过程保留：

1. 直接执行模块预览，确认 19 行块字符及左右布局可输出。
2. 首次终端测试 9 passed、1 failed：新增的子进程测试未使用启动器已有的 `-X utf8`，Windows 重定向输出时按 GBK 编码，无法输出部分块字符。测试改为与正式启动器相同的 UTF-8 参数，不掩盖该限制。
3. 最终终端测试 **10 passed，3.80秒**，3个既有第三方弃用警告。范围含临时后端、模拟模型、确认与断线重试，以及 24/40/60/80/120 列排版不溢出、无需凭据的模块预览。
4. 真实调用 Windows PowerShell 的 `start_terminal.ps1 -Preview -NoColor`，进入预览并正常退出。Ruff 检查、格式检查通过。
5. 使用与启动区相同的 `welcome_rows(100)` 数据和本机 Consolas 字体生成[排版预览](validation/019-terminal-preview.png)，已人工查看。原始排版数据见 `validation/019-welcome-layout.json`。这是排版模拟图，不是实际终端截图或服务连接证据。

本机未安装 Pillow，预览改用 Windows System.Drawing 生成，未新增产品依赖。完整历史、真实模型与后端进程重启未重新验收；本轮无需修改其业务逻辑，执行终端定向回归，没有重复全量测试。

## 限制

真实终端的背景、字距、字体与字符抗锯齿由宿主终端控制，不能保证任意字体逐像素一致。建议 Windows Terminal 使用等宽字体；窗口至少约 80 列以显示左右布局。不自动改用户的全局终端配置，不引入全屏 TUI。本轮没有增加实时人格进度、流式输出或子 Agent，也没有调整网页主题。

属于 UI 调整，不写入 `question.md`。本轮交付后等待用户查看真实终端效果。
