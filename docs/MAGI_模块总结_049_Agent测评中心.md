# MAGI 模块049：Agent 测评中心

2026-11-10

假设你调整了三人格的评审规则。要判断这次改动有没有改善，你需要在前后两个版本运行同一批题，然后找到究竟哪一题多用了工具、哪一题漏掉了证据、哪一题失败。此前 MAGI 的研究和记忆已有评测脚本，但要分别找命令、记录版本和核对报告。本轮把选择案例组、运行指定检出、留存每次尝试和逐案对照接到同一个开发者命令入口。

## 如何使用

在仓库目录运行下列命令。它们依次列出案例组、运行、查看历史、展开一次运行、比较两次运行。用运行输出里的完整 ID 替换占位符。

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluation_center.py suites
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluation_center.py run memory-offline
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluation_center.py list
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluation_center.py show <运行ID>
.\.venv-harness\Scripts\python.exe -X utf8 scripts/evaluation_center.py compare <基线运行ID> <新版运行ID>
```

| 案例组 | 运行内容 | 查看什么 |
| --- | --- | --- |
| `research-offline` | 现有研究任务回归，使用可控模型 | 状态、预算、隔离、版本和工具边界，失败时有断言详情 |
| `research-cloud` | 现有六个千问固定研究任务 | 任务和评审状态、模型及工具调用量，原始报告留待逐条复核 |
| `memory-offline` | 固定向量，实际 SQLite 和本地 Qdrant | 命中、漏召回、额外返回项、禁止项和本机耗时 |
| `memory-cloud` | 同批合成记忆，真实 BGE-M3 嵌入 | 真实向量表现与调用状态 |

Windows 受限环境运行研究回归需要允许 TestClient 的本机通信。云端组须显式选择，并配置对应环境变量。运行目录默认是 `.magi/agent-evaluations`，可在子命令前通过 `--runs-dir` 修改。每次使用新目录，重复运行会得到新的运行 ID。任务和记忆评测继续使用原有脚本的隔离数据，研究云端的合成数据保存在当次目录下。

如果要指定基线代码，可先准备一个可信的 Git 工作树，再传入 `run memory-offline --checkout <工作树根目录>`。中心在该目录执行该版本的脚本，并把它的 `src` 放到 Python 导入路径上。默认要求工作树干净，以便 Git 提交能解释运行使用了哪份代码。临时诊断可传 `--allow-dirty`，记录会保留未提交状态，之后不能作为正式对照。

## 运行身份与留证

[evaluation_center.py](../scripts/evaluation_center.py) 的 `run_suite(root, suite, checkout, allow_dirty)` 先读指定工作树的提交和案例定义指纹，再创建运行目录。以下是保存初始记录后调用真实评测入口的实际代码摘录。

```python
with (directory / "process.log").open("wb") as log:
    result = subprocess.run(
        [sys.executable, "-X", "utf8", *args],
        cwd=checkout,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
record["exit_code"] = result.returncode
record["status"] = "FINISHED" if result.returncode == 0 else "FAILED"
```

`root` 是评测存放目录，`suite` 从固定白名单选择入口和超时，`checkout` 是要跑的工作树。`args` 由中心按案例组拼装，包含证据路径，研究云端还包含隔离数据目录。凭据沿用云端脚本从环境读取的方式。程序不把环境变量写进评测记录。报告中保留的提交、模型名和配置指纹用于解释一次结果。

运行 ID 标识一次评测尝试，提交标识代码版本，案例名标识同一套题中的某个输入。云端研究内还会有产品任务 ID 和人格运行 ID，它们指向一次业务任务和一次人格执行。运行状态初始为 `INCOMPLETE`。子进程结束后，中心解析原始证据并原子替换清单。若子进程失败或超时，仍记录退出信息和已产出的证据。若中心进程自身被强制中止，初始清单会留在原处。重跑使用新的运行 ID，原来的失败记录仍可通过 `show` 查看。

`read_evidence(path, kind)` 把 pytest XML 转成测试状态和断言信息，把记忆 JSON 转成命中、遗漏、额外返回和禁止项，并把云端研究报告转成任务状态、停止原因和用量。完整正文、评审和人工复核字段仍在原始证据里。中心的 `FINISHED` 表示子进程成功结束且有逐案结果。比如某份报告需要修改，仍应查看其任务状态和人工复核。中心不对模型报告自动打一个总分。

## 为什么对照有准入条件

下面是 `compare(before, after)` 在计算指标差值前执行的检查。

```python
for key in ("schema", "suite", "suite_hash", "config_hash", "model"):
    if before.get(key) != after.get(key):
        raise ValueError(f"对照配置不同：{key}")
if any(run["dirty"] or run["status"] == "INCOMPLETE" for run in (before, after)):
    raise ValueError("对照需要两个干净版本的已结束运行")
if not before["cases"] or before["cases"].keys() != after["cases"].keys():
    raise ValueError("逐案证据不完整或案例集合不同")
```

两个参数是从运行目录读取的清单。比较会按案例名对齐两侧，并返回每项可比指标的前值、后值和差值。同一套题中某项失败，仍可正常比较，便于看到新版是否修复。若评测脚本本身改变，指纹不匹配，需要先为两个版本使用相同定义取得新结果。这个指纹覆盖评测入口文本和云端研究配置，导入的测试辅助代码、Python 依赖及硬件仍会影响结果。正式对照宜固定运行环境，特别是比较耗时的时候。

## 验证

两套离线适配已在真实干净检出 `cd02584` 上运行。研究回归通过 21 项，记忆评测完成 10 个案例。云端研究尝试因为缺少 `DASHSCOPE_API_KEY` 在调用前退出，历史仍显示 `FAILED`，详情指向原始日志。两项中心专项测试覆盖失败一侧的诊断、混用案例或未提交代码时拒绝比较、换行兼容。

第一次跨提交比较拒绝了同一个 Git 文件。检查 `git ls-files --eol` 后确认，一边工作树是 LF，另一边是 CRLF。修复后使用文本读取统一换行再计算指纹，并把新清单标记为 schema 2。旧记录仍可查看，正式对照使用重新跑出的 schema 2 记录。

修复版实际对照了 `cd02584` 和 `dfe2755` 两个干净提交，10 个记忆案例全部对齐。语义组前后均命中 6 条，均无禁止项。这两个版本的记忆产品逻辑相同，结果用于确认中心的运行和对照机制。两条云端入口的缺凭据失败、换行问题的原始运行 ID 和复跑命令见[验收记录](validation/049-evaluation-center.md)。

全量回归 497 项通过。本轮交付命令行入口和结构化报告。网页看板、在线 A/B 和人工标注工作流尚未接入，真实云端质量本次未验收。

## 面试知识关联

这里的业务问题是“新版更好了”该用什么证据证明。配对评测让两次运行回答相同问题，版本标记和固定定义让变化可以追溯。pytest、SQLite 和 Qdrant 继续承担原来的测试和数据职责，MAGI 中心负责执行、留证、归一化和对齐。追问“失败的任务要不要算进分母”时，可先区分两种情况。有完整逐案证据的失败应保留并参加比较，缺少逐案证据的整次中断要单独展示，避免把缺失算成成功。

建议先实际执行 `suites` 和 `list`，再看 `run_suite` 如何保存失败，最后对照 `compare` 的拒绝条件。有空可自查两个问题。为什么运行 ID 与提交号需要同时记录？为什么 Git 提交不同可以比较，而案例定义变了通常要重跑基线？
