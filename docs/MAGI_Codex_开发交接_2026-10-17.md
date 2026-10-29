# MAGI 项目开发与教学交接（供 Codex 接续）

**更新日期：2026-10-17**  
**当前阶段：L0 需求/验收已冻结；L1 领域设计完成，正在进行 Python 纯领域核心的渐进式编码。**  
**最近完成的任务：完成 Ruff 格式整理；只调整空格、空行和导入格式，领域行为未改变。**  
**最新测试结果：2026-10-17 全量 pytest 10 passed；`ruff check` 与 `ruff format --check` 均通过。**  
**下一步唯一任务：定位并阅读冻结的 L1 领域设计原文，再确认 Ballot 的下一项编码任务；不得猜测字段或规则。**

> **请先阅读此文档，再检查仓库的实际代码。你是我的 Python、DDD 和 Agent 工程导师，不是自动代写项目的 Coding Agent。除非我明确请求，不要直接修改业务文件或生成完整实现。**

## 1. 项目定位与开发目标

MAGI 是受《新世纪福音战士》（EVA）启发的三人格多 Agent 辅助决策系统。它为用户确认的 A/B 决策提供独立、多视角、有证据边界、可解释的建议；可以在不稳定或证据不足时暂缓推荐。最终现实决策归用户，第一版不提供外部写执行能力。

学习目标不是快速生成整套系统，而是由我**亲自设计、实现、测试并解释** Python 领域核心，能够在不依赖 AI 自动写代码的情况下理解设计权衡，逐渐提高代码审查能力。参考版本 0.5.0 只用来对照，不是必须照搬的架构或技术栈。

当前只做 **L1：不依赖 LLM 的纯领域核心**；暂不引入 Agent/LLM 调用、FastAPI、数据库、消息队列、前端或 LangGraph 等复杂设施。

## 2. 文档与决策优先级（防止上下文漂移）

1. **用户在新会话中明确做出的最新决定**。
2. **冻结后的 L0 验收文档与 L1 领域设计/职责边界文档**；严格执行已确认规则。
3. **本交接文档对开发进度、当前代码和教学方式的描述**。
4. 较早的《MAGI_L0_设计交接_2026-10-16.md》和 MAGI 0.5.0 参考资料：用于溯源，不应覆盖更新后的决策。

**版本冲突提醒：**较早的 L0 交接中曾写“L0 进行中、尚未写代码、最大轮数尚未决定”，这些信息**已过期**。现在已经确定 Python 3.12、最多两轮、L0 冻结、L1 进入编码。对于本文件未列明的字段、异常契约、AC-001-T1～T8 的精确验收语义，必须回查对应原始冻结文档与仓库，**禁止按猜测补造规则**。不能将早期“草案”误说成全部已经实现。

## 3. 已冻结的关键产品与领域规则

### 3.1 三人格

- **MELCHIOR**：证据、逻辑、可行性、成本与不确定性；EVA 科学家侧面。
- **BALTHASAR**：人的福祉、安全、伦理、隐私、可逆性；EVA 母亲侧面。
- **CASPER**：自主意愿、战略、二阶影响与长期价值；EVA 原作的“女人”侧面作致敬，但避免性别刻板印象。

人格决定稳定的关注点与论证方式，不预设其必然支持 A/B，不强制制造分歧。可三票一致，但每票应有独立、可理解的理由、事实和未验证假设。

### 3.2 投票与合议

- 用户确认的正式决策选项只有 **A/B**，正式 `Ballot` 不允许 `ABSTAIN`；信息不足或失败由独立的业务机制处理，不能伪造有效投票。
- 第一轮三人格**独立隔离**投票，彼此不看到首次分析和投票；首轮记录不可覆盖。
- 第二轮为**受控交叉复核**。MAGI **最多两轮**。
- 合议稳定：两轮内三个**人格各自的选择均不变**，而且**没有未处理的关键新证据**；仅“2:1 票数没变”不够。不要求 3:0。
- 达到轮数上限仍不稳定：终止并**暂缓正式推荐**，保留最后有效票和重要变化，说明不稳定原因，交由用户判断；不能直接把最后一轮多数票当作正式推荐。
- 证据不足或严重冲突时，即使 3:0，也不得仅凭票数给出强行推荐。输出需说明成立条件、关键未知、改变结论所需证据。
- 扩展分析默认关闭，仅按冻结规则在正式决议后的独立任务中处理，不得修改已完成的正式投票/报告；扩展失败不破坏原结果。

### 3.3 L1 领域职责

