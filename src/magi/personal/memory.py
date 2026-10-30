import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from magi.personal.models import ErrorCode, Memory, PersonalError, Receipt
from magi.personal.store import PersonalStore

MAX_KEY_LENGTH = 80
MAX_VALUE_LENGTH = 4000
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(data: dict) -> str:
    encoded = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _scope(value: str) -> str:
    if value == "global":
        return value
    if value.startswith("session:") and SESSION_ID.fullmatch(value[8:]):
        return value
    raise PersonalError(ErrorCode.INVALID_INPUT, "scope must be global or session:<id>")


def _text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise PersonalError(
            ErrorCode.INVALID_INPUT,
            f"{name} must be non-empty and at most {maximum} characters",
        )
    return value.strip()


def _memory(row) -> Memory:
    return Memory(
        id=row["id"],
        key=row["key"],
        scope=row["scope"],
        value=row["value"],
        status=row["status"],
        version=row["version"],
        source=row["source"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        deleted_at=(
            datetime.fromisoformat(row["deleted_at"]) if row["deleted_at"] else None
        ),
    )


class MemoryService:
    def __init__(self, store: PersonalStore):
        self.store = store

    @staticmethod
    def _replay(connection, request_id: str, digest: str) -> Receipt | None:
        row = connection.execute(
            "SELECT * FROM operations WHERE request_id = ?", (request_id,)
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

    def add(
        self,
        *,
        key: str,
        scope: str,
        value: str,
        request_id: str,
        source: str = "explicit_cli",
    ) -> Receipt:
        if source not in {"explicit_cli", "explicit_web"}:
            raise PersonalError(
                ErrorCode.INVALID_INPUT,
                "memory creation requires an explicit user entry point",
            )
        key, scope, value = (
            _text(key, "key", MAX_KEY_LENGTH),
            _scope(scope),
            _text(value, "value", MAX_VALUE_LENGTH),
        )
        request_id = _text(request_id, "request_id", 120)
        data = {
            "action": "memory.add",
            "key": key,
            "scope": scope,
            "value": value,
            "source": source,
        }
        digest = _digest(data)
        with self.store.transaction() as connection:
            if replay := self._replay(connection, request_id, digest):
                return replay
            if connection.execute(
                "SELECT 1 FROM memories WHERE key=? AND scope=? AND status='ACTIVE'",
                (key, scope),
            ).fetchone():
                raise PersonalError(
                    ErrorCode.ACTIVE_KEY_CONFLICT,
                    "an active memory already uses this key and scope",
                )
            memory_id, operation_id, now = uuid4().hex, uuid4().hex, _now()
            connection.execute(
                "INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                (memory_id, key, scope, value, "ACTIVE", 1, source, now, now),
            )
            self._finish(
                connection,
                operation_id,
                request_id,
                "memory.add",
                None,
                digest,
                memory_id,
                None,
                1,
                source,
                now,
            )
            return Receipt(
                operation_id=operation_id,
                request_id=request_id,
                action="memory.add",
                object_id=memory_id,
                result_version=1,
                committed=True,
            )

    def update(
        self, memory_id: str, *, expected_version: int, value: str, request_id: str
    ) -> Receipt:
        value, request_id = (
            _text(value, "value", MAX_VALUE_LENGTH),
            _text(request_id, "request_id", 120),
        )
        if type(expected_version) is not int or expected_version < 1:
            raise PersonalError(
                ErrorCode.INVALID_INPUT, "expected_version must be a positive integer"
            )
        data = {
            "action": "memory.update",
            "id": memory_id,
            "expected_version": expected_version,
            "value": value,
        }
        return self._change(data, request_id, memory_id, expected_version, value, False)

    def delete(
        self, memory_id: str, *, expected_version: int, request_id: str
    ) -> Receipt:
        request_id = _text(request_id, "request_id", 120)
        if type(expected_version) is not int or expected_version < 1:
            raise PersonalError(
                ErrorCode.INVALID_INPUT, "expected_version must be a positive integer"
            )
        data = {
            "action": "memory.delete",
            "id": memory_id,
            "expected_version": expected_version,
        }
        return self._change(data, request_id, memory_id, expected_version, None, True)

    def _change(self, data, request_id, memory_id, expected_version, value, deleting):
        digest, action = _digest(data), data["action"]
        with self.store.transaction() as connection:
            if replay := self._replay(connection, request_id, digest):
                return replay
            row = connection.execute(
                "SELECT * FROM memories WHERE id=?", (memory_id,)
            ).fetchone()
            if row is None:
                raise PersonalError(ErrorCode.NOT_FOUND, "memory not found")
            if row["status"] != "ACTIVE":
                raise PersonalError(
                    ErrorCode.VERSION_CONFLICT, "deleted memory cannot be changed"
                )
            if row["version"] != expected_version:
                raise PersonalError(
                    ErrorCode.VERSION_CONFLICT, f"current version is {row['version']}"
                )
            new_version, operation_id, now = expected_version + 1, uuid4().hex, _now()
            status = "DELETED" if deleting else "ACTIVE"
            connection.execute(
                "UPDATE memories SET value=?,status=?,version=?,updated_at=?,deleted_at=? WHERE id=? AND version=?",
                (
                    None if deleting else value,
                    status,
                    new_version,
                    now,
                    now if deleting else None,
                    memory_id,
                    expected_version,
                ),
            )
            self._finish(
                connection,
                operation_id,
                request_id,
                action,
                memory_id,
                digest,
                memory_id,
                expected_version,
                new_version,
                row["source"],
                now,
            )
            return Receipt(
                operation_id=operation_id,
                request_id=request_id,
                action=action,
                object_id=memory_id,
                result_version=new_version,
                committed=True,
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
        old_version,
        new_version,
        source,
        now,
    ):
        connection.execute(
            "INSERT INTO memory_changes VALUES (?,?,?,?,?,?,?)",
            (operation_id, result_id, action, old_version, new_version, now, source),
        )
        connection.execute(
            "INSERT INTO operations VALUES (?,?,?,?,?,'SUCCEEDED',?,?,NULL,?)",
            (
                operation_id,
                request_id,
                action,
                target_id,
                digest,
                result_id,
                new_version,
                now,
            ),
        )
        connection.execute(
            "UPDATE app_state SET context_epoch=context_epoch+1 WHERE singleton=1"
        )

    def get(self, memory_id: str) -> Memory:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id=?", (memory_id,)
            ).fetchone()
        if row is None:
            raise PersonalError(ErrorCode.NOT_FOUND, "memory not found")
        return _memory(row)

    def list_active(self, scope: str | None = None) -> list[Memory]:
        parameters = ()
        sql = "SELECT * FROM memories WHERE status='ACTIVE'"
        if scope is not None:
            sql += " AND scope=?"
            parameters = (_scope(scope),)
        sql += " ORDER BY scope,key,id"
        with self.store.connect() as connection:
            return [_memory(row) for row in connection.execute(sql, parameters)]

    def current_epoch(self) -> int:
        with self.store.connect() as connection:
            return connection.execute(
                "SELECT context_epoch FROM app_state WHERE singleton=1"
            ).fetchone()[0]

    def get_operation(self, request_id: str) -> dict:
        request_id = _text(request_id, "request_id", 120)
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE request_id=?", (request_id,)
            ).fetchone()
        if row is None:
            raise PersonalError(ErrorCode.NOT_FOUND, "operation not found")
        return dict(row)
