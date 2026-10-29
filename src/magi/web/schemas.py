from pydantic import BaseModel, ConfigDict, Field


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
