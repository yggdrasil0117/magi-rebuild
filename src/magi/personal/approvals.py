"""工作单生产审批：框架保存暂停位置，业务服务保存决定并核对文件。"""

import hashlib
import json
from contextlib import contextmanager
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from langsmith import tracing_context

from magi.personal.models import PersonalError


class ApprovalState(TypedDict, total=False):
    """确定性动作没有模型消息，只有被冻结的目标、决定和业务结果。"""

    document_id: str
    payload_hash: str
    decision: str
    created: bool


class DocumentApprovals:
    def __init__(self, documents):
        """与业务库同目录使用独立checkpoint；单worker调用方负责串行修改。"""
        self.documents = documents
        self.store, self.owner = documents.store, documents.owner
        self.checkpoint_path = self.store.path.with_suffix(".approvals.sqlite3")

    def _record(self, document_id):
        """owner来自服务端，读取决定前先按业务对象检查身份。"""
        self.documents.get(document_id)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM document_approvals WHERE document_id=? AND owner=?",
                (document_id, self.owner),
            ).fetchone()
        return dict(row) if row else None

    def _config(self, document_id):
        """内部状态指针由owner与文档派生，不接受客户端提供thread_id。"""
        key = hashlib.sha256(json.dumps([self.owner, document_id]).encode()).hexdigest()
        return {"configurable": {"thread_id": "document-" + key}}

    @contextmanager
    def _graph(self):
        """复用LangGraph中断/SQLite持久化，不自研等待循环或引入额外模型。"""

        def wait_for_decision(state):
            """中断前无业务副作用；恢复时核对决定确实由后端先行提交。"""
            decision = interrupt(
                {
                    "document_id": state["document_id"],
                    "payload_hash": state["payload_hash"],
                }
            )
            record = self._record(state["document_id"])
            if (
                not record
                or decision not in {"approve", "reject"}
                or record["decision"] != decision
                or record["payload_hash"] != state["payload_hash"]
            ):
                raise PersonalError("APPROVAL_CONFLICT", "审批决定与已保存记录不一致")
            return {"decision": decision}

        def execute(state):
            """只在持久批准后调用原文件服务；拒绝不创建文件。"""
            if state["decision"] == "reject":
                return {"created": False}
            record = self._record(state["document_id"])
            if (
                not record
                or record["decision"] != "approve"
                or record["payload_hash"] != state["payload_hash"]
            ):
                raise PersonalError("APPROVAL_CONFLICT", "缺少匹配的持久批准")
            result = self.documents.confirm(state["document_id"], state["payload_hash"])
            return {"created": result["verified"]}

        graph = StateGraph(ApprovalState)
        graph.add_node("wait_for_decision", wait_for_decision)
        graph.add_node("execute", execute)
        graph.add_edge(START, "wait_for_decision")
        graph.add_edge("wait_for_decision", "execute")
        graph.add_edge("execute", END)
        with (
            SqliteSaver.from_conn_string(str(self.checkpoint_path)) as saver,
            tracing_context(enabled=False),
        ):
            yield graph.compile(checkpointer=saver)

    def status(self, document_id):
        """只读业务状态，不恢复或重放执行；CREATED是历史事实，确认时才核对文件。"""
        saved = self.documents.get(document_id)
        record = self._record(document_id)
        if saved["status"] == "CREATED":
            status = "CREATED"
        elif not record:
            status = "NOT_STARTED"
        elif record["decision"] == "reject":
            status = "REJECTED"
        elif record["error_code"]:
            status = "BLOCKED"
        elif record["decision"] == "approve":
            status = "APPROVED"
        else:
            status = "PENDING"
        return {
            "status": status,
            "interrupt_id": record["interrupt_id"] if record else None,
            "decision": record["decision"] if record else None,
            "error_code": record["error_code"] if record else None,
        }

    def prepare(self, document_id):
        """创建唯一待审流程；旧版预览可显式初始化，已有决定不会被重置。"""
        saved = self.documents.get(document_id)
        if saved["status"] == "CREATED":
            return self.status(document_id)
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO document_approvals(document_id,owner,payload_hash) VALUES (?,?,?)",
                (document_id, self.owner, saved["payload_hash"]),
            )
        record = self._record(document_id)
        if record["decision"] is not None or record["error_code"]:
            return self.status(document_id)
        with self._graph() as graph:
            config = self._config(document_id)
            snapshot = graph.get_state(config)
            if not snapshot.values:
                graph.invoke(
                    {"document_id": document_id, "payload_hash": saved["payload_hash"]},
                    config,
                )
                snapshot = graph.get_state(config)
            pending = [item for task in snapshot.tasks for item in task.interrupts]
            if len(pending) != 1 or pending[0].value != {
                "document_id": document_id,
                "payload_hash": saved["payload_hash"],
            }:
                raise PersonalError(
                    "APPROVAL_BLOCKED", "待审批流程状态不一致，请重新准备预览"
                )
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE document_approvals SET interrupt_id=? WHERE document_id=? AND owner=? AND decision IS NULL",
                    (pending[0].id, document_id, self.owner),
                )
        return self.status(document_id)

    def decide(self, document_id, payload_hash, decision, interrupt_id=None):
        """先提交唯一决定，再恢复框架；仅明确重试可恢复已批准而中断的执行。"""
        if decision not in {"approve", "reject"}:
            raise PersonalError("INVALID_INPUT", "只允许批准或拒绝")
        saved = self.documents.get(document_id)
        if payload_hash != saved["payload_hash"]:
            raise PersonalError("IDEMPOTENCY_CONFLICT", "确认内容与预览不一致")
        record = self._record(document_id)
        if record and record["decision"] not in {None, decision}:
            raise PersonalError(
                "APPROVAL_CONFLICT", "该工作单已经作出相反决定，请准备新预览"
            )
        if saved["status"] == "CREATED":
            if decision == "reject":
                raise PersonalError("APPROVAL_CONFLICT", "已创建文件不能通过拒绝撤销")
            return self.documents.confirm(document_id, payload_hash)
        self.prepare(document_id)
        record = self._record(document_id)
        if interrupt_id is not None and interrupt_id != record["interrupt_id"]:
            raise PersonalError("STALE_APPROVAL", "审批编号不匹配当前工作单")
        if record["decision"] == "reject":
            return saved
        if record["error_code"]:
            raise PersonalError(
                "APPROVAL_BLOCKED", "执行已受阻，不自动重放；请检查文件或准备新预览"
            )
        with self.store.transaction() as db:
            db.execute(
                "UPDATE document_approvals SET decision=? WHERE document_id=? AND owner=? AND decision IS NULL",
                (decision, document_id, self.owner),
            )
        try:
            with self._graph() as graph:
                config = self._config(document_id)
                snapshot = graph.get_state(config)
                pending = [item for task in snapshot.tasks for item in task.interrupts]
                if pending:
                    graph.invoke(Command(resume=decision), config)
                elif snapshot.next:
                    graph.invoke(None, config)
                elif (
                    decision == "approve"
                    and self.documents.get(document_id)["status"] != "CREATED"
                ):
                    raise PersonalError(
                        "APPROVAL_BLOCKED", "流程结束但没有业务创建记录"
                    )
        except Exception as exc:
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE document_approvals SET error_code=? WHERE document_id=? AND owner=?",
                    (getattr(exc, "code", "EXECUTION_FAILED"), document_id, self.owner),
                )
            if isinstance(exc, (PersonalError, OSError)):
                raise
            raise PersonalError(
                "APPROVAL_BLOCKED", "审批执行未完成，请检查实际业务状态"
            ) from exc
        result = self.documents.get(document_id)
        return (
            {**result, "verified": True}
            if decision == "approve" and result["status"] == "CREATED"
            else result
        )
