from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    ACTIVE_KEY_CONFLICT = "ACTIVE_KEY_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"


class PersonalError(ValueError):
    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, kw_only=True)
class Memory:
    id: str
    key: str
    scope: str
    value: str | None
    status: str
    version: int
    source: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True, kw_only=True)
class Receipt:
    operation_id: str
    request_id: str
    action: str
    object_id: str
    result_version: int
    committed: bool
    duplicate: bool = False


@dataclass(frozen=True, kw_only=True)
class CurrentBinding:
    logical_session_id: str
    epoch: int
    thread_id: str


@dataclass(frozen=True, kw_only=True)
class PlanView:
    id: str
    title: str
    version: int
    body: str
    sources: tuple[str, ...]
    source: str
    source_epoch: int
    created_at: datetime
    needs_review: bool


@dataclass(frozen=True, kw_only=True)
class PlanSummary:
    id: str
    title: str
    version: int
    needs_review: bool
