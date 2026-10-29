from datetime import datetime, timezone
from uuid import uuid4

from magi.personal.models import CurrentBinding
from magi.personal.store import PersonalStore


class ContextService:
    def __init__(self, store: PersonalStore):
        self.store = store

    def resolve_binding(self, logical_session_id: str) -> CurrentBinding:
        with self.store.transaction() as connection:
            epoch = connection.execute(
                "SELECT context_epoch FROM app_state WHERE singleton=1"
            ).fetchone()[0]
            row = connection.execute(
                "SELECT thread_id FROM conversation_bindings WHERE logical_session_id=? AND epoch=?",
                (logical_session_id, epoch),
            ).fetchone()
            if row is None:
                thread_id = f"ctx-{uuid4().hex}"
                connection.execute(
                    "INSERT INTO conversation_bindings VALUES (?,?,?,?)",
                    (
                        logical_session_id,
                        epoch,
                        thread_id,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            else:
                thread_id = row[0]
        return CurrentBinding(
            logical_session_id=logical_session_id, epoch=epoch, thread_id=thread_id
        )

    def build_current_context(
        self, logical_session_id: str
    ) -> tuple[CurrentBinding, list]:
        binding = self.resolve_binding(logical_session_id)
        scopes = ("global", f"session:{logical_session_id}")
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT key,scope,value FROM memories WHERE status='ACTIVE' AND scope IN (?,?) ORDER BY key,scope",
                scopes,
            ).fetchall()
        selected = {}
        for row in rows:
            if row["key"] not in selected or row["scope"].startswith("session:"):
                selected[row["key"]] = dict(row)
        return binding, list(selected.values())

    def bindings(self, logical_session_id: str) -> list[CurrentBinding]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT epoch,thread_id FROM conversation_bindings WHERE logical_session_id=? ORDER BY epoch",
                (logical_session_id,),
            ).fetchall()
        return [
            CurrentBinding(
                logical_session_id=logical_session_id,
                epoch=row["epoch"],
                thread_id=row["thread_id"],
            )
            for row in rows
        ]
