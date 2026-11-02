"""计划版本的用户执行记录：追加历史、乐观并发、幂等重放，不自动验证完成。"""

from datetime import datetime, timezone
from uuid import uuid4

from magi.personal.models import ErrorCode, PersonalError
from magi.personal.plans import _digest, _text

STATUSES = {"NOT_STARTED", "IN_PROGRESS", "BLOCKED", "COMPLETED"}


class PlanExecutions:
    def __init__(self, store, owner):
        """store为现有业务库，owner仅由后端身份注入，不接受模型自报。"""
        self.store, self.owner = store, owner

    @staticmethod
    def _require_plan(db, plan_id, version):
        """只给已保存计划版本记录进展；候选和不存在版本都不能作为执行对象。"""
        if type(version) is not int or version < 1:
            raise PersonalError(ErrorCode.INVALID_INPUT, "计划版本必须为正整数")
        if not db.execute(
            "SELECT 1 FROM plan_revisions WHERE plan_id=? AND version=?",
            (plan_id, version),
        ).fetchone():
            raise PersonalError(ErrorCode.NOT_FOUND, "计划版本不存在")

    def view(self, plan_id, version, *, limit=50):
        """返回此版本最新状态及倒序有界历史；无记录为未开始，不继承其他版本。"""
        if type(limit) is not int or not 1 <= limit <= 50:
            raise PersonalError(ErrorCode.INVALID_INPUT, "记录读取上限必须为1—50")
        with self.store.connect() as db:
            self._require_plan(db, plan_id, version)
            rows = db.execute(
                "SELECT event_id,revision,status,note,created_at FROM plan_execution_events WHERE owner=? AND plan_id=? AND plan_version=? ORDER BY revision DESC LIMIT ?",
                (self.owner, plan_id, version, limit),
            ).fetchall()
        events = [dict(row) for row in rows]
        revision = events[0]["revision"] if events else 0
        return dict(
            plan_id=plan_id,
            plan_version=version,
            status=events[0]["status"] if events else "NOT_STARTED",
            revision=revision,
            events=events,
            history_truncated=revision > len(events),
            provenance="用户自报执行记录，非程序自动核验",
        )

    def record(
        self,
        plan_id,
        version,
        *,
        request_id,
        expected_revision,
        status,
        note,
        confirmed,
    ):
        """明确点击记录后事务追加；同请求重放，过期revision拒绝，不修改计划版本。

        note为用户进展/阻塞/结果说明，完成也只代表用户自报。允许追加纠正或重新开始，旧记录不删除。
        """
        if (
            confirmed is not True
            or not isinstance(status, str)
            or status not in STATUSES
        ):
            raise PersonalError(
                ErrorCode.INVALID_INPUT, "必须明确确认并选择有效执行状态"
            )
        if type(expected_revision) is not int or expected_revision < 0:
            raise PersonalError(ErrorCode.INVALID_INPUT, "执行记录版本必须为非负整数")
        request_id = _text(request_id, "request_id", 120)
        note = _text(note, "进展或结果说明", 2000)
        data = dict(
            plan_id=plan_id,
            plan_version=version,
            expected_revision=expected_revision,
            status=status,
            note=note,
        )
        fingerprint = _digest(data)
        with self.store.transaction() as db:
            self._require_plan(db, plan_id, version)
            replay = db.execute(
                "SELECT * FROM plan_execution_events WHERE owner=? AND request_id=?",
                (self.owner, request_id),
            ).fetchone()
            if replay:
                if replay["request_digest"] != fingerprint:
                    raise PersonalError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "此请求编号已用于不同执行记录"
                    )
                return dict(
                    event_id=replay["event_id"],
                    revision=replay["revision"],
                    duplicate=True,
                    committed=True,
                )
            latest = db.execute(
                "SELECT COALESCE(MAX(revision),0) FROM plan_execution_events WHERE owner=? AND plan_id=? AND plan_version=?",
                (self.owner, plan_id, version),
            ).fetchone()[0]
            if latest != expected_revision:
                raise PersonalError(
                    ErrorCode.VERSION_CONFLICT, "执行记录已更新，请刷新后核对再提交"
                )
            event_id = uuid4().hex
            db.execute(
                "INSERT INTO plan_execution_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    self.owner,
                    request_id,
                    fingerprint,
                    plan_id,
                    version,
                    latest + 1,
                    status,
                    note,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return dict(
                event_id=event_id, revision=latest + 1, duplicate=False, committed=True
            )

    def context(self, plan_id, version):
        """取最近3条、每条最多300字给修订模型；保留裁剪标记，不扩充证据白名单。"""
        view = self.view(plan_id, version, limit=3)
        shortened = any(len(event["note"]) > 300 for event in view["events"])
        for event in view["events"]:
            event["note"] = event["note"][:300]
        view["notes_truncated"] = shortened
        return view

    def current_statuses(self):
        """一次查询当前计划版本的执行状态，供列表展示，避免逐计划加载历史。"""
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT e.plan_id,e.status FROM plan_execution_events e JOIN plans p ON p.id=e.plan_id AND p.current_version=e.plan_version WHERE e.owner=? AND e.revision=(SELECT MAX(x.revision) FROM plan_execution_events x WHERE x.owner=e.owner AND x.plan_id=e.plan_id AND x.plan_version=e.plan_version)",
                (self.owner,),
            ).fetchall()
        return {row["plan_id"]: row["status"] for row in rows}