- `DecisionCase`：**聚合根**，维护投票的接纳、生命周期和跨对象不变量。
- 状态：`READY → ROUND_1 → ROUND_2 → TERMINATED`，必要时支持**提前失败终结**；具体转移条件依据冻结 L1 文档。
- `Ballot`：不可变的正式投票领域对象，自身负责字段类型/取值等局部不变量。
- `Arbitration`：独立、确定性的**纯业务**仲裁组件，不做网络/LLM/数据库调用。
- `DecisionOutcome`：结构化、不可变的结果值对象。
- 已确认的相关模型名称还有 `ArbitrationInput`、`FinalFailureFact`；具体字段结构应回查冻结 L1 文档。
- 已确认的**结果判断优先级**：**决议未完成 > 合议未稳定 > 证据不足 > 稳定完成**。
- 最终失败事实：聚合根保存最小结构化业务事实，技术堆栈/诊断日志留在应用层。
- **原子终结与强异常安全**：先验证拟接纳的 `FinalFailureFact`，以拟接纳事实计算合法 `DecisionOutcome`；两者均成功后，`DecisionCase` 才一次性提交失败事实、Outcome 和 `TERMINATED`。若校验或仲裁失败，不得留下部分修改；业务上未完成与技术异常需区分。
- 聚合操作须遵守已冻结的一致性、幂等与异常安全规则；具体键、错误类型、异常时序不可自行设计代替原文。

### 3.4 L0 验收参考

正式验收以冻结后的 **AC-001-T1～T8** 和其他 L0 规则为准。早期 L0 文档列出的典型场景包括：2:1 保留少数意见、3:0 但证据不足、总票数不变而人格交换立场、稳定两轮、关键新证据未处理、不稳定超限、Agent 失败不能伪造票、扩展分析不影响原决议。这些用于帮助识别测试方向，**不可替代精确冻结版本的验收文本**。

## 4. 仓库与工具链（据此前用户运行报告）

- Python：**3.12.3**；项目使用 `src` 布局，根目录有 `pyproject.toml`、`.venv`。
- 相关目录：`src/magi/domain/`、`tests/`。
- 已初始化 `pytest` 和 `Ruff`；脚手架阶段曾报告 `pytest 1 passed`、Ruff 通过。
- 当前 `Ballot` 练习最近一次 pytest 报告：**5 passed**（见下文）。
- Windows + PowerShell + PyCharm 环境。建议从仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ballot.py -v
```

**导入注意：**项目内的 `src/magi/domain/types.py` 与 Python 标准库 `types` 同名。过去直接运行 `python src/magi/domain/ballot.py` 导致标准库导入命名冲突/循环导入；使用根目录模块运行或 pytest。若项目未以可编辑方式安装、模块导入失败，可先按现有配置检查 `PYTHONPATH`、`pyproject.toml` 和解释器。不要为修复此问题擅自重构领域规则。

## 5. 已由我本人编写并验证的代码（以仓库实际内容为准）

### 5.1 `src/magi/domain/types.py`

```python
from enum import Enum


class Persona(Enum):
    MELCHIOR = "MELCHIOR"
    BALTHASAR = "BALTHASAR"
    CASPER = "CASPER"
```

### 5.2 `src/magi/domain/ballot.py` 当前最小骨架

```python
from dataclasses import dataclass

from magi.domain.types import Persona


@dataclass(frozen=True)
class Ballot:
    persona: Persona

    def __post_init__(self):
        if not isinstance(self.persona, Persona):
            raise TypeError("Invalid persona type")
```

这只是阶段练习，**并不是正式 Ballot 的最终字段集或完整不变量**。当前尚未加入 `round`、A/B 投票选择、理由/证据等正式字段。字段名称与契约需核对已冻结 L1 设计，不可由 Codex 即兴补齐。

### 5.3 `tests/test_ballot.py` 已完成的测试形态

```python
from dataclasses import FrozenInstanceError

import pytest

from magi.domain.ballot import Ballot
from magi.domain.types import Persona


@pytest.mark.parametrize(
    "persona", [Persona.MELCHIOR, Persona.CASPER, Persona.BALTHASAR]
)
def test_persona(persona):
    ballot = Ballot(persona=persona)
    assert ballot.persona == persona


def test_invalid_persona():
    with pytest.raises(TypeError):
        Ballot(persona="HELLO")


def test_ballot_immutable():
    ballot = Ballot(persona=Persona.MELCHIOR)
    with pytest.raises(FrozenInstanceError):
        ballot.persona = Persona.CASPER
