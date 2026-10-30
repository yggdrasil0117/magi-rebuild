"""SQLite business store. It never reads or writes LangGraph's tables."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 5


class ClosingConnection(sqlite3.Connection):
    """A read context must release its handle, including on Windows."""

    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class PersonalStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            self._migrate(connection)
        except BaseException:
            connection.close()  # 关闭失败迁移的连接，回滚仍未提交的新增结构。
            raise
        return connection

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)"
        )
        row = connection.execute("SELECT version FROM schema_meta").fetchone()
        if row is None:
            connection.execute("INSERT INTO schema_meta VALUES (0)")
            version = 0
        else:
            version = row[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("business database schema is newer than this program")
        if version < 1:
            connection.executescript(
                """
                CREATE TABLE app_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    context_epoch INTEGER NOT NULL CHECK (context_epoch >= 0)
                );
                INSERT INTO app_state VALUES (1, 0);
                CREATE TABLE memories (
                    id TEXT PRIMARY KEY,
                    key TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    value TEXT,
                    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'DELETED')),
                    version INTEGER NOT NULL CHECK (version >= 1),
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE UNIQUE INDEX one_active_memory_per_key_scope
                    ON memories(key, scope) WHERE status = 'ACTIVE';
                CREATE TABLE memory_changes (
                    operation_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL REFERENCES memories(id),
                    action TEXT NOT NULL,
                    old_version INTEGER,
                    new_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL
                );
                CREATE TABLE operations (
                    operation_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    action TEXT NOT NULL,
                    target_id TEXT,
                    request_digest TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('SUCCEEDED', 'FAILED')),
                    result_id TEXT,
                    result_version INTEGER,
                    error_code TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE conversation_bindings (
                    logical_session_id TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    thread_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(logical_session_id, epoch)
                );
                UPDATE schema_meta SET version = 1;
                """
            )
            version = 1
        if version < 2:
            # Plan bodies are immutable revisions. The plans row only points to
            # the current revision, so an update never destroys the old text.
            connection.executescript(
                """
                CREATE TABLE plans (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    current_version INTEGER NOT NULL CHECK (current_version >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE plan_revisions (
                    plan_id TEXT NOT NULL REFERENCES plans(id),
                    version INTEGER NOT NULL CHECK (version >= 1),
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_epoch INTEGER NOT NULL CHECK (source_epoch >= 0),
                    operation_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(plan_id, version)
                );
                UPDATE schema_meta SET version = 2;
                """
            )
        if version < 3:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS web_turns (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    chat TEXT NOT NULL,
                    message TEXT NOT NULL,
                    reply TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    user_text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    result_json TEXT,
                    proposal_id TEXT,
                    synced INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(owner,chat,message),
                    UNIQUE(owner,chat,reply)
                );
                CREATE TABLE IF NOT EXISTS plan_proposals (
                    id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL UNIQUE REFERENCES web_turns(id),
                    owner TEXT NOT NULL,
                    chat TEXT NOT NULL,
                    reply TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    target_id TEXT,
                    expected_version INTEGER,
                    created_at TEXT NOT NULL
                );
                UPDATE schema_meta SET version=3;
                """
            )
        if version < 4:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE memory_suggestion_runs (
                    turn_id TEXT PRIMARY KEY REFERENCES web_turns(id),
                    owner TEXT NOT NULL,
                    status TEXT NOT NULL,
                    diagnostics_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE memory_suggestions (
                    id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL UNIQUE REFERENCES web_turns(id),
                    owner TEXT NOT NULL,
                    chat TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    target_id TEXT,
                    expected_version INTEGER,
                    old_value TEXT,
                    status TEXT NOT NULL CHECK(status IN ('PENDING','DISMISSED','CONFIRMED')),
                    confirmed_value TEXT
                );
                CREATE INDEX memory_suggestions_owner ON memory_suggestions(owner,chat,status);
                UPDATE schema_meta SET version=4;
                COMMIT;
                """
            )
        if version < 5:
            # 工具产物独立于计划版本；预览也需持久化，避免重试重新生成内容。
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE task_documents (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL REFERENCES plans(id),
                    version INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('PREVIEW','CREATED')),
                    UNIQUE(owner,request_id)
                );
                UPDATE schema_meta SET version=5;
                COMMIT;
                """
            )
        connection.commit()

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
