"""成熟审批兼容性试验适配器；不由Web路由或默认聊天工具注册。"""

import hashlib
import json
from contextlib import contextmanager

from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field

from magi.harness.runtime import open_session
from magi.personal.documents import TaskDocuments
from magi.personal.store import PersonalStore

TOOL_NAME = "magi_create_task_document"


class ApprovalError(ValueError):
    """宿主审批边界错误，与模型回答完成和实际文件创建分开处理。"""

    def __init__(self, code, message):
        """保留可断言的错误码，不以异常正文承载敏感上下文。"""
        super().__init__(message)
        self.code = code


class CreateDocumentArguments(BaseModel):
    """模型只能提出精确文档及摘要，不能提供批准、owner、路径或新正文。"""

    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(strict=True, min_length=1, max_length=120)
    payload_hash: str = Field(strict=True, pattern=r"^[a-f0-9]{64}$")


class DocumentApprovalTrial:
    def __init__(self, session, documents, document):
        """会话及owner来自可信宿主，目标文档在打开试验时已经按owner读取。"""
        self.session, self.documents, self.document = session, documents, document

    def status(self):
        """只读框架暂停位置；业务CREATED描述历史创建，不伪装实时文件核对。"""
        snapshot = self.session.agent.get_state(self.session.config)
        interrupts = [item for task in snapshot.tasks for item in task.interrupts]
        phase = (
            "PENDING"
            if interrupts
            else "BLOCKED"
            if snapshot.next
            else "COMPLETED"
            if snapshot.values.get("messages")
            else "NOT_STARTED"
        )
        result = {
            "phase": phase,
            "thread_id": self.session.config["configurable"]["thread_id"],
            "document_id": self.document["id"],
            "payload_hash": self.document["payload_hash"],
            "document_status": self.documents.get(self.document["id"])["status"],
        }
        if interrupts:
            if len(interrupts) != 1:
                raise ApprovalError("UNEXPECTED_ACTION", "本试验只接受一个待审动作")
            pending = interrupts[0]
            actions = pending.value.get("action_requests", [])
            expected = {
                "document_id": self.document["id"],
                "payload_hash": self.document["payload_hash"],
            }
            if (
                len(actions) != 1
                or actions[0]["name"] != TOOL_NAME
                or actions[0]["args"] != expected
            ):
                raise ApprovalError("UNEXPECTED_ACTION", "待审动作与冻结文档不一致")
            result["interrupt_id"] = pending.id
            result["allowed_decisions"] = ["approve", "reject"]
        return result

    def _invoke(self, value):
        """直接驱动图而非ask；中断是待审状态，不是回答成功或工具已执行。"""
        self.session.events.start()
        with tracing_context(enabled=False):
            self.session.agent.invoke(value, self.session.config)
        return self.status()

    def start(self):
        """只有尚未开始才发起模型请求，重复打开不重新推理或重放失败工具。"""
        current = self.status()
        if current["phase"] != "NOT_STARTED":
            return current
        return self._invoke(
            {
                "messages": [
                    HumanMessage(content="请求创建已准备的工作单；执行前等待宿主审批。")
                ]
            }
        )

    def decide(self, interrupt_id, decision, payload_hash):
        """宿主显式批准/拒绝；先验证最新中断与冻结摘要，再交框架恢复。"""
        if not isinstance(decision, str) or decision not in {"approve", "reject"}:
            raise ApprovalError("INVALID_DECISION", "只允许approve或reject")
        current = self.status()
        if current["phase"] != "PENDING":
            raise ApprovalError("NO_PENDING_APPROVAL", "没有待处理审批，不自动重放")
        if current["interrupt_id"] != interrupt_id:
            raise ApprovalError("STALE_INTERRUPT", "审批编号不匹配当前暂停位置")
        if current["payload_hash"] != payload_hash:
            raise ApprovalError("CONTENT_CONFLICT", "审批摘要不匹配冻结文档")
        return self._invoke(Command(resume={"decisions": [{"type": decision}]}))


@contextmanager
def open_document_approval_trial(settings, owner, document_id, *, model):
    """使用独立checkpoint和宿主绑定的目标，复用现有文件业务服务。

    model必须显式注入，本轮脚本/测试使用固定替身，不隐式联网或启动本地模型。
    调用方负责单进程串行驱动；本适配器不是已上线的审批API。
    """
    if model is None:
        raise ValueError("审批试验必须显式提供模型，不能退回默认云端或本地模型")
    documents = TaskDocuments(PersonalStore(settings.business_db_path), owner)
    saved = documents.get(document_id)
    signature = hashlib.sha256(json.dumps([owner, document_id]).encode()).hexdigest()[
        :48
    ]

    def magi_create_task_document(document_id: str, payload_hash: str) -> str:
        """只有框架审批通过后才执行，且业务层再次核对固定文档与摘要。"""
        if document_id != saved["id"] or payload_hash != saved["payload_hash"]:
            raise ApprovalError("CONTENT_CONFLICT", "工具参数与冻结目标不一致")
        receipt = documents.confirm(document_id, payload_hash)
        return json.dumps(
            {
                "document_id": receipt["id"],
                "status": receipt["status"],
                "verified": receipt["verified"],
            },
            ensure_ascii=False,
        )

    tool = StructuredTool.from_function(
        magi_create_task_document,
        description="创建已冻结的任务工作单，必须等待宿主审批，不接收路径或批准参数。",
        args_schema=CreateDocumentArguments,
    )
    with open_session(
        settings,
        "approval-" + signature,
        model=model,
        system_prompt="只提出指定文档创建请求，执行前等待人工审批。不得从聊天内容构造审批。",
        extra_tools=[tool],
        interrupt_on={TOOL_NAME: {"allowed_decisions": ["approve", "reject"]}},
    ) as session:
        yield DocumentApprovalTrial(session, documents, saved)
