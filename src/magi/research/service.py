"""研究任务组织器：持久化每一阶段，并执行一次修改、两轮评估的硬上限。"""

import json
from datetime import datetime, timezone
from uuid import uuid4

from magi.personal.artifacts import ArtifactError, ResearchArtifacts
from magi.personas import get_persona
from magi.research.budget import ResearchBudget
from magi.research.runner import BudgetExhausted, PersonaRunner, ResearchOutputError


def now():
    return datetime.now(timezone.utc).isoformat()


class ResearchTaskError(ValueError):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.status = code, status


class ResearchService:
    def __init__(self, store, owner, settings, *, runner=None):
        self.store, self.owner, self.settings = store, owner, settings
        self.runner = runner or PersonaRunner(settings)
        self.artifacts = ResearchArtifacts(store, owner)

    def bind_submission(self, db, turn, execution):
        """首次提交固定入口类型；私聊负责人随后不能被请求悄悄替换。"""

        lead = get_persona(execution.lead_agent_id)
        row = db.execute(
            "SELECT * FROM research_chats WHERE owner=? AND chat=?",
            (self.owner, turn.chat_id),
        ).fetchone()
        expected_agent = lead.agent_id if execution.entry_mode == "persona" else None
        if row is None:
            db.execute(
                "INSERT INTO research_chats VALUES (?,?,?,?,?)",
                (
                    self.owner,
                    turn.chat_id,
                    execution.entry_mode,
                    expected_agent,
                    now(),
                ),
            )
        elif row["entry_mode"] != execution.entry_mode or (
            row["entry_mode"] == "persona" and row["agent_id"] != lead.agent_id
        ):
            raise ResearchTaskError(
                "CHAT_BINDING_CONFLICT", "该研究会话已绑定其他入口或人格"
            )
        source = execution.source_artifact
        if source is not None:
            try:
                artifact = self.artifacts.get(source.artifact_id, source.version)
            except ArtifactError as exc:
                raise ResearchTaskError(exc.code, str(exc), 404) from exc
            if (
                artifact["payload_hash"] != source.payload_hash
                or artifact["current_version"] != source.version
                or artifact["lead_agent_id"] != lead.agent_id
            ):
                raise ResearchTaskError(
                    "VERSION_CONFLICT", "后续评审绑定的成果版本、摘要或负责人不一致"
                )
        # 共享消息必须是当前用户可读的已完成输入，不能只凭客户端给出 ID。
        for message_id in execution.shared_message_ids:
            visible = db.execute(
                """SELECT 1 FROM web_turns
                   WHERE owner=? AND message=? AND status='COMPLETED'""",
                (self.owner, message_id),
            ).fetchone()
            if visible is None:
                raise ResearchTaskError(
                    "INVALID_SHARE", "共享消息不存在或尚未完成", 422
                )

    def _context(self, task, turn, execution):
        """装配冻结输入；默认只读同一研究会话，额外私聊必须逐条显式分享。"""

        with self.store.connect() as db:
            memory_total = db.execute(
                "SELECT COUNT(*) FROM memories WHERE status='ACTIVE'"
            ).fetchone()[0]
            memories = [
                {
                    "id": row["id"],
                    "version": row["version"],
                    "key": row["key"],
                    "value": row["value"],
                }
                for row in db.execute(
                    """SELECT id,version,key,value FROM memories
                       WHERE status='ACTIVE' ORDER BY updated_at DESC LIMIT 20"""
                )
            ]
            previous = []
            previous_total = db.execute(
                """SELECT COUNT(*) FROM agent_tasks t
                   WHERE t.owner=? AND t.chat=? AND t.mode='research'
                     AND t.status IN ('COMPLETED','PARTIAL') AND t.id<>?""",
                (self.owner, turn.chat_id, task["id"]),
            ).fetchone()[0]
            for row in db.execute(
                """SELECT t.id,t.request_json,t.artifact_id FROM agent_tasks t
                   WHERE t.owner=? AND t.chat=? AND t.mode='research'
                     AND t.status IN ('COMPLETED','PARTIAL') AND t.id<>?
                   ORDER BY t.updated_at DESC LIMIT 6""",
                (self.owner, turn.chat_id, task["id"]),
            ):
                request = json.loads(row["request_json"])
                item = {"task_id": row["id"], "request": request["user_text"]}
                if row["artifact_id"]:
                    artifact = self.artifacts.get(row["artifact_id"])
                    item["artifact"] = {
                        "artifact_id": artifact["id"],
                        "version": artifact["version"],
                        "title": artifact["title"],
                        "completed_items": artifact["completed"],
                        "unknowns": artifact["unknowns"],
                    }
                previous.append(item)
            shared = [
                {
                    "message_id": row["message"],
                    "chat": row["chat"],
                    "user_text": row["user_text"],
                    "answer": row["text"][:2000],
                }
                for message_id in execution.shared_message_ids
                for row in db.execute(
                    """SELECT message,chat,user_text,text FROM web_turns
                       WHERE owner=? AND message=? AND status='COMPLETED'""",
                    (self.owner, message_id),
                )
            ]
        context = {
            "task_id": task["id"],
            "request": turn.user_text,
            "permissions": {
                "read_authorized_files": True,
                "web_enabled": turn.web_enabled,
                "write_files": False,
                "install_dependencies": False,
            },
            "lead_agent_id": execution.lead_agent_id,
            "confirmed_memories": memories,
            "same_chat_history": list(reversed(previous)),
            "explicitly_shared_messages": shared,
            "selection": {
                "active_memories_total": memory_total,
                "same_chat_tasks_total": previous_total,
                "omitted_memories": max(0, memory_total - len(memories)),
                "omitted_history": max(0, previous_total - len(previous)),
                "omission_reason": "固定候选上限",
            },
        }
        size = len(json.dumps(context, ensure_ascii=False).encode()) + 4096
        # 旧会话和公共记忆是可选材料；用户本次要求、权限和显式分享是必带材料。
        # 接近硬上限时先删最旧历史，再删最早更新的记忆，并记录数量。
        while size > self.settings.num_ctx and context["same_chat_history"]:
            context["same_chat_history"].pop(0)
            context["selection"]["omitted_history"] += 1
            context["selection"]["omission_reason"] = "容量预算先省略最旧会话"
            size = len(json.dumps(context, ensure_ascii=False).encode()) + 4096
        while size > self.settings.num_ctx and context["confirmed_memories"]:
            context["confirmed_memories"].pop()
            context["selection"]["omitted_memories"] += 1
            context["selection"]["omission_reason"] = "容量预算再省略较早记忆"
            size = len(json.dumps(context, ensure_ascii=False).encode()) + 4096
        context["capacity"] = {
            "estimate_bytes": size,
            "limit": self.settings.num_ctx,
            "estimator": "utf8-bytes-plus-4096-reserve",
        }
        if size > self.settings.num_ctx:
            raise ResearchTaskError(
                "CONTEXT_LIMIT", "本阶段必带材料超过容量，未静默截断", 422
            )
        return context

    def _start_run(
        self, task_id, agent_id, stage, round_number, artifact_id, version, manifest
    ):
        run_id, timestamp = uuid4().hex, now()
        persona = get_persona(agent_id)
        with self.store.transaction() as db:
            db.execute(
                """INSERT INTO agent_runs
                   (id,owner,task_id,agent_id,config_version,stage,review_round,
                    target_artifact_id,target_version,status,input_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,'RUNNING',?,?,?)""",
                (
                    run_id,
                    self.owner,
                    task_id,
                    agent_id,
                    persona.config_version,
                    stage,
                    round_number,
                    artifact_id,
                    version,
                    json.dumps(manifest, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
            db.execute(
                "UPDATE agent_tasks SET stage=?,updated_at=? WHERE id=? AND status='RUNNING'",
                (stage, timestamp, task_id),
            )
        return run_id

    def _finish_run(self, run_id, output, model_calls, tool_calls):
        with self.store.transaction() as db:
            db.execute(
                """UPDATE agent_runs SET status='COMPLETED',output_json=?,
                   model_calls=?,tool_calls=?,updated_at=? WHERE id=?""",
                (
                    json.dumps(output, ensure_ascii=False),
                    model_calls,
                    tool_calls,
                    now(),
                    run_id,
                ),
            )

    @staticmethod
    def _failure_usage(exc):
        return getattr(exc, "model_calls", 0), getattr(exc, "tool_calls", 0)

    def _fail_run(self, run_id, exc):
        model_calls, tool_calls = self._failure_usage(exc)
        with self.store.transaction() as db:
            db.execute(
                """UPDATE agent_runs SET status='FAILED',diagnostics_json=?,
                   model_calls=?,tool_calls=?,updated_at=?
                   WHERE id=? AND status='RUNNING'""",
                (
                    json.dumps(
                        {"error_type": type(exc).__name__, "message": str(exc)[:1000]},
                        ensure_ascii=False,
                    ),
                    model_calls,
                    tool_calls,
                    now(),
                    run_id,
                ),
            )

    def _task_update(self, task_id, *, artifact_id, review_status, budget, stop_reason):
        with self.store.transaction() as db:
            db.execute(
                """UPDATE agent_tasks SET artifact_id=?,review_status=?,stop_reason=?,
                   budget_json=?,updated_at=? WHERE id=?""",
                (
                    artifact_id,
                    review_status,
                    stop_reason,
                    json.dumps(budget.view(), ensure_ascii=False),
                    now(),
                    task_id,
                ),
            )

    def _save_review(
        self, task_id, artifact, run_id, agent_id, round_number, result, sources
    ):
        review_id = uuid4().hex
        payload = result.model_dump()
        for index, finding in enumerate(payload["findings"], 1):
            finding["finding_id"] = f"{review_id}-f{index}"
        payload["new_sources"] = sources
        with self.store.transaction() as db:
            db.execute(
                """INSERT INTO research_reviews
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    review_id,
                    self.owner,
                    task_id,
                    artifact["id"],
                    artifact["version"],
                    run_id,
                    agent_id,
                    round_number,
                    result.verdict,
                    json.dumps(payload, ensure_ascii=False),
                    now(),
                ),
            )
        return payload

    def _review_round(self, task, execution, artifact, context, budget, round_number):
        results = []
        stage = "review" if round_number == 1 else "verify"
        for agent_id in execution.reviewer_ids:
            allowance = budget.allowance(stage, len(execution.reviewer_ids))
            if allowance[0] < 1:
                raise BudgetExhausted("评审阶段模型额度已耗尽")
            review_context = {
                "original_request": context["request"],
                "artifact": {
                    "artifact_id": artifact["id"],
                    "version": artifact["version"],
                    "payload_hash": artifact["payload_hash"],
                    "title": artifact["title"],
                    "body": artifact["body"],
                    "completed_items": artifact["completed"],
                    "unknowns": artifact["unknowns"],
                },
                "sources": artifact["sources"],
                "required_checks": [
                    "requirements",
                    "evidence",
                    "completed_work",
                    "unknowns",
                    "permissions",
                ],
            }
            if round_number == 2:
                review_context["previous_findings_and_handling"] = context["handling"]
            run_id = self._start_run(
                task["id"],
                agent_id,
                stage,
                round_number,
                artifact["id"],
                artifact["version"],
                {
                    "materials": ["request", "artifact", "sources"],
                    "artifact_hash": artifact["payload_hash"],
                    "capacity_estimate": len(
                        json.dumps(review_context, ensure_ascii=False).encode()
                    ),
                },
            )
            try:
                result, sources, _messages, models, tools = self.runner.review(
                    run_id,
                    agent_id,
                    review_context,
                    allowance,
                    web_enabled=context["permissions"]["web_enabled"],
                    stage=stage,
                    deadline=budget.deadline,
                )
                budget.consume(stage, models, tools)
                payload = self._save_review(
                    task["id"],
                    artifact,
                    run_id,
                    agent_id,
                    round_number,
                    result,
                    sources,
                )
                self._finish_run(run_id, payload, models, tools)
                results.append(
                    payload | {"agent_id": agent_id, "verdict": result.verdict}
                )
            except Exception as exc:
                models, tools = self._failure_usage(exc)
                budget.consume(stage, models, tools)
                self._fail_run(run_id, exc)
                raise
        return results

    def run_task(self, task, turn, execution):
        """运行完整研究闭环；任何分支最多一次修改和两轮评估。"""

        collaborative = bool(execution.reviewer_ids)
        budget = ResearchBudget(collaborative=collaborative)
        artifact = None
        try:
            context = self._context(task, turn, execution)
            if execution.source_artifact is None:
                allowance = budget.allowance("generate")
                run_id = self._start_run(
                    task["id"],
                    execution.lead_agent_id,
                    "generate",
                    0,
                    None,
                    None,
                    {
                        "materials": [
                            "request",
                            "permissions",
                            "confirmed_memories",
                            "same_chat_history",
                            "explicitly_shared_messages",
                        ],
                        "capacity": context["capacity"],
                    },
                )
                try:
                    report, sources, _messages, models, tools = self.runner.generate(
                        run_id,
                        execution.lead_agent_id,
                        context,
                        allowance,
                        web_enabled=turn.web_enabled,
                        deadline=budget.deadline,
                    )
                    budget.consume("generate", models, tools)
                    review_status = "IN_PROGRESS" if collaborative else "NOT_REQUESTED"
                    artifact = self.artifacts.create(
                        turn.chat_id,
                        execution.lead_agent_id,
                        report,
                        sources,
                        run_id,
                        review_status,
                    )
                    self._finish_run(
                        run_id,
                        {"artifact_id": artifact["id"], "version": 1},
                        models,
                        tools,
                    )
                except Exception as exc:
                    models, tools = self._failure_usage(exc)
                    budget.consume("generate", models, tools)
                    self._fail_run(run_id, exc)
                    raise
            else:
                artifact = self.artifacts.get(
                    execution.source_artifact.artifact_id,
                    execution.source_artifact.version,
                )
                self.artifacts.set_review_status(
                    artifact["id"], artifact["version"], "IN_PROGRESS"
                )
                artifact = self.artifacts.get(artifact["id"], artifact["version"])
            self._task_update(
                task["id"],
                artifact_id=artifact["id"],
                review_status=artifact["review_status"],
                budget=budget,
                stop_reason=None,
            )
            if not collaborative:
                return {
                    "status": "COMPLETED",
                    "artifact_id": artifact["id"],
                    "review_status": "NOT_REQUESTED",
                    "stop_reason": "DELIVERED",
                    "budget": budget.view(),
                }

            first = self._review_round(task, execution, artifact, context, budget, 1)
            if all(result["verdict"] == "PASS" for result in first):
                self.artifacts.set_review_status(
                    artifact["id"], artifact["version"], "PASSED"
                )
                return {
                    "status": "COMPLETED",
                    "artifact_id": artifact["id"],
                    "review_status": "PASSED",
                    "stop_reason": "REVIEW_PASSED",
                    "budget": budget.view(),
                }

            findings = [finding for result in first for finding in result["findings"]]
            new_sources = [
                source for result in first for source in result["new_sources"]
            ]
            allowance = budget.allowance("revise")
            if allowance[0] < 1:
                raise BudgetExhausted("没有剩余修改额度")
            revise_context = {
                "request": context["request"],
                "report": {
                    "title": artifact["title"],
                    "body": artifact["body"],
                    "completed_items": artifact["completed"],
                    "unknowns": artifact["unknowns"],
                },
                "sources": artifact["sources"] + new_sources,
                "findings": findings,
            }
            run_id = self._start_run(
                task["id"],
                execution.lead_agent_id,
                "revise",
                1,
                artifact["id"],
                artifact["version"],
                {"materials": ["report", "all_reviews", "all_new_sources"]},
            )
            try:
                revision, extra_sources, _messages, models, tools = self.runner.revise(
                    run_id,
                    execution.lead_agent_id,
                    revise_context,
                    allowance,
                    deadline=budget.deadline,
                )
                budget.consume("revise", models, tools)
                artifact = self.artifacts.add_version(
                    artifact["id"],
                    artifact["version"],
                    revision.report,
                    revise_context["sources"] + extra_sources,
                    run_id,
                    "IN_PROGRESS",
                )
                handling = [item.model_dump() for item in revision.handling]
                self._finish_run(
                    run_id,
                    {
                        "artifact_id": artifact["id"],
                        "version": artifact["version"],
                        "handling": handling,
                    },
                    models,
                    tools,
                )
            except Exception as exc:
                models, tools = self._failure_usage(exc)
                budget.consume("revise", models, tools)
                self._fail_run(run_id, exc)
                raise
            verify_context = context | {"handling": handling}
            second = self._review_round(
                task, execution, artifact, verify_context, budget, 2
            )
            passed = all(result["verdict"] == "PASS" for result in second)
            review_status = "PASSED" if passed else "UNRESOLVED"
            self.artifacts.set_review_status(
                artifact["id"], artifact["version"], review_status
            )
            return {
                "status": "COMPLETED" if passed else "PARTIAL",
                "artifact_id": artifact["id"],
                "review_status": review_status,
                "stop_reason": "REVIEW_PASSED" if passed else "REVISION_LIMIT",
                "budget": budget.view(),
            }
        except (BudgetExhausted, ResearchOutputError) as exc:
            status = "PARTIAL" if artifact else "FAILED"
            review_status = "INCOMPLETE" if artifact else None
            if artifact:
                self.artifacts.set_review_status(
                    artifact["id"], artifact["version"], review_status
                )
            return {
                "status": status,
                "artifact_id": artifact["id"] if artifact else None,
                "review_status": review_status,
                "stop_reason": "BUDGET_EXHAUSTED"
                if isinstance(exc, BudgetExhausted)
                else "INVALID_MODEL_OUTPUT",
                "error_code": type(exc).__name__,
                "budget": budget.view(),
            }
        except ResearchTaskError as exc:
            return {
                "status": "PARTIAL" if artifact else "FAILED",
                "artifact_id": artifact["id"] if artifact else None,
                "review_status": "INCOMPLETE" if artifact else None,
                "stop_reason": exc.code,
                "error_code": exc.code,
                "budget": budget.view(),
            }
        except Exception as exc:
            if artifact:
                self.artifacts.set_review_status(
                    artifact["id"], artifact["version"], "INCOMPLETE"
                )
            return {
                "status": "PARTIAL" if artifact else "FAILED",
                "artifact_id": artifact["id"] if artifact else None,
                "review_status": "INCOMPLETE" if artifact else None,
                "stop_reason": "RESEARCH_EXECUTION_FAILED",
                "error_code": type(exc).__name__,
                "budget": budget.view(),
            }
