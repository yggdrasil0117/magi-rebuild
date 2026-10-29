# 模块 006：千问接入与三人格生成校验

日期：2026-10-29。用户因本地 4B 推理卡顿，要求优先接入千问AI平台 `qwen3.8-max`。本轮停止本地评测，保留已有改动和未完成记录；不自动下载、加载或切换到其他本地模型。

## 实际结果

1. **首次调用通过**：中国站官方 `https://maas.qianwenaiapi.com/compatible-mode/v1`，HTTP 200，模型 `qwen3.8-max` 返回“MAGI连接成功”；2.407 秒，用量 106 tokens。
2. **结构化输出通过**：真实验证 `response_format=json_schema`、strict、关闭思考后的 Opinion 字段校验，不仅测试普通文本接口。
3. **只读 Agent 工具通过**：临时库生成测试计划，Deep Agents 实际分发 `magi_get_plan` 并回到模型输出标题；日志有真实工具调用事件。保持四个只读工具，没有开放写文件、shell 或子代理。
4. **完整 HTTP 应用闭环通过一次**：日常背景 → 三人格创建候选 → 确认、重复确认 → 重新打开应用回读 v1 → 反馈生成更新候选 → 确认 v2 → 查询 v1/v2 → 日常追问。首次审议 22.156 秒、反馈审议 22.516 秒，各四次调用；候选时间分别为 360 和 120 分钟。修订后追问正确回答“实验诊断，30 分钟”。

完整脱敏证据：[006-qianwen-evidence.json](validation/006-qianwen-evidence.json)。本轮使用真实平台请求、真实 SQLite 与 HTTP TestClient，没有重跑浏览器或声称完成新一轮浏览器验收；上一轮网页验证见模块 005。

这证明接口和当前 MAGI 路径接通，不证明任意任务的成功率、模型质量或连续多轮稳定性。没有用新密钥运行大批量收费评测。

## 怎样启动

先安装新增的可选依赖（不升级既有 Agent 框架）：

```powershell
.\.venv-harness\Scripts\python.exe -m pip install -r requirements-qianwen.lock.txt
.\scripts\start_magi.ps1
```

默认配置改为 `magi.qianwen.toml`。脚本在 `DASHSCOPE_API_KEY` 未设置时隐藏输入密钥，仅传入当前服务进程，不写文件；服务退出后清除本次输入的进程环境变量。密钥不在源码、TOML、Git 或验证报告中。脚本另行显示的 `MAGI_API_TOKEN` 是本机网页访问凭据，不是千问 API Key。

打开 `http://127.0.0.1:8765`，填入本机网页凭据后使用。Open WebUI 无须保存千问密钥；Pipe 仍只连接 MAGI。若要明确选择旧本地配置，使用 `start_magi.ps1 -Config magi.harness.toml`；本轮没有再启动该配置。

凭据来自环境变量时，也可单独运行以下验证；它们会请求真实云端接口并产生平台用量，不会自动运行：

```powershell
.\.venv-harness\Scripts\python.exe -X utf8 scripts/probe_qianwen.py
.\.venv-harness\Scripts\python.exe -X utf8 scripts/demo_mvp.py --config magi.qianwen.toml
```

依赖：langchain-openai 1.7.3、openai 3.41.0、tiktoken 0.14.0、regex 2026.9.29。安装检查无需替换现有 langchain-core、Deep Agents 或 LangGraph；`pip check` 通过。凭据曾在聊天中明文提供，建议测试后在平台轮换，再通过隐藏输入使用新密钥。

## 请求怎样走

```text
网页 → FastAPI → WebService
  ├─ 日常聊天 → open_personal_session → Deep Agents
  └─ 三人格 → 冻结快照 → 三份意见 → 综合
                         ↓
                 统一 build_model 工厂
                         ↓
           ChatOpenAI 的 Chat Completions 兼容协议
                         ↓
               千问AI平台 qwen3.8-max
```

只在模型工厂处理云端的凭据、HTTPS 地址、关闭思考、输出预算和重试设置。云端使用 `response_format`，Ollama 保留 `format`；不把 Ollama 的 `num_ctx` 等参数发给千问。SDK 自动重试关闭，三人格仍最多 3+1 次；超时与总预算仍有效。框架 profile 对云端应用相同工具排除规则。

配置只允许本轮核实的中国站官方 HTTPS 地址；API Key 环境变量名可配置，不能在该字段填字面密钥。默认云端 checkpoint 与事件使用 `.magi/qianwen-*` 文件，避免把旧模型未完成推理直接接到新提供方；业务记忆与计划继续使用 `.magi/personal.sqlite3`。

官方依据：[首次调用](https://platform.qianwenai.com/docs/developer-guides/getting-started/first-api-call)、[官方客户端的平台与端点区分](https://qwenlm.github.io/qwen-code-docs/en/users/configuration/auth/)。千问AI平台中国站、QwenCloud 国际站和百炼的地址不混用，模型名也由真实响应核对。

## 三人格生成与失败处理

本轮在切换云端前已做的改进一并保留：

- 生成 schema 内联引用；限制短文本和列表长度；证据 ID 枚举只能来自当前快照；有显式预算时，用时字段必须填写且单步不超过预算。
- prompt 与解码器使用同一份 schema；去掉供模型引用的内部 run/thread 元数据，保留当前问题、记忆、历史和旧计划；提示当前反馈和时间预算优先。
- 生成约束与持久化业务模型分开，旧版本候选仍按原业务契约读取，不因缩短新输出而无法恢复历史。
- 总时长依然由程序求和校验。不会按比例偷偷改写模型的超预算计划，也不会放松校验后宣称成功。
- 每次调用记录结束原因、用量（提供方返回时）、长度与预算；非法字段仅记录字段路径及类型，不把错误原文或网络异常中的敏感内容塞入日志。
- 网页正文区分格式、字段、未知引用、截断、超时、未填用时、超预算、鉴权/权限/限流失败，并显示可核对的总分钟数。失败没有候选，也不自动重跑。

本地对照事实：原始 4B 固定评测覆盖三个场景、创建/反馈、各重复两次，共 12 次，仅 2 次 COMPLETED、10 次超预算。改进后的同套评测完成前 6 次（5 次 COMPLETED、1 次超预算）后，根据用户电脑卡顿的反馈中止，剩余 6 次未执行。两者样本量不同，**不能把 5/6 当作完整对照或稳定成功率**。记录在 `.magi/evaluations`；评测脚本现在支持显式配置、限定次数并逐次保存，不自动跳过失败。

## 离线验证与学习入口

新增测试覆盖：生成 schema 与旧候选兼容、字段隐私、云端参数和零重试、endpoint 边界、严格结构化响应、同框架只读工具、401/403/429 分类。完整回归命令：

最终结果：**228 passed in 15.49s**，一个既有上游 TestClient 弃用警告；修改文件 Ruff 检查与格式检查通过，PowerShell 启动脚本语法检查通过。测试密钥均为假值，云端测试传输已替换，不额外请求平台。

```powershell
.\.venv-harness\Scripts\python.exe -m pytest -q
```

阅读顺序：`magi.qianwen.toml` → `harness/model_factory.py` → `council/generation.py` 和 `council/service.py` → `scripts/probe_qianwen.py` → `tests/test_qianwen_provider.py`。

这一阶段的核心是：ID、记忆、计划和保存事务仍由 MAGI 管理；更换的是模型请求的适配层。普通对话成功、结构化成功、工具成功和完整业务闭环成功，是四份不同的验证证据。
