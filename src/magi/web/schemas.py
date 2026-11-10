from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TurnInput(Request):
    issue_enabled: bool = Field(default=False, strict=True)
    memory_recall: bool = Field(default=False, strict=True)
    web_enabled: bool = Field(default=False, strict=True)
    chat_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    message_id: str = Field(min_length=1, max_length=120)
    reply_message_id: str = Field(min_length=1, max_length=120)
    parent_reply_message_id: str | None = Field(default=None, max_length=120)
    user_text: str = Field(min_length=1, max_length=8000)
    target_plan_id: str | None = Field(default=None, max_length=120)
    expected_version: int | None = Field(default=None, ge=1)
    time_budget_minutes: int | None = Field(default=None, ge=1, le=10080)


class SourceArtifact(Request):
    artifact_id: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1, strict=True)
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ResearchExecution(Request):
    """研究执行身份由用户选择并随消息冻结，模型不能临时换负责人。"""

    lead_agent_id: Literal["melchior", "balthasar", "casper"]
    reviewer_ids: list[Literal["melchior", "balthasar", "casper"]] = Field(
        default_factory=list, max_length=2
    )
    deliverable: Literal["report"] = "report"
    entry_mode: Literal["persona", "room"]
    shared_message_ids: list[str] = Field(default_factory=list, max_length=20)
    source_artifact: SourceArtifact | None = None

    @model_validator(mode="after")
    def validate_participants(self):
        """评审身份唯一且不能包含负责人；后续评审必须实际选择评审者。"""

        if len(set(self.reviewer_ids)) != len(self.reviewer_ids):
            raise ValueError("reviewer_ids must be unique")
        if self.lead_agent_id in self.reviewer_ids:
            raise ValueError("lead agent cannot review its own report")
        if self.source_artifact is not None and not self.reviewer_ids:
            raise ValueError("follow-up review requires reviewers")
        return self


class TaskSubmission(Request):
    """后台任务冻结消息和执行身份；旧聊天请求保持兼容。"""

    mode: Literal["chat", "council", "research"]
    turn: TurnInput
    execution: ResearchExecution | None = None

    @model_validator(mode="after")
    def validate_mode(self):
        if self.mode == "research" and self.execution is None:
            raise ValueError("research mode requires execution")
        if self.mode != "research" and self.execution is not None:
            raise ValueError("execution is only valid in research mode")
        return self


class ArtifactDecision(Request):
    request_id: str = Field(min_length=1, max_length=120)
    expected_version: int = Field(ge=1, strict=True)
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["approve", "reject"]


class IssueDecision(Request):
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["approve", "reject"]


class IssueReconcile(Request):
    issue_number: int = Field(strict=True, ge=1, le=2147483647)


class Confirmation(Request):
    chat_id: str = Field(min_length=1, max_length=80)
    reply_message_id: str = Field(min_length=1, max_length=120)
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class MemoryConfirmation(Request):
    """显式确认的记忆命令；请求ID由页面生成，重试必须保留。"""

    request_id: str = Field(min_length=1, max_length=120)
    confirmed: bool = Field(strict=True)

    @field_validator("confirmed")
    @classmethod
    def require_confirmation(cls, value):
        """拒绝未确认请求，不把缺省值、字符串或数字当作用户确认。"""
        if not value:
            raise ValueError("explicit confirmation required")
        return value


class MemoryCreate(MemoryConfirmation):
    """带时区的截止时间可选，空值表示长期有效。"""

    key: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=4000)
    expires_at: AwareDatetime | None = None


class MemoryVersion(MemoryConfirmation):
    expected_version: int = Field(ge=1, strict=True)


class MemoryUpdate(MemoryVersion):
    """更新正文或有效期。字段缺省时沿用原有效期。"""

    value: str = Field(min_length=1, max_length=4000)
    expires_at: AwareDatetime | None = None


class MemorySuggestionConfirm(Request):
    """候选编号已绑定固定操作ID；仅允许用户编辑内容并明确确认。"""

    value: str = Field(min_length=1, max_length=4000)
    confirmed: bool = Field(strict=True)

    @field_validator("confirmed")
    @classmethod
    def require_confirmation(cls, value):
        if not value:
            raise ValueError("explicit confirmation required")
        return value


class DocumentPreview(Request):
    """预览请求必须指定不可变计划版本；请求ID供断线重试去重。"""

    request_id: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1, strict=True)


class DocumentConfirm(Request):
    """明确批准或拒绝，绑定冻结摘要；中断编号可空以兼容旧客户端。"""

    confirmed: bool = Field(strict=True)
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    interrupt_id: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("confirmed")
    @classmethod
    def require_confirmation(cls, value):
        """缺失、false或非布尔值均不能授权工具写文件。"""
        if not value:
            raise ValueError("explicit confirmation required")
        return value


class ExecutionRecord(Request):
    """用户明确保存执行观察，revision用于并发检查，不是计划版本。"""

    request_id: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1, strict=True)
    expected_revision: int = Field(ge=0, strict=True)
    status: Literal["NOT_STARTED", "IN_PROGRESS", "BLOCKED", "COMPLETED"]
    note: str = Field(min_length=1, max_length=2000)
    confirmed: bool = Field(strict=True)
