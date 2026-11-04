"""研究路径的结构化模型；程序校验结构，不把模型判断当作事实。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReportPayload(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=40000)
    completed_items: list[str] = Field(default_factory=list, max_length=30)
    unknowns: list[str] = Field(default_factory=list, max_length=30)


class ReviewCheck(StrictModel):
    name: Literal[
        "requirements", "evidence", "completed_work", "unknowns", "permissions"
    ]
    status: Literal["pass", "issue", "unknown"]
    reason: str = Field(min_length=1, max_length=1000)


class ReviewFindingInput(StrictModel):
    target: str = Field(min_length=1, max_length=500)
    issue_type: Literal[
        "MISSING_REQUIREMENT",
        "UNSUPPORTED_CLAIM",
        "REPEATED_WORK",
        "UNKNOWN_NOT_DISCLOSED",
        "PERMISSION_BOUNDARY",
        "PREFERENCE_MISMATCH",
        "OTHER",
    ]
    blocking: bool
    reason: str = Field(min_length=1, max_length=1500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    suggestion: str = Field(min_length=1, max_length=1500)


class ReviewPayload(StrictModel):
    verdict: Literal["PASS", "REVISE", "INSUFFICIENT_EVIDENCE"]
    checks: list[ReviewCheck] = Field(min_length=5, max_length=5)
    findings: list[ReviewFindingInput] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_semantics(self):
        """保证判定与阻塞问题一致，避免只相信模型给出的 PASS 字样。"""

        names = {item.name for item in self.checks}
        if len(names) != 5:
            raise ValueError("五项共同检查必须各出现一次")
        blocking = any(item.blocking for item in self.findings)
        if self.verdict == "PASS" and blocking:
            raise ValueError("PASS 不能包含阻塞问题")
        if self.verdict == "REVISE" and not self.findings:
            raise ValueError("REVISE 必须给出可处理问题")
        if self.verdict == "INSUFFICIENT_EVIDENCE" and not self.findings:
            raise ValueError("证据不足必须说明缺少什么")
        return self


class FindingHandling(StrictModel):
    finding_id: str = Field(min_length=1, max_length=120)
    action: Literal["accepted", "retained"]
    reason: str = Field(min_length=1, max_length=1000)
    location: str = Field(min_length=1, max_length=500)


class RevisionPayload(StrictModel):
    report: ReportPayload
    handling: list[FindingHandling]
