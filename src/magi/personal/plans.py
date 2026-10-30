"""Versioned plan service for explicit, local business operations."""

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from magi.personal.models import (
    ErrorCode,
    PersonalError,
    PlanSummary,
    PlanView,
    Receipt,
)
from magi.personal.store import PersonalStore

MAX_TITLE_LENGTH = 200
MAX_BODY_LENGTH = 50_000
MAX_SOURCES = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise PersonalError(
            ErrorCode.INVALID_INPUT,
            f"{name} must be non-empty and at most {maximum} characters",
        )
    return value.strip()


def _sources(values) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)) or len(values) > MAX_SOURCES:
        raise PersonalError(ErrorCode.INVALID_INPUT, "sources must be a bounded list")
    return tuple(_text(value, "source reference", 1000) for value in values)


def _digest(data: dict) -> str:
    canonical = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PlanService:
    def __init__(self, store: PersonalStore):
        self.store = store

    @staticmethod
    def _current_epoch(connection) -> int:
        return connection.execute(
            "SELECT context_epoch FROM app_state WHERE singleton=1"
        ).fetchone()[0]

    @staticmethod
    def _replay(connection, request_id: str, digest: str) -> Receipt | None:
        row = connection.execute(
            "SELECT * FROM operations WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        if row["request_digest"] != digest:
            raise PersonalError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "request_id already has different parameters",
            )
        return Receipt(
            operation_id=row["operation_id"],
            request_id=request_id,
            action=row["action"],
            object_id=row["result_id"],
            result_version=row["result_version"],
            committed=True,
            duplicate=True,
        )

    def create(
        self,
        *,
        title: str,
        body: str,
        sources=(),
        request_id: str,
        source_epoch: int,
        source: str = "explicit_cli:user_declared_sources",
    ) -> Receipt:
        if source not in {
            "explicit_cli:user_declared_sources",
            "confirmed_web_proposal",
        }:
            raise PersonalError(ErrorCode.INVALID_INPUT, "invalid plan source")
        title = _text(title, "title", MAX_TITLE_LENGTH)
        body = _text(body, "body", MAX_BODY_LENGTH)
        sources = _sources(sources)
        request_id = _text(request_id, "request_id", 120)
        if type(source_epoch) is not int or source_epoch < 0:
            raise PersonalError(
                ErrorCode.INVALID_INPUT, "source_epoch must be non-negative"
            )
        data = {
            "action": "plan.create",
            "title": title,
            "body": body,
            "sources": sources,
            "source_epoch": source_epoch,
        }
        # Preserve pre-Web CLI request digests across the schema upgrade.
        if source != "explicit_cli:user_declared_sources":
            data["source"] = source
        digest = _digest(data)
        with self.store.transaction() as connection:
            if replay := self._replay(connection, request_id, digest):
                return replay
            if source_epoch != self._current_epoch(connection):
                raise PersonalError(
                    ErrorCode.VERSION_CONFLICT,
                    "source_epoch is stale; rebuild or explicitly resubmit current content",
                )
            plan_id, operation_id, now = uuid4().hex, uuid4().hex, _now()
            connection.execute(
                "INSERT INTO plans VALUES (?,?,?,?,?)",
                (plan_id, title, 1, now, now),
            )
            self._insert_revision(
                connection,
                plan_id,
                1,
                title,
                body,
                sources,
                source_epoch,
                operation_id,
                now,
                source,
            )
            self._finish(
                connection,
                operation_id,
                request_id,
                "plan.create",
                None,
                digest,
                plan_id,
                1,
                now,
            )
            return Receipt(
                operation_id=operation_id,
                request_id=request_id,
                action="plan.create",
                object_id=plan_id,
                result_version=1,
                committed=True,
            )

    def update(
        self,
        plan_id: str,
        *,
        expected_version: int,
        title: str,
        body: str,
        sources=(),
        request_id: str,
        source_epoch: int,
        source: str = "explicit_cli:user_declared_sources",
    ) -> Receipt:
        if source not in {
            "explicit_cli:user_declared_sources",
            "confirmed_web_proposal",
        }:
            raise PersonalError(ErrorCode.INVALID_INPUT, "invalid plan source")
        title = _text(title, "title", MAX_TITLE_LENGTH)
        body = _text(body, "body", MAX_BODY_LENGTH)
        sources = _sources(sources)
        request_id = _text(request_id, "request_id", 120)
        if type(expected_version) is not int or expected_version < 1:
            raise PersonalError(
                ErrorCode.INVALID_INPUT, "expected_version must be positive"
            )
        if type(source_epoch) is not int or source_epoch < 0:
            raise PersonalError(
                ErrorCode.INVALID_INPUT, "source_epoch must be non-negative"
            )
        data = {
            "action": "plan.update",
            "id": plan_id,
            "expected_version": expected_version,
            "title": title,
            "body": body,
            "sources": sources,
            "source_epoch": source_epoch,
        }
        # Preserve pre-Web CLI request digests across the schema upgrade.
        if source != "explicit_cli:user_declared_sources":
            data["source"] = source
        digest = _digest(data)
        with self.store.transaction() as connection:
            if replay := self._replay(connection, request_id, digest):
                return replay
            if source_epoch != self._current_epoch(connection):
                raise PersonalError(ErrorCode.VERSION_CONFLICT, "source_epoch is stale")
            row = connection.execute(
                "SELECT current_version FROM plans WHERE id=?", (plan_id,)
            ).fetchone()
            if row is None:
                raise PersonalError(ErrorCode.NOT_FOUND, "plan not found")
            if row["current_version"] != expected_version:
                raise PersonalError(
                    ErrorCode.VERSION_CONFLICT,
                    f"current version is {row['current_version']}",
                )
            version, operation_id, now = expected_version + 1, uuid4().hex, _now()
            # The version predicate makes a stale writer observable instead of
            # silently overwriting a plan changed by another request.
            changed = connection.execute(
                "UPDATE plans SET title=?,current_version=?,updated_at=? "
                "WHERE id=? AND current_version=?",
                (title, version, now, plan_id, expected_version),
            ).rowcount
            if changed != 1:
                raise PersonalError(
                    ErrorCode.VERSION_CONFLICT, "plan changed concurrently"
                )
            self._insert_revision(
                connection,
                plan_id,
                version,
                title,
                body,
                sources,
                source_epoch,
                operation_id,
                now,
                source,
            )
            self._finish(
                connection,
                operation_id,
                request_id,
                "plan.update",
                plan_id,
                digest,
                plan_id,
                version,
                now,
            )
            return Receipt(
                operation_id=operation_id,
                request_id=request_id,
                action="plan.update",
                object_id=plan_id,
                result_version=version,
                committed=True,
            )

    @staticmethod
    def _insert_revision(
        connection,
        plan_id,
        version,
        title,
        body,
        sources,
        source_epoch,
        operation_id,
        now,
        source,
    ):
        # CLI references are user declarations. They are deliberately not
        # labelled as files that the program has read and verified.
        connection.execute(
            "INSERT INTO plan_revisions VALUES (?,?,?,?,?,?,?,?,?)",
            (
                plan_id,
                version,
                title,
                body,
                json.dumps(sources, ensure_ascii=False),
                source,
                source_epoch,
                operation_id,
                now,
            ),
        )

    @staticmethod
    def _finish(
        connection,
        operation_id,
        request_id,
        action,
        target_id,
        digest,
        result_id,
        version,
        now,
    ):
        connection.execute(
            "INSERT INTO operations VALUES (?,?,?,?,?,'SUCCEEDED',?,?,NULL,?)",
            (
                operation_id,
                request_id,
                action,
                target_id,
                digest,
                result_id,
                version,
                now,
            ),
        )

    def get(self, plan_id: str, version: int | None = None) -> PlanView:
        with self.store.connect() as connection:
            plan = connection.execute(
                "SELECT * FROM plans WHERE id=?", (plan_id,)
            ).fetchone()
            if plan is None:
                raise PersonalError(ErrorCode.NOT_FOUND, "plan not found")
            selected = plan["current_version"] if version is None else version
            if type(selected) is not int or selected < 1:
                raise PersonalError(ErrorCode.INVALID_INPUT, "version must be positive")
            row = connection.execute(
                "SELECT * FROM plan_revisions WHERE plan_id=? AND version=?",
                (plan_id, selected),
            ).fetchone()
            if row is None:
                raise PersonalError(ErrorCode.NOT_FOUND, "plan version not found")
            epoch = self._current_epoch(connection)
        return PlanView(
            id=plan_id,
            title=row["title"],
            version=row["version"],
            body=row["body"],
            sources=tuple(json.loads(row["sources_json"])),
            source=row["source"],
            source_epoch=row["source_epoch"],
            created_at=datetime.fromisoformat(row["created_at"]),
            needs_review=row["source_epoch"] != epoch,
        )

    def list_current(self) -> list[PlanSummary]:
        with self.store.connect() as connection:
            epoch = self._current_epoch(connection)
            rows = connection.execute(
                "SELECT p.id,p.title,p.current_version,r.source_epoch "
                "FROM plans p JOIN plan_revisions r "
                "ON r.plan_id=p.id AND r.version=p.current_version "
                "ORDER BY p.updated_at DESC,p.id"
            ).fetchall()
        return [
            PlanSummary(
                id=row["id"],
                title=row["title"],
                version=row["current_version"],
                needs_review=row["source_epoch"] != epoch,
            )
            for row in rows
        ]

    def list_versions(self, plan_id: str) -> list[dict]:
        """返回已保存版本的目录，不加载全部正文；无此计划时保持404语义。"""
        with self.store.connect() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM plans WHERE id=?", (plan_id,)
                ).fetchone()
                is None
            ):
                raise PersonalError(ErrorCode.NOT_FOUND, "plan not found")
            epoch = self._current_epoch(connection)
            rows = connection.execute(
                "SELECT version,title,created_at,source_epoch FROM plan_revisions "
                "WHERE plan_id=? ORDER BY version DESC",
                (plan_id,),
            ).fetchall()
        return [
            dict(
                version=r["version"],
                title=r["title"],
                created_at=r["created_at"],
                needs_review=r["source_epoch"] != epoch,
            )
            for r in rows
        ]
