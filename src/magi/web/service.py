import hashlib
import json
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END

from magi.council.generation import failure_details, timeout_diagnostics
from magi.council.service import Candidate, Council, candidate_body, render
from magi.embedding import SiliconFlowEmbeddingProvider
from magi.harness.chat_loop import ChatCapacityError
from magi.harness.runtime import HarnessSession, WebChatSession
from magi.harness.runtime import open_web_session as open_personal_session
from magi.personal.approvals import DocumentApprovals
from magi.personal.context import ContextService
from magi.personal.documents import TaskDocuments
from magi.personal.executions import PlanExecutions
from magi.personal.extraction import MemoryExtractor
from magi.personal.memory import MemoryService
from magi.personal.models import PersonalError
from magi.personal.plans import PlanService
from magi.personal.store import PersonalStore
from magi.personal.suggestions import MemorySuggestions
from magi.research.service import ResearchService
from magi.retrieval import MagiLocalRetrievalProvider, RetrievalRequest
from magi.web.document_tools import build_document_tool


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def turn_signature(request, mode):
    """任务和同步入口共享请求身份，默认关闭的新增开关沿用旧摘要。"""
    data = request.model_dump()
    for field in ("web_enabled", "memory_recall", "issue_enabled"):
        if not data[field]:
            data.pop(field)
    return digest([data, mode])


class WebError(Exception):
    def __init__(self, code, message, status=409, run_id=None):
        super().__init__(message)
        self.code, self.status = code, status
        self.run_id = run_id


