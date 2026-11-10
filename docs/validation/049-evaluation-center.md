# 模块049验收记录

2026-11-10。运行数据使用独立工作树或合成资料。原始运行位于本机 `.magi/agent-evaluations/<运行ID>`，未将运行数据库或日志提交进 Git。

| 检查 | 运行 ID | 实际结果 |
| --- | --- | --- |
| 首次记忆基线，`cd02584` | `fe49f8bc470b4f6b9cc0590b9a755143` | 10 个案例完成 |
| 首次记忆新版，`f0a6f5c` | `35f4847dc0bf4aafb53671dbd6095147` | 10 个案例完成，和基线对照时被 `suite_hash` 拒绝 |
| 修复后记忆基线，`cd02584` | `afa59c8be84d4a609974bb5377ae4baf` | 10 个案例完成 |
| 修复后记忆新版，`dfe2755` | `a6c4246388b94eafb3f417b0a73ca805` | 10 个案例完成，对照成功 |
| 首次离线研究 | `d83e4bced5e24fa396eb9523963bfc0a` | 21 项通过 |
| 最终离线研究 | `581c631f2dbf44219ab9c4e881623a2a` | 21 项通过，与记忆组比较时明确拒绝 `suite` 不同 |
| 云端研究 | `5bb55b7b10e14d938ae7f52b9c9a405f` | 子进程退出码 2，缺少 `DASHSCOPE_API_KEY`，未请求 |
| 云端记忆 | `2029443b729a431f8bb83ff35ad3d177` | 子进程退出码 2，缺少 `SILICONFLOW_API_KEY`，未请求 |

修复前，同一个评测脚本的 Git blob 为 `ac43ccea8101b32a63b7781a02dd8c80aeb46202`，两个工作树的实际换行分别为 LF 和 CRLF。修复使用统一换行后的文本指纹，schema 1 历史保留，schema 2 重新取得基线。一次 PowerShell 包装命令在预期的拒绝检查后保留了非零退出状态，后续把检查明确写为预期退出码 2，并在匹配后退出 0。正常对照与拒绝场景分别复测。

成功对照时语义组前后均命中 6 条，禁止项均为 0，样本中位耗时分别为 25.57 和 26.3595 毫秒。两个提交间没有变更记忆产品逻辑，这个实验用于验证从指定检出运行、指标差值计算和案例对齐。小样本的耗时差不用于判断性能变化。

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluation_center.py run memory-offline --checkout .magi/eval-baseline-049
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluation_center.py run memory-offline
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluation_center.py compare afa59c8be84d4a609974bb5377ae4baf a6c4246388b94eafb3f417b0a73ca805
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluation_center.py run research-offline --checkout .magi/eval-baseline-049
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluation_center.py show 5bb55b7b10e14d938ae7f52b9c9a405f
.\.venv-harness\Scripts\python.exe -X utf8 -m pytest -q tests/test_evaluation_center.py
```

中心专项 2 项通过，变更文件 Ruff 检查通过。

全量回归使用 `.\.venv-harness\Scripts\python.exe -X utf8 -m pytest -q`，结果为 497 项通过，50.00 秒，3 条依赖弃用警告。
