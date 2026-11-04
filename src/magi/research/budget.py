"""整个研究任务共享的有界预算，不能按人格偷偷重置。"""

from dataclasses import dataclass, field
from time import monotonic

STAGE_LIMITS = {
    "generate": (8, 12),
    "review": (6, 8),
    "revise": (4, 4),
    "verify": (6, 8),
}


@dataclass
class ResearchBudget:
    collaborative: bool
    model_used: int = 0
    tool_used: int = 0
    stage_model_used: dict[str, int] = field(default_factory=dict)
    stage_tool_used: dict[str, int] = field(default_factory=dict)
    started: float = field(default_factory=monotonic, repr=False)

    @property
    def deadline(self):
        return self.started + self.duration_limit

    @property
    def model_limit(self):
        return 24 if self.collaborative else 12

    @property
    def tool_limit(self):
        return 32 if self.collaborative else 16

    @property
    def duration_limit(self):
        return 360 if self.collaborative else 180

    def allowance(self, stage: str, participants: int = 1):
        """返回当前运行的可用额度；评审阶段在评审者之间平均分配。"""

        stage_models, stage_tools = STAGE_LIMITS[stage]
        model_cap, tool_cap = stage_models, stage_tools
        if stage in {"review", "verify"}:
            model_cap //= max(1, participants)
            tool_cap //= max(1, participants)
        models = min(
            model_cap,
            stage_models - self.stage_model_used.get(stage, 0),
            self.model_limit - self.model_used,
        )
        tools = min(
            tool_cap,
            stage_tools - self.stage_tool_used.get(stage, 0),
            self.tool_limit - self.tool_used,
        )
        if monotonic() >= self.deadline:
            models = tools = 0
        return max(0, models), max(0, tools)

    def consume(self, stage: str, model_calls: int, tool_calls: int):
        self.model_used += model_calls
        self.tool_used += tool_calls
        self.stage_model_used[stage] = self.stage_model_used.get(stage, 0) + model_calls
        self.stage_tool_used[stage] = self.stage_tool_used.get(stage, 0) + tool_calls

    def view(self):
        return {
            "model_calls": self.model_used,
            "model_limit": self.model_limit,
            "tool_calls": self.tool_used,
            "tool_limit": self.tool_limit,
            "stage_model_calls": self.stage_model_used,
            "stage_tool_calls": self.stage_tool_used,
            "duration_limit_seconds": self.duration_limit,
        }