class WebService:
    def __init__(
        self,
        settings,
        owner,
        *,
        council=None,
        session_factory=None,
        memory_extractor=None,
    ):
        from magi.web.issues import IssueActions

        self.settings, self.owner = settings, owner
        self.store = PersonalStore(settings.business_db_path)
        self.plans = PlanService(self.store)
        self.executions = PlanExecutions(self.store, owner)
        self.memories = MemoryService(self.store)
        self.context = ContextService(self.store)
        self.gate = Lock()
        self.council = council or Council(settings)
        self.session_factory = session_factory or open_personal_session
        self.suggestions = MemorySuggestions(self.store, owner)
        self.memory_extractor = memory_extractor or MemoryExtractor(settings)
        self.extraction_gate = Lock()
        self.documents = TaskDocuments(self.store, owner)
        self.approvals = DocumentApprovals(self.documents)
        self.issues = IssueActions(self.store, owner)
        self.research = ResearchService(self.store, owner, settings)

    def document_view(self, document_id):
        """将持久审批状态附在预览上，GET只读取，不恢复任何执行节点。"""
        return {
            **self.documents.get(document_id),
            "approval": self.approvals.status(document_id),
        }

    def prepare_document(self, *args, **kwargs):
        """调用方已持串行门；手动和模型准备预览都初始化同一框架待审流程。"""
        saved = self.documents.preview(*args, **kwargs)
        self.approvals.prepare(saved["id"])
        return self.document_view(saved["id"])

    def document_action(self, action, *args):
        """文件操作与业务修改串行；失败明确报告，是否重试由持久审批状态决定。"""
        with self.gate:
            try:
                if action not in {"preview", "confirm", "reject"}:
                    raise WebError("INVALID_INPUT", "未知工作单操作", 422)
                if action == "preview":
                    return self.prepare_document(*args)
                document_id, payload_hash, *extra = args
                result = self.approvals.decide(
                    document_id,
                    payload_hash,
                    "approve" if action == "confirm" else "reject",
                    *extra,
                )
                return {**result, "approval": self.approvals.status(document_id)}
            except OSError as exc:
                raise WebError(
                    "DOCUMENT_IO_ERROR", "文件操作未完成，请检查输出目录后重试", 409
                ) from exc

    def propose_memory(self, turn_id):
        """回答后的独立有界提取；失败不改原回答、不重放，模型等待不占聊天串行门。"""
        turn = self.get_turn(turn_id)
        if turn["status"] != "COMPLETED":
            raise WebError("INVALID_INPUT", "只有已完成回复可提出记忆建议", 422)
        if self.suggestions.run_status(turn_id)["status"] != "NOT_STARTED":
            return self.suggestions.run_status(turn_id)
        if not self.extraction_gate.acquire(blocking=False):
            self.suggestions.skip(turn, "SKIPPED_BUSY")
            return self.suggestions.run_status(turn_id)
        try:
            if not self.gate.acquire(blocking=False):
                self.suggestions.skip(turn, "SKIPPED_BUSY")
                return self.suggestions.run_status(turn_id)
            try:
                snapshot = self.suggestions.start(turn)
            finally:
                self.gate.release()
            if snapshot is not None:
                diagnostics = {"calls": 1}
                started = time.monotonic()
                try:
                    candidate = self.memory_extractor.extract(snapshot, diagnostics)
                    if time.monotonic() - started > min(
                        20, self.settings.timeout_seconds
                    ):
                        raise TimeoutError("memory extraction deadline exceeded")
                    diagnostics["seconds"] = round(time.monotonic() - started, 3)
                    self.suggestions.finish(turn, snapshot, candidate, diagnostics)
                except Exception as exc:
                    diagnostics["error_code"], diagnostics["validation_errors"] = (
                        failure_details(exc)
                    )
                    diagnostics["seconds"] = round(time.monotonic() - started, 3)
                    if diagnostics["error_code"] == "TIMEOUT":
                        diagnostics.update(timeout_diagnostics(exc))
                    self.suggestions.fail(turn_id, diagnostics)
            return self.suggestions.run_status(turn_id)
        finally:
            self.extraction_gate.release()

    def decide_memory_suggestion(self, suggestion_id, value=None):
        """确认或忽略候选与已有写操作共用串行门；确认才推进有效上下文。"""
        if not self.gate.acquire(blocking=False):
            raise WebError("BUSY", "正在处理其他任务，请稍后确认记忆", 429)
        try:
            if value is None:
                return self.suggestions.dismiss(suggestion_id)
            return self.suggestions.confirm(suggestion_id, value)
        finally:
            self.gate.release()

    def change_memory(self, action, command, memory_id=None):
        """显式网页操作复用记忆事务，与审议共享串行门，避免生成中途改变背景。

        网页只管理全局记忆；不把聊天文本或模型建议自动提升为有效记忆。
        请求ID绑定当前用户并隔离CLI命名空间，重复请求复用原回执。
        """
        if command.confirmed is not True:
            raise WebError("INVALID_INPUT", "请先确认记忆修改", 422)
        if not self.gate.acquire(blocking=False):
            raise WebError("BUSY", "正在处理其他任务，请完成后再修改记忆", 429)
        try:
            request_id = "web-memory:" + digest([self.owner, command.request_id])
            if action == "add":
                receipt = self.memories.add(
                    key=command.key,
                    scope="global",
                    value=command.value,
                    request_id=request_id,
                    source="explicit_web",
                    expires_at=command.expires_at,
                )
            else:
                if self.memories.get(memory_id).scope != "global":
                    raise WebError("INVALID_INPUT", "网页仅管理全局记忆", 422)
                params = dict(
                    expected_version=command.expected_version, request_id=request_id
                )
                if action == "update":
                    if "expires_at" in command.model_fields_set:
                        params["expires_at"] = command.expires_at
                    receipt = self.memories.update(
                        memory_id, value=command.value, **params
                    )
                elif action == "delete":
                    receipt = self.memories.delete(memory_id, **params)
                else:
                    raise WebError("INVALID_INPUT", "不支持的记忆操作", 422)
            return {
                "receipt": asdict(receipt),
                "context_epoch": self.memories.current_epoch(),
            }
        finally:
            self.gate.release()

    def recover(self):
        self.issues.recover()
        self.suggestions.recover()
        with self.store.transaction() as db:
            db.execute(
                "UPDATE web_turns SET status='INTERRUPTED',text='服务中断；请新发消息，不自动重跑。' WHERE status='RUNNING'"
            )
        # Completed business results may need checkpoint repair; never rerun models.
        with self.store.connect() as db:
            pending = db.execute(
                "SELECT * FROM web_turns WHERE owner=? AND synced=0 AND status='COMPLETED' ORDER BY created_at,id",
                (self.owner,),
            ).fetchall()
        for turn in pending:
            self._sync(dict(turn))

    def logical_id(self, chat):
        return "web-" + digest([self.owner, chat])[:60]

    def get_turn(self, turn_id):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM web_turns WHERE id=? AND owner=?", (turn_id, self.owner)
            ).fetchone()
        if row is None:
            raise WebError("NOT_FOUND", "turn not found", 404)
        result = dict(row)
        result["result"] = json.loads(result.pop("result_json") or "null")
        result["run_id"] = result["id"] if result["mode"] == "council" else None
        result["documents"] = self.documents.for_turn(turn_id)
        result["issues"] = self.issues.for_turn(turn_id)
        return result

    def turn_status(self, chat, message_id):
        """只查本用户的持久轮次，尚未登记时返回空，避免把查询当作提交。"""
        with self.store.connect() as db:
            row = db.execute(
                """SELECT id, status, created_at FROM web_turns
                   WHERE owner=? AND chat=? AND message=?""",
                (self.owner, chat, message_id),
            ).fetchone()
        return {"turn": dict(row) if row is not None else None}

    def history(self, chat):
        """恢复当前owner会话及真实工具产物，不能根据模型回答推断文件已创建。"""
        with self.store.connect() as db:
            turns = [
                dict(r)
                for r in db.execute(
                    "SELECT id,message,user_text,text,status,proposal_id,reply,result_json FROM web_turns WHERE owner=? AND chat=? ORDER BY created_at,id",
                    (self.owner, chat),
                )
            ]
            by_turn = {}
            issues = {
                row["turn_id"]: dict(row)
                for row in db.execute(
                    """SELECT a.id,a.status,a.turn_id FROM issue_actions a
                       JOIN web_turns t ON t.id=a.turn_id
                       WHERE a.owner=? AND t.owner=? AND t.chat=?""",
                    (self.owner, self.owner, chat),
                )
            }
            for row in db.execute(
                "SELECT d.id,d.plan_id,d.version,d.status,d.source_turn_id FROM task_documents d JOIN web_turns t ON t.id=d.source_turn_id WHERE d.owner=? AND t.owner=? AND t.chat=? ORDER BY d.rowid",
                (self.owner, self.owner, chat),
            ):
                item = dict(row)
                by_turn.setdefault(item.pop("source_turn_id"), []).append(item)
            for turn in turns:
                # 历史只附冻结资料，不把全部内部诊断强制展示给普通用户。
                stored = json.loads(turn.pop("result_json") or "null") or {}
                turn["retrieval"] = stored.get("snapshot", {}).get("retrieval")
                turn["memory_recall"] = stored.get("memory_recall")
                turn["web_research"] = stored.get("web_research", [])
                turn["documents"] = by_turn.get(turn["id"], [])
                turn["issues"] = [issues[turn["id"]]] if turn["id"] in issues else []
            return turns

    def _sync(self, turn):
        """将已完成审议续接到有效聊天历史，不运行日常模型。

        turn来自持久业务轮次；同epoch才写入，稳定消息ID允许中断后重写。
        写消息与结束图调度均成功才标记synced，避免恢复时漏修checkpoint。
        """
        binding = self.context.resolve_binding(self.logical_id(turn["chat"]))
        # Do not resurrect a completed old-epoch result in the new context.
        if binding.epoch == turn["epoch"]:
            result = turn.get("result")
            if result is None and turn.get("result_json"):
                result = json.loads(turn["result_json"])
            # Only the bounded synthesis continues the conversation. Full persona
            # opinions remain in the business store and display history.
            messages = self._council_exchange(
                turn["id"], turn["user_text"], turn["text"], result
            )
            with self.session_factory(
                self.settings, self.logical_id(turn["chat"])
            ) as session:
                if isinstance(session, WebChatSession):
                    session.messages()  # 先导入旧历史，再补写已有审议结果。
                    with self.store.transaction() as db:
                        updated = db.execute(
                            "UPDATE web_turns SET synced=1 WHERE id=? AND synced=0",
                            (turn["id"],),
                        )
                        epoch = db.execute(
                            "SELECT context_epoch FROM app_state WHERE singleton=1"
                        ).fetchone()[0]
                        if (
                            updated.rowcount
                            and epoch == session.history.binding.epoch == turn["epoch"]
                        ):
                            session.history.append(db, turn["id"], messages)
                    return
                if isinstance(session, HarnessSession):
                    session.save_messages(messages)
                else:
                    # 兼容已有演示脚本注入的会话适配器。
                    session.agent.update_state(
                        session.config,
                        {"messages": messages},
                    )
                    session.agent.update_state(session.config, None, as_node=END)
        with self.store.transaction() as db:
            db.execute("UPDATE web_turns SET synced=1 WHERE id=?", (turn["id"],))

    @staticmethod
    def _council_exchange(turn_id, question, text, result):
        """聊天只承接综合结论，完整意见仍由审议结果保管。"""
        if result and result.get("candidate"):
            text = "三人格综合（候选，保存事实请查询计划）：\n" + candidate_body(
                Candidate(**result["candidate"])
            )
        return [
            HumanMessage(content=question, id=turn_id + "-user"),
            AIMessage(content=text[:2200], id=turn_id + "-assistant"),
        ]

    def turn(self, request, mode, *, expected_epoch=None):
        if request.issue_enabled and mode != "chat":
            raise WebError("INVALID_INPUT", "Issue 草稿工具仅用于日常聊天", 422)
        if request.memory_recall and mode != "chat":
            raise WebError("INVALID_INPUT", "语义记忆召回请使用日常聊天", 422)
        if request.web_enabled and mode != "chat":
            raise WebError("INVALID_INPUT", "联网查资料请使用日常聊天模式", 422)
        signature = turn_signature(request, mode)
        with self.store.connect() as db:
            prior = db.execute(
                "SELECT id,digest FROM web_turns WHERE owner=? AND chat=? AND message=?",
                (self.owner, request.chat_id, request.message_id),
            ).fetchone()
        if prior:
            if prior["digest"] != signature:
                raise WebError(
                    "IDEMPOTENCY_CONFLICT", "同一消息 ID 的输入已改变；请新发消息"
                )
            return self.get_turn(prior["id"])
        if request.issue_enabled and not self.issues.repo:
            raise WebError("ISSUE_DISABLED", "服务未配置有效的 Issue 目标仓库", 422)
        if not self.gate.acquire(blocking=False):
            raise WebError("BUSY", "正在处理其他任务", 429)
        turn_id = uuid4().hex
        created = False
        try:
            previous = self.history(request.chat_id)
            if previous and request.parent_reply_message_id != previous[-1]["reply"]:
                raise WebError(
                    "BRANCH_CONFLICT", "请加载最新历史后顺序发送；旧消息分叉请新建聊天"
                )
            # Repair any durable completed council result before a new turn.
            with self.store.connect() as db:
                pending = db.execute(
                    "SELECT * FROM web_turns WHERE owner=? AND synced=0 AND status='COMPLETED' ORDER BY created_at,id",
                    (self.owner,),
                ).fetchall()
            for row in pending:
                self._sync(dict(row))
            logical = self.logical_id(request.chat_id)
            binding, memories = self.context.build_current_context(logical)
            with self.store.transaction() as db:
                if (
                    expected_epoch is None
                    and db.execute(
                        """SELECT 1 FROM agent_tasks WHERE owner=? AND chat=?
                       AND status IN ('QUEUED','RUNNING')""",
                        (self.owner, request.chat_id),
                    ).fetchone()
                ):
                    raise WebError("CHAT_TASK_ACTIVE", "此会话还有后台任务，请等待结果")
                if expected_epoch is not None:
                    current_epoch = db.execute(
                        "SELECT context_epoch FROM app_state WHERE singleton=1"
                    ).fetchone()[0]
                    if (
                        expected_epoch != current_epoch
                        or expected_epoch != binding.epoch
                    ):
                        raise WebError(
                            "CONTEXT_CHANGED", "排队期间背景已变化，请重新提交"
                        )
                db.execute(
                    "INSERT INTO web_turns(id,owner,chat,message,reply,digest,epoch,mode,user_text,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,'RUNNING',?)",
                    (
                        turn_id,
                        self.owner,
                        request.chat_id,
                        request.message_id,
                        request.reply_message_id,
                        signature,
                        binding.epoch,
                        mode,
                        request.user_text,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            created = True
            result, proposal, synced = None, None, 0
            native_history, exchange = None, None
            if mode == "chat":
                if request.target_plan_id or request.expected_version:
                    raise WebError("INVALID_INPUT", "反馈修订请使用三人格模式", 422)
                # 只给真实日常会话注入当前轮次工具；测试替身与三人格只读续接保持原约定。
                options = {}
                research = None
                recall_result = None
                if self.session_factory is open_personal_session:
                    options["compact_history"] = True
                    if request.memory_recall:
                        from magi.personal.recall import recall

                        recall_result = recall(
                            self.context, logical, request.user_text, self.settings
                        )
                        options["memory_selection"] = recall_result["items"]
                if self.session_factory is open_personal_session:
                    options["extra_tools"] = [
                        build_document_tool(
                            self.documents, turn_id, prepare=self.prepare_document
                        )
                    ]
                    if request.issue_enabled:
                        options["extra_tools"].extend(self.issues.tools(turn_id))
                if (
                    request.web_enabled
                    and self.session_factory is open_personal_session
                ):
                    from magi.web_research import WebResearch

                    def persist_web(records):
                        """独立保存实际工具结果，后续模型失败仍能查询来源。"""
                        with self.store.transaction() as db:
                            db.execute(
                                "UPDATE web_turns SET result_json=? WHERE id=?",
                                (
                                    json.dumps(
                                        {"web_research": records}, ensure_ascii=False
                                    ),
                                    turn_id,
                                ),
                            )

                    research = WebResearch(persist_web)
                    options["extra_tools"].extend(research.tools())
                with self.session_factory(self.settings, logical, **options) as session:
                    if isinstance(session, WebChatSession):
                        session.events.web_turn_id = turn_id
                        native_history = session.history
                        text, exchange = session.ask(request.user_text)
                    else:
                        text = session.ask(request.user_text)
                if research:
                    result = {"web_research": research.records}
                if recall_result is not None:
                    result = {**(result or {}), "memory_recall": recall_result}
                status, synced = "COMPLETED", 1
            else:
                old = None
                if request.target_plan_id:
                    old = self.plans.get(request.target_plan_id)
                    if old.needs_review or old.version != request.expected_version:
                        raise WebError(
                            "VERSION_CONFLICT", "旧计划版本或背景已变化，请复核后重试"
                        )
                elif request.expected_version is not None:
                    raise WebError(
                        "INVALID_INPUT", "expected_version requires target_plan_id", 422
                    )
                with self.session_factory(self.settings, logical) as session:
                    if isinstance(session, WebChatSession):
                        native_history = session.history
                        pending = False
                    else:
                        pending = session.agent.get_state(session.config).next
                    if pending:
                        raise WebError(
                            "INTERRUPTED_CONTEXT", "此会话有未完成推理，请新建聊天继续"
                        )
                    history = [
                        {"role": m.type, "text": m.text}
                        for m in session.messages()[-12:]
                        if m.type in {"human", "ai"} and m.text
                    ]
                snapshot = {
                    "run_id": turn_id,
                    "logical_session_id": logical,
                    "epoch": binding.epoch,
                    "question": request.user_text,
                    "time_budget_minutes": request.time_budget_minutes,
                    "memories": memories,
                    "history": history,
                    "old_plan": asdict(old) if old else None,
                    "evidence_ids": ["user:current"]
                    + ["memory:" + m["key"] for m in memories],
                }
                # 读取一次并随web_turn持久化；确认计划保留对该回合的来源引用。
                snapshot["retrieval"] = MagiLocalRetrievalProvider(
                    self.settings.docs_root,
                    self.settings.business_db_path.with_name("retrieval.db"),
                    embedding=SiliconFlowEmbeddingProvider()
                    if self.settings.embedding_enabled
                    else None,
                    min_score=self.settings.embedding_min_score,
                ).retrieve(RetrievalRequest(request.user_text))
                snapshot["evidence_ids"] += [
                    item["evidence_ref"] for item in snapshot["retrieval"]["matches"]
                ]
                if old:
                    snapshot["old_plan"]["created_at"] = old.created_at.isoformat()
                    snapshot["old_plan"]["execution"] = self.executions.context(
                        old.id, old.version
                    )

                def progress(state):
                    state = {
                        **state,
                        "snapshot": snapshot,
                        "snapshot_hash": digest(snapshot),
                        "model": self.settings.model,
                        "provider": self.settings.provider,
                    }
                    with self.store.transaction() as db:
                        db.execute(
                            "UPDATE web_turns SET result_json=? WHERE id=?",
                            (json.dumps(state, ensure_ascii=False), turn_id),
                        )

                result = self.council.run(snapshot, progress=progress)
                result["snapshot"] = snapshot
                result["snapshot_hash"] = digest(snapshot)
                result["model"] = self.settings.model
                result["provider"] = self.settings.provider
                text, status = render(result), result["status"]
                if result["candidate"] and status == "COMPLETED":
                    candidate = Candidate(**result["candidate"])
                    proposal = {
                        "id": uuid4().hex,
                        "title": candidate.title,
                        "body": candidate_body(candidate, snapshot),
                        "target_id": request.target_plan_id,
                        "expected_version": request.expected_version,
                    }
                    proposal["payload_hash"] = digest(
                        {**proposal, "epoch": binding.epoch}
                    )
                    text += f"\n\n候选 ID：{proposal['id']}\n摘要：{proposal['payload_hash']}\n请点击确认保存；当前尚未保存。"
                if native_history is not None and status == "COMPLETED":
                    exchange = self._council_exchange(
                        turn_id, request.user_text, text, result
                    )
                    synced = 1
            with self.store.transaction() as db:
                if exchange is not None:
                    epoch = db.execute(
                        "SELECT context_epoch FROM app_state WHERE singleton=1"
                    ).fetchone()[0]
                    if epoch != binding.epoch or epoch != native_history.binding.epoch:
                        raise WebError("CONTEXT_CHANGED", "背景已变化，请新发消息")
                    native_history.append(db, turn_id, exchange)
                if proposal:
                    db.execute(
                        "INSERT INTO plan_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            proposal["id"],
                            turn_id,
                            self.owner,
                            request.chat_id,
                            request.reply_message_id,
                            binding.epoch,
                            proposal["title"],
                            proposal["body"],
                            proposal["payload_hash"],
                            proposal["target_id"],
                            proposal["expected_version"],
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                updated = db.execute(
                    "UPDATE web_turns SET status=?,text=?,result_json=?,proposal_id=?,synced=? WHERE id=? AND status='RUNNING'",
                    (
                        status,
                        text,
                        json.dumps(result, ensure_ascii=False),
                        proposal["id"] if proposal else None,
                        synced,
                        turn_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise WebError("TURN_NOT_RUNNING", "本轮已结束，请刷新聊天")
            if status == "COMPLETED" and not synced:
                self._sync({**self.get_turn(turn_id)})
            return self.get_turn(turn_id)
        except Exception as exc:
            if created:
                with self.store.transaction() as db:
                    # Keep a completed result for checkpoint repair, never rerun it.
                    db.execute(
                        "UPDATE web_turns SET status='FAILED',text=? WHERE id=? AND status='RUNNING'",
                        (
                            str(exc)
                            if isinstance(exc, ChatCapacityError)
                            else "处理失败："
                            + type(exc).__name__
                            + "；请新发消息重试。",
                            turn_id,
                        ),
                    )
            if isinstance(exc, ChatCapacityError):
                raise WebError(exc.code, str(exc), 422, run_id=turn_id) from exc
            if isinstance(exc, (WebError, PersonalError)):
                if created:
                    exc.run_id = turn_id
                raise
            if isinstance(exc, sqlite3.IntegrityError):
                raise WebError(
                    "IDENTIFIER_CONFLICT", "消息或回复 ID 已被使用，请新发消息"
                ) from exc
            raise WebError(
                "MODEL_OR_STORAGE_FAILURE",
                "处理未完成，请查询该消息状态或新发消息",
                502,
                run_id=turn_id if created else None,
            ) from exc
        finally:
            self.gate.release()

    def proposal(self, proposal_id, chat, reply):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM plan_proposals WHERE id=? AND owner=? AND chat=? AND reply=?",
                (proposal_id, self.owner, chat, reply),
            ).fetchone()
        if row is None:
            raise WebError("NOT_FOUND", "候选与当前用户、会话或回复不匹配", 404)
        # 保存状态来自已提交操作，而不是聊天中的“已保存”文字；刷新后也可恢复。
        with self.store.connect() as db:
            saved = db.execute(
                "SELECT result_id AS object_id,result_version,operation_id FROM operations "
                "WHERE request_id=? AND status='SUCCEEDED'",
                ("proposal:" + proposal_id + ":save",),
            ).fetchone()
        return {**dict(row), "saved": dict(saved) if saved else None}

    def confirm(self, proposal_id, confirmation):
        if not self.gate.acquire(blocking=False):
            raise WebError("BUSY", "正在处理其他任务", 429)
        try:
            p = self.proposal(
                proposal_id, confirmation.chat_id, confirmation.reply_message_id
            )
            if p["payload_hash"] != confirmation.payload_hash:
                raise WebError("PAYLOAD_MISMATCH", "候选摘要不匹配")
            if p["target_id"] and not p["saved"]:
                # 生成后用户可能又记录了新进展；不能把旧执行上下文静默当成当前依据。
                # 已保存请求必须仍可幂等重放，不因后续进展变化撤销真实提交回执。
                frozen = self.get_turn(p["turn_id"])["result"] or {}
                execution = ((frozen.get("snapshot") or {}).get("old_plan") or {}).get(
                    "execution"
                )
                if (
                    execution is not None
                    and self.executions.view(p["target_id"], p["expected_version"])[
                        "revision"
                    ]
                    != execution["revision"]
                ):
                    raise WebError(
                        "VERSION_CONFLICT",
                        "执行记录已变化，请基于最新进展重新生成修订后确认",
                    )
            params = dict(
                title=p["title"],
                body=p["body"],
                sources=["web_turn:" + p["turn_id"]],
                request_id="proposal:" + p["id"] + ":save",
                source_epoch=p["epoch"],
                source="confirmed_web_proposal",
            )
            if p["target_id"]:
                receipt = self.plans.update(
                    p["target_id"], expected_version=p["expected_version"], **params
                )
            else:
                receipt = self.plans.create(**params)
            saved = self.plans.get(receipt.object_id, receipt.result_version)
            if saved.body != p["body"] or saved.title != p["title"]:
                raise RuntimeError("saved content differs from proposal")
            return asdict(receipt)
        finally:
            self.gate.release()