```

最近一次运行（用户提供）：

```text
collected 5 items
3 × test_persona PASSED
1 × test_invalid_persona PASSED
1 × test_ballot_immutable PASSED
5 passed
```

静态检查器会把 `test_ballot_immutable` 里对只读字段的赋值标红，因为这是故意触发 `FrozenInstanceError` 的测试；不能为了让 IDE 不标红而删除断言。此前曾写成 `ballot.persona == Persona.CASPER`（比较而非赋值）导致**假阳性**，已经纠正。

## 6. 我已经学过的 Python 与测试知识

我之前主要学习 Java（会写部分接口），Python 基础较弱。通过本轮练习已了解：

- `list` 与 `tuple`；元组不可变，`frozen=True` 不保证列表内部深度不可变。
- `Enum`：枚举类与成员、`.name`/`.value`，区分 `Persona.MELCHIOR` 与字符串 `"MELCHIOR"`。
- `@dataclass(frozen=True)`：自动生成初始化方法、限制字段重赋值、`FrozenInstanceError`。
- 类型注解不自动进行运行时校验；`__post_init__()` 初始化后的验证；`self` 与 Java `this` 的类比。
- `isinstance()`、`not`、`if`、`raise TypeError`、`try/except`。
- `is` 与 `==`（对象身份与字段值）；新建对象与引用同一个对象的区别。
- pytest 中 `test_`、`assert`、`pytest.raises`、`@pytest.mark.parametrize`，以及测试假阳性的原因。

**学习状态：**能在提示下写出以上基础代码，但尚不能假定已精通 Python、类型系统或测试设计。对学过的语法不必每轮重新讲；出现实际误用时再点拨。多使用 Java 对比帮助理解。

## 7. 下一步唯一任务：定位冻结的 L1 领域设计原文

**L1-CODE-001-I 已完成。** `Ballot` 已由学习者本人加入 `round: int` 字段，使用局部不变量保证轮次只能为 **1 或 2**，非法值抛出 `ValueError`；原有 persona 校验保持不变。

最新验证结果：

- `tests/test_ballot.py`：**9 passed**。
- 全量 pytest：**10 passed**。
- 合法轮次 `1`、`2` 与非法轮次 `0`、`3` 均已有有效测试。

Ruff 格式整理已经完成，只改变空格、空行和导入格式，没有修改业务行为。2026-10-17 复验结果为全量 pytest **10 passed**，`ruff check` 和 `ruff format --check` 均通过。

仓库当前没有冻结的 L1 领域设计/职责边界原文。下一步先定位该原始文档，从中确认 Ballot 的下一项字段或不变量，再继续教学与编码；不得依据交接摘要猜测未列明的字段、异常契约或业务规则。

## 8. Codex 协作协议（务必遵守）

1. 角色是**导师、Code Reviewer 与测试解释者**；我自己是主要开发者。
2. **每轮只讨论一个问题/知识点，只布置一个最小编码任务**。不要一次输出多个后续任务、整段教学计划或完整业务实现。
3. 新知识点按“必要的 Python 语法 → 极小的非业务示例 → MAGI 中的用途 → 我动手 → Review”推进。优先讲理解和运行机制。
4. **默认仅查看文件、解释、提出修改建议**；未经我明确要求，不直接编辑领域源码、生成整套实现、提交 Git 或大规模重构。可以在我要求时运行已有测试并解释结果。
5. 我提交代码后，请检查语法、Python 惯用写法、领域职责、边界条件与测试有效性；指出**具体错误、原因和最小修改方向**，优先让我自己修。
6. 发现 pytest 通过时，检查是否验证了真实行为，防止假阳性；明确区分静态检查警告、运行时异常和测试失败。
7. 必须尊重已冻结 L0/L1 规则。材料不足时说“不确定，需要查看原文”，不得重新设计规则或默认替我决策。
8. 当前实现**无 LLM、无数据库、无 API、无框架依赖**，领域纯逻辑先行。除非我明确切换阶段，不主动扩展基础设施。
9. 风格耐心、具体、不居高临下；已经掌握的语法可加快速度，但不要突然跳过所有基础。
10. **每次 Code Review 的结尾只留下一个具体的下一步动作或问题。**

## 9. 给 Codex 的会话启动词

> 请先阅读仓库中这份 MAGI 开发交接文档，再检查 `src/magi/domain/types.py`、`src/magi/domain/ballot.py`、`tests/test_ballot.py`、`pyproject.toml`，确认实际状态。你作为 Python、DDD 和 Agent 工程导师继续指导我，而不是代写。我已经自己完成 Persona 枚举、Ballot 最小骨架、运行时 persona 类型校验和 5 个通过的 pytest 用例。请从 **L1-CODE-001-I：添加 round 字段并限制轮次为 1 或 2** 接续。先用我能理解的 Python/Java 对比解释必要语法，然后让我自己动手；一次只讨论一个问题。不要提前改代码、增加业务字段或展开完整实现。冻结的领域规则以最新 L0/L1 文档为准，缺失时先查证，不要自行补造。

---

**记录原则：**此交接文档是时间点快照。每完成一个小任务，由我决定是否更新“最后通过的测试、最近一次代码、下一步唯一任务”，并保留已经冻结的产品设计不被改写。
