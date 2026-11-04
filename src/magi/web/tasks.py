"""单进程任务队列：保留未领取工作，按轮次回执恢复已领取工作。"""

import json
import logging
from datetime import datetime, timezone
from threading import Event, Thread
from uuid import uuid4

from magi.web.schemas import ResearchExecution, TurnInput
from magi.web.service import WebError, turn_signature

PUBLIC_FIELDS = (
    "id,chat,message,mode,status,turn_id,error_code,created_at,updated_at,"
    "artifact_id,stage,review_status,stop_reason,budget_json,execution_json"
)


def now():
    return datetime.now(timezone.utc).isoformat()


class AgentTasks:
    def __init__(self, service):
        self.service = service
        self.store = service.store
        self.owner = service.owner
        self.wake = Event()
        self.stop = Event()

    def submit(self, request, mode, execution=None):
        """同一消息及执行身份重试返回原任务，提交后才通知执行器。"""
        payload = request.model_dump_json()
        execution_payload = (
            execution.model_dump_json() if execution is not None else None
        )
        with self.store.transaction() as db:
            old = db.execute(
                "SELECT * FROM agent_tasks WHERE owner=? AND chat=? AND message=?",
                (self.owner, request.chat_id, request.message_id),
            ).fetchone()
            if old:
                if (
                    old["mode"] != mode
                    or json.loads(old["request_json"]) != json.loads(payload)
                    or json.loads(old["execution_json"] or "null")
                    != json.loads(execution_payload or "null")
                ):
                    raise WebError("IDEMPOTENCY_CONFLICT", "同一消息的任务参数已改变")
                task_id = old["id"]
            else:
                active = db.execute(
                    """SELECT 1 FROM agent_tasks WHERE owner=? AND chat=?
                       AND status IN ('QUEUED','RUNNING')""",
                    (self.owner, request.chat_id),
                ).fetchone()
                if active:
                    raise WebError("CHAT_TASK_ACTIVE", "此会话还有待处理任务", 409)
                running_turn = db.execute(
                    """SELECT 1 FROM web_turns WHERE owner=? AND chat=?
                       AND status='RUNNING'""",
                    (self.owner, request.chat_id),
                ).fetchone()
                if running_turn:
                    raise WebError("CHAT_TASK_ACTIVE", "此会话还有正在生成的消息", 409)
                count = db.execute(
                    "SELECT COUNT(*) FROM agent_tasks WHERE owner=? AND status='QUEUED'",
                    (self.owner,),
                ).fetchone()[0]
                if count >= 32:
                    raise WebError("TASK_QUEUE_FULL", "任务队列已满，请稍后再试", 429)
                epoch = db.execute(
                    "SELECT context_epoch FROM app_state WHERE singleton=1"
                ).fetchone()[0]
                if mode == "research":
                    if execution is None:
                        raise WebError("INVALID_INPUT", "研究任务缺少执行身份", 422)
                    try:
                        self.service.research.bind_submission(db, request, execution)
                    except Exception as exc:
                        if hasattr(exc, "code"):
                            raise WebError(
                                exc.code, str(exc), getattr(exc, "status", 409)
                            ) from exc
                        raise
                    if execution.source_artifact is not None:
                        active_artifact = db.execute(
                            """SELECT 1 FROM agent_tasks
                               WHERE owner=? AND artifact_id=?
                                 AND status IN ('QUEUED','RUNNING')""",
                            (self.owner, execution.source_artifact.artifact_id),
                        ).fetchone()
                        if active_artifact:
                            raise WebError(
                                "ARTIFACT_TASK_ACTIVE",
                                "这份成果已有修改或评审任务，请等待完成",
                                409,
                            )
                task_id, timestamp = uuid4().hex, now()
                db.execute(
                    """INSERT INTO agent_tasks
                       (id,owner,chat,message,mode,request_json,epoch,status,created_at,
                        updated_at,execution_json,artifact_id)
                       VALUES (?,?,?,?,?,?,?,'QUEUED',?,?,?,?)""",
                    (
                        task_id,
                        self.owner,
                        request.chat_id,
                        request.message_id,
                        mode,
                        payload,
                        epoch,
                        timestamp,
                        timestamp,
                        execution_payload,
                        execution.source_artifact.artifact_id
                        if execution is not None and execution.source_artifact
                        else None,
                    ),
                )
        self.wake.set()
        return self.get(task_id)

    def get(self, task_id):
        """只返回当前用户的任务元数据，回复通过关联轮次读取。"""
        with self.store.connect() as db:
            row = db.execute(
                f"SELECT {PUBLIC_FIELDS} FROM agent_tasks WHERE id=? AND owner=?",
                (task_id, self.owner),
            ).fetchone()
        if row is None:
            raise WebError("NOT_FOUND", "未找到该任务", 404)
        result = dict(row)
        result["budget"] = json.loads(result.pop("budget_json") or "null")
        result["execution"] = json.loads(result.pop("execution_json") or "null")
        return result

    def list_chat(self, chat):
        """刷新时找回当前会话最近的任务，优先保留仍在运行的记录。"""
        with self.store.connect() as db:
            rows = db.execute(
                f"""SELECT {PUBLIC_FIELDS} FROM agent_tasks WHERE owner=? AND chat=?
                    ORDER BY (status IN ('QUEUED','RUNNING')) DESC, created_at DESC LIMIT 20""",
                (self.owner, chat),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["budget"] = json.loads(item.pop("budget_json") or "null")
            item["execution"] = json.loads(item.pop("execution_json") or "null")
            results.append(item)
        return results

    def claim(self):
        """在短写事务内领取最早任务，网络执行发生在事务外。"""
        with self.store.connect() as db:
            pending = db.execute(
                "SELECT 1 FROM agent_tasks WHERE owner=? AND status='QUEUED' LIMIT 1",
                (self.owner,),
            ).fetchone()
        if not pending:
            return None
        with self.store.transaction() as db:
            task = db.execute(
                """SELECT * FROM agent_tasks WHERE owner=? AND status='QUEUED'
                   ORDER BY created_at,id LIMIT 1""",
                (self.owner,),
            ).fetchone()
            if task is not None:
                db.execute(
                    "UPDATE agent_tasks SET status='RUNNING',updated_at=? WHERE id=?",
                    (now(), task["id"]),
                )
        return task

    @staticmethod
    def matching_turn(db, task):
        """请求摘要一致才认领回执，避免误关联同步入口的同名消息。"""
        if task["mode"] == "research":
            return None
        request = TurnInput.model_validate_json(task["request_json"])
        return db.execute(
            """SELECT id,status FROM web_turns
               WHERE owner=? AND chat=? AND message=? AND digest=?""",
            (
                task["owner"],
                task["chat"],
                task["message"],
                turn_signature(request, task["mode"]),
            ),
        ).fetchone()

    @staticmethod
    def finish(db, task, status, turn_id=None, code=None):
        """只收尾已领取任务，不改动网页轮次或独立工具回执。"""
        db.execute(
            """UPDATE agent_tasks SET status=?,turn_id=?,error_code=?,updated_at=?
               WHERE id=? AND status='RUNNING'""",
            (status, turn_id, code, now(), task["id"]),
        )

    def recover(self):
        """在服务恢复轮次之后运行；未领取的继续排队，已领取的只核对回执。"""
        with self.store.transaction() as db:
            tasks = db.execute(
                "SELECT * FROM agent_tasks WHERE owner=? AND status='RUNNING'",
                (self.owner,),
            ).fetchall()
            for task in tasks:
                if task["mode"] == "research":
                    db.execute(
                        """UPDATE agent_runs SET status='INTERRUPTED',updated_at=?
                           WHERE task_id=? AND status='RUNNING'""",
                        (now(), task["id"]),
                    )
                    self.finish(db, task, "INTERRUPTED", code="WORKER_INTERRUPTED")
                    continue
                turn = self.matching_turn(db, task)
                status = turn["status"] if turn else None
                if status not in {"COMPLETED", "FAILED", "INTERRUPTED"}:
                    status = "FAILED" if turn and status != "RUNNING" else "INTERRUPTED"
                self.finish(
                    db,
                    task,
                    status,
                    turn["id"] if turn else None,
                    "WORKER_INTERRUPTED" if status == "INTERRUPTED" else None,
                )

    def run_one(self):
        """运行已领取任务，只有入口未接收的 BUSY 可以安全回队。"""
        task = self.claim()
        if task is None:
            return False
        code = None
        try:
            request = TurnInput.model_validate_json(task["request_json"])
            if task["mode"] == "research":
                execution = ResearchExecution.model_validate_json(
                    task["execution_json"]
                )
                with self.store.connect() as db:
                    epoch = db.execute(
                        "SELECT context_epoch FROM app_state WHERE singleton=1"
                    ).fetchone()[0]
                if epoch != task["epoch"]:
                    result = {
                        "status": "FAILED",
                        "artifact_id": task["artifact_id"],
                        "review_status": None,
                        "stop_reason": "CONTEXT_CHANGED",
                        "error_code": "CONTEXT_CHANGED",
                        "budget": None,
                    }
                elif not self.service.gate.acquire(blocking=False):
                    with self.store.transaction() as db:
                        self.finish(db, task, "QUEUED")
                    return False
                else:
                    try:
                        result = self.service.research.run_task(
                            task, request, execution
                        )
                    finally:
                        self.service.gate.release()
                with self.store.transaction() as db:
                    db.execute(
                        """UPDATE agent_tasks SET status=?,artifact_id=?,review_status=?,
                           stop_reason=?,error_code=?,budget_json=?,updated_at=?
                           WHERE id=? AND status='RUNNING'""",
                        (
                            result["status"],
                            result.get("artifact_id"),
                            result.get("review_status"),
                            result.get("stop_reason"),
                            result.get("error_code"),
                            json.dumps(result.get("budget"), ensure_ascii=False),
                            now(),
                            task["id"],
                        ),
                    )
                return True
            self.service.turn(request, task["mode"], expected_epoch=task["epoch"])
        except WebError as exc:
            code = exc.code
        except Exception:
            code = "TASK_EXECUTION_FAILED"
        with self.store.transaction() as db:
            turn = self.matching_turn(db, task)
            if (code == "BUSY" and turn is None) or (
                turn and turn["status"] == "RUNNING"
            ):
                self.finish(db, task, "QUEUED")
                return False
            status = turn["status"] if turn else None
            if status not in {"COMPLETED", "INTERRUPTED"}:
                status = "FAILED"
            self.finish(
                db,
                task,
                status,
                turn["id"] if turn else None,
                None if status == "COMPLETED" else code,
            )
        return True

    def start_worker(self):
        """应用锁持有期间运行一个执行线程，停止时由应用等待线程退出。"""
        self.stop.clear()

        def work():
            needs_recovery = False
            while not self.stop.is_set():
                try:
                    if needs_recovery:
                        self.recover()
                        needs_recovery = False
                    if self.run_one():
                        continue
                except Exception as exc:
                    needs_recovery = True
                    logging.getLogger(__name__).error(
                        "任务执行器存储错误：%s", type(exc).__name__
                    )
                self.wake.wait(0.5)
                self.wake.clear()

        worker = Thread(target=work, name="magi-agent-tasks", daemon=True)
        worker.start()
        return worker
