import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from magi.council.service import Candidate, Council, candidate_body, render
from magi.harness.runtime import open_personal_session
from magi.personal.context import ContextService
from magi.personal.models import PersonalError
from magi.personal.plans import PlanService
from magi.personal.store import PersonalStore


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


class WebError(Exception):
    def __init__(self, code, message, status=409, run_id=None):
        super().__init__(message)
        self.code, self.status = code, status
        self.run_id = run_id


class WebService:
    def __init__(self, settings, owner, *, council=None, session_factory=None):
        self.settings, self.owner = settings, owner
        self.store = PersonalStore(settings.business_db_path)
        self.plans = PlanService(self.store)
        self.context = ContextService(self.store)
        self.gate = Lock()
        self.council = council or Council(settings)
        self.session_factory = session_factory or open_personal_session

    def recover(self):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE web_turns SET status='INTERRUPTED',text='服务中断；请新发消息，不自动重跑。' WHERE status='RUNNING'"
            )
        # Completed business results may need checkpoint repair; never rerun models.
        with self.store.connect() as db:
            pending = db.execute(
                "SELECT * FROM web_turns WHERE synced=0 AND status='COMPLETED'"
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
        return result

    def history(self, chat):
        with self.store.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,message,user_text,text,status,proposal_id,reply FROM web_turns WHERE owner=? AND chat=? ORDER BY created_at,id",
                    (self.owner, chat),
                )
            ]

    def _sync(self, turn):
        binding = self.context.resolve_binding(self.logical_id(turn["chat"]))
        # Do not resurrect a completed old-epoch result in the new context.
        if binding.epoch == turn["epoch"]:
            result = turn.get("result")
            if result is None and turn.get("result_json"):
                result = json.loads(turn["result_json"])
            # Only the bounded synthesis continues the conversation. Full persona
            # opinions remain in the business store and display history.
            continuation = turn["text"]
            if result and result.get("candidate"):
                continuation = (
                    "三人格综合（候选，保存事实请查询计划）：\n"
                    + candidate_body(Candidate(**result["candidate"]))
                )
            with self.session_factory(
                self.settings, self.logical_id(turn["chat"])
            ) as session:
                session.agent.update_state(
                    session.config,
                    {
                        "messages": [
                            HumanMessage(
                                content=turn["user_text"], id=turn["id"] + "-user"
                            ),
                            AIMessage(
                                content=continuation[:2200],
                                id=turn["id"] + "-assistant",
                            ),
                        ]
                    },
                )
        with self.store.transaction() as db:
            db.execute("UPDATE web_turns SET synced=1 WHERE id=?", (turn["id"],))

    def turn(self, request, mode):
        data = request.model_dump()
        signature = digest([data, mode])
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
                    "SELECT * FROM web_turns WHERE synced=0 AND status='COMPLETED'"
                ).fetchall()
            for row in pending:
                self._sync(dict(row))
            logical = self.logical_id(request.chat_id)
            binding, memories = self.context.build_current_context(logical)
            with self.store.transaction() as db:
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
            if mode == "chat":
                if request.target_plan_id or request.expected_version:
                    raise WebError("INVALID_INPUT", "反馈修订请使用三人格模式", 422)
                with self.session_factory(self.settings, logical) as session:
                    text = session.ask(request.user_text)
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
                    if session.agent.get_state(session.config).next:
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
                if old:
                    snapshot["old_plan"]["created_at"] = old.created_at.isoformat()

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
                        "body": candidate_body(candidate),
                        "target_id": request.target_plan_id,
                        "expected_version": request.expected_version,
                    }
                    proposal["payload_hash"] = digest(
                        {**proposal, "epoch": binding.epoch}
                    )
                    text += f"\n\n候选 ID：{proposal['id']}\n摘要：{proposal['payload_hash']}\n请点击确认保存；当前尚未保存。"
            with self.store.transaction() as db:
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
                db.execute(
                    "UPDATE web_turns SET status=?,text=?,result_json=?,proposal_id=?,synced=? WHERE id=?",
                    (
                        status,
                        text,
                        json.dumps(result, ensure_ascii=False),
                        proposal["id"] if proposal else None,
                        synced,
                        turn_id,
                    ),
                )
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
                            "处理失败：" + type(exc).__name__ + "；请新发消息重试。",
                            turn_id,
                        ),
                    )
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
