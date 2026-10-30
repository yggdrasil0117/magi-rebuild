"""候选与有效记忆分开保存；只有明确确认才调用MemoryService。"""

import hashlib
import json
from dataclasses import asdict
from uuid import uuid4

from magi.personal.memory import MemoryService
from magi.personal.models import ErrorCode, PersonalError


def normalized(text):
    return " ".join(text.split()).casefold()


class MemorySuggestions:
    def __init__(self, store, owner):
        self.store, self.owner = store, owner
        self.memories = MemoryService(store)

    def recover(self):
        """重启不重放提取；已提交但未标记的确认从真实操作记录恢复。"""
        with self.store.transaction() as db:
            db.execute(
                "UPDATE memory_suggestion_runs SET status='INTERRUPTED' WHERE status='RUNNING'"
            )
            db.execute(
                "UPDATE memory_suggestions SET status='CONFIRMED' WHERE EXISTS "
                "(SELECT 1 FROM operations o WHERE o.request_id='memory-suggestion:' || memory_suggestions.id || ':save' AND o.status='SUCCEEDED')"
            )

    def run_status(self, turn_id):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT status,diagnostics_json FROM memory_suggestion_runs WHERE turn_id=? AND owner=?",
                (turn_id, self.owner),
            ).fetchone()
        return (
            {
                "status": row["status"],
                "diagnostics": json.loads(row["diagnostics_json"]),
            }
            if row
            else {"status": "NOT_STARTED"}
        )

    def skip(self, turn, status):
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO memory_suggestion_runs(turn_id,owner,status) VALUES (?,?,?)",
                (turn["id"], self.owner, status),
            )

    def start(self, turn):
        """主串行门内冻结用户表达与有效记忆；同轮已有记录时不再调用模型。"""
        memories = [
            dict(id=m.id, key=m.key, value=m.value, version=m.version)
            for m in self.memories.list_active("global")
        ]
        epoch = self.memories.current_epoch()
        snapshot = {"question": turn["user_text"], "memories": memories, "epoch": epoch}
        with self.store.transaction() as db:
            if db.execute(
                "SELECT 1 FROM memory_suggestion_runs WHERE turn_id=?", (turn["id"],)
            ).fetchone():
                return None
            pending = db.execute(
                "SELECT COUNT(*) FROM memory_suggestions WHERE owner=? AND epoch=? AND status='PENDING'",
                (self.owner, epoch),
            ).fetchone()[0]
            status = "RUNNING"
            if pending >= 3:
                status = "SKIPPED_LIMIT"
            elif len(json.dumps(snapshot, ensure_ascii=False)) > 12000:
                status = "SKIPPED_CONTEXT_SIZE"
            db.execute(
                "INSERT INTO memory_suggestion_runs(turn_id,owner,status) VALUES (?,?,?)",
                (turn["id"], self.owner, status),
            )
        return snapshot if status == "RUNNING" else None

    def finish(self, turn, snapshot, candidate, diagnostics):
        """校验来源和确定性重复后保存待确认候选，不修改epoch。"""
        if candidate is not None:
            for key, maximum in (("key", 80), ("value", 400), ("reason", 160)):
                if (
                    not isinstance(candidate.get(key), str)
                    or not candidate[key].strip()
                    or len(candidate[key]) > maximum
                ):
                    raise ValueError("invalid memory suggestion")
            sources = candidate.get("sources", [])
            if (
                not sources
                or len(sources) > 2
                or any(
                    s.get("evidence_ref") != "user:current"
                    or not s.get("quote")
                    or s["quote"] not in snapshot["question"]
                    for s in sources
                )
            ):
                raise ValueError("unknown evidence reference")
        with self.store.transaction() as db:
            status = "NO_SUGGESTION"
            if (
                snapshot["epoch"]
                != db.execute("SELECT context_epoch FROM app_state").fetchone()[0]
            ):
                status = "SKIPPED_STALE_CONTEXT"
            elif candidate:
                key, value = candidate["key"].strip(), candidate["value"].strip()
                old = next(
                    (
                        m
                        for m in snapshot["memories"]
                        if normalized(m["key"]) == normalized(key)
                    ),
                    None,
                )
                if old:
                    key = old["key"]
                fingerprint = hashlib.sha256(
                    json.dumps(
                        [normalized(key), normalized(value)], ensure_ascii=False
                    ).encode()
                ).hexdigest()
                duplicate = db.execute(
                    "SELECT 1 FROM memory_suggestions WHERE owner=? AND fingerprint=?",
                    (self.owner, fingerprint),
                ).fetchone()
                if duplicate or (old and normalized(old["value"]) == normalized(value)):
                    status = "DUPLICATE"
                else:
                    db.execute(
                        "INSERT INTO memory_suggestions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',NULL)",
                        (
                            uuid4().hex,
                            turn["id"],
                            self.owner,
                            turn["chat"],
                            snapshot["epoch"],
                            key,
                            value,
                            candidate["reason"],
                            json.dumps(sources, ensure_ascii=False),
                            fingerprint,
                            old["id"] if old else None,
                            old["version"] if old else None,
                            old["value"] if old else None,
                        ),
                    )
                    status = "PROPOSED"
            db.execute(
                "UPDATE memory_suggestion_runs SET status=?,diagnostics_json=? WHERE turn_id=?",
                (status, json.dumps(diagnostics), turn["id"]),
            )

    def fail(self, turn_id, diagnostics):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE memory_suggestion_runs SET status='FAILED',diagnostics_json=? WHERE turn_id=? AND owner=?",
                (json.dumps(diagnostics), turn_id, self.owner),
            )

    def pending(self, chat):
        epoch = self.memories.current_epoch()
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM memory_suggestions WHERE owner=? AND chat=? AND epoch=? AND status='PENDING' ORDER BY rowid",
                (self.owner, chat, epoch),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["sources"] = json.loads(item.pop("sources_json"))
            item["stale"] = item["epoch"] != epoch
            result.append(item)
        return result

    def _get(self, db, suggestion_id):
        row = db.execute(
            "SELECT * FROM memory_suggestions WHERE id=? AND owner=?",
            (suggestion_id, self.owner),
        ).fetchone()
        if row is None:
            raise PersonalError(ErrorCode.NOT_FOUND, "memory suggestion not found")
        return row

    def confirm(self, suggestion_id, value):
        """主串行门内确认；先冻结编辑值，依靠固定操作ID弥合提交后的中断。"""
        request_id = "memory-suggestion:" + suggestion_id + ":save"
        with self.store.transaction() as db:
            row = self._get(db, suggestion_id)
            if row["status"] == "DISMISSED":
                raise PersonalError(
                    ErrorCode.VERSION_CONFLICT, "suggestion was dismissed"
                )
            if row["confirmed_value"] is not None and row["confirmed_value"] != value:
                raise PersonalError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "confirmation content changed"
                )
            operation = db.execute(
                "SELECT 1 FROM operations WHERE request_id=?", (request_id,)
            ).fetchone()
            if (
                not operation
                and row["epoch"]
                != db.execute("SELECT context_epoch FROM app_state").fetchone()[0]
            ):
                raise PersonalError(
                    ErrorCode.VERSION_CONFLICT,
                    "memory context changed; regenerate suggestion",
                )
            db.execute(
                "UPDATE memory_suggestions SET confirmed_value=? WHERE id=?",
                (value, suggestion_id),
            )
        if row["target_id"]:
            receipt = self.memories.update(
                row["target_id"],
                expected_version=row["expected_version"],
                value=value,
                request_id=request_id,
            )
        else:
            receipt = self.memories.add(
                key=row["key"],
                scope="global",
                value=value,
                request_id=request_id,
                source="explicit_web",
            )
        with self.store.transaction() as db:
            db.execute(
                "UPDATE memory_suggestions SET status='CONFIRMED' WHERE id=?",
                (suggestion_id,),
            )
        return asdict(receipt)

    def dismiss(self, suggestion_id):
        """忽略不推进epoch；已保存的候选不能再伪装成未采纳。"""
        with self.store.transaction() as db:
            row = self._get(db, suggestion_id)
            if (
                row["status"] == "CONFIRMED"
                or db.execute(
                    "SELECT 1 FROM operations WHERE request_id=?",
                    ("memory-suggestion:" + suggestion_id + ":save",),
                ).fetchone()
            ):
                raise PersonalError(
                    ErrorCode.VERSION_CONFLICT, "suggestion already confirmed"
                )
            db.execute(
                "UPDATE memory_suggestions SET status='DISMISSED' WHERE id=?",
                (suggestion_id,),
            )
        return {"status": "DISMISSED"}
