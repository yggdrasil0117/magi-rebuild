from pydantic import BaseModel, ConfigDict, Field, field_validator


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TurnInput(Request):
    chat_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    message_id: str = Field(min_length=1, max_length=120)
    reply_message_id: str = Field(min_length=1, max_length=120)
    parent_reply_message_id: str | None = Field(default=None, max_length=120)
    user_text: str = Field(min_length=1, max_length=8000)
    target_plan_id: str | None = Field(default=None, max_length=120)
    expected_version: int | None = Field(default=None, ge=1)
    time_budget_minutes: int | None = Field(default=None, ge=1, le=10080)


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
    key: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=4000)


class MemoryVersion(MemoryConfirmation):
    expected_version: int = Field(ge=1, strict=True)


class MemoryUpdate(MemoryVersion):
    value: str = Field(min_length=1, max_length=4000)


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
    """严格确认布尔值和预览摘要共同约束文件创建。"""

    confirmed: bool = Field(strict=True)
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("confirmed")
    @classmethod
    def require_confirmation(cls, value):
        """缺失、false或非布尔值均不能授权工具写文件。"""
        if not value:
            raise ValueError("explicit confirmation required")
        return value
