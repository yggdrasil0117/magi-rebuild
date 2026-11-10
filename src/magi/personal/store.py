"""SQLite business store. It never reads or writes LangGraph's tables."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 14


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
        if version < 6:
            # 来源轮次由服务端绑定，不靠模型文字或客户端自报证明工具调用。
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE task_documents ADD COLUMN source_turn_id TEXT REFERENCES web_turns(id);
                CREATE INDEX task_documents_turn ON task_documents(owner,source_turn_id);
                UPDATE schema_meta SET version=6;
                COMMIT;
                """
            )
        if version < 7:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE document_approvals (
                    document_id TEXT PRIMARY KEY REFERENCES task_documents(id),
                    owner TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    interrupt_id TEXT,
                    decision TEXT CHECK(decision IN ('approve','reject')),
                    error_code TEXT
                );
                UPDATE schema_meta SET version=7;
                COMMIT;
                """
            )
        if version < 8:
            # 执行记录是用户对某个不可变计划版本的观察，不修改计划正文或完成事实。
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE plan_execution_events (
                    event_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    plan_version INTEGER NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision > 0),
                    status TEXT NOT NULL CHECK(status IN ('NOT_STARTED','IN_PROGRESS','BLOCKED','COMPLETED')),
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(plan_id,plan_version) REFERENCES plan_revisions(plan_id,version),
                    UNIQUE(owner,request_id),
                    UNIQUE(owner,plan_id,plan_version,revision)
                );
                CREATE INDEX execution_history ON plan_execution_events(owner,plan_id,plan_version,revision DESC);
                UPDATE schema_meta SET version=8;
                COMMIT;
                """
            )
        if version < 9:
            # 跟随记忆事务登记待索引版本，队列只保存身份，不复制个人正文。
            connection.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS memory_index_pending (
                    memory_id TEXT PRIMARY KEY REFERENCES memories(id),
                    version INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO memory_index_pending
                    SELECT id, version FROM memories;
                CREATE TRIGGER IF NOT EXISTS memory_index_insert AFTER INSERT ON memories
                BEGIN
                    INSERT OR REPLACE INTO memory_index_pending VALUES (NEW.id, NEW.version);
                END;
                CREATE TRIGGER IF NOT EXISTS memory_index_update AFTER UPDATE ON memories
                BEGIN
                    INSERT OR REPLACE INTO memory_index_pending VALUES (NEW.id, NEW.version);
                END;
                UPDATE schema_meta SET version=9;
                COMMIT;
            """)
        if version < 10:
            connection.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS web_chat_imports (
                    thread_id TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS web_chat_history (
                    sequence INTEGER PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    turn_id TEXT REFERENCES web_turns(id),
                    messages_json TEXT NOT NULL,
                    UNIQUE(thread_id, turn_id)
                );
                UPDATE schema_meta SET version=10;
                COMMIT;
            """)
        if version < 11:
            connection.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    chat TEXT NOT NULL,
                    message TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK(mode IN ('chat','council')),
                    request_json TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('QUEUED','RUNNING','COMPLETED','FAILED','INTERRUPTED')),
                    turn_id TEXT REFERENCES web_turns(id),
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner,chat,message)
                );
                CREATE INDEX IF NOT EXISTS agent_tasks_queue
                    ON agent_tasks(owner,status,created_at,id);
                CREATE UNIQUE INDEX IF NOT EXISTS agent_tasks_one_active_chat
                    ON agent_tasks(owner,chat) WHERE status IN ('QUEUED','RUNNING');
                UPDATE schema_meta SET version=11;
                COMMIT;
            """)
        if version < 12:
            connection.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS issue_actions (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    turn_id TEXT NOT NULL UNIQUE REFERENCES web_turns(id),
                    repo TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('DRAFT','REJECTED','SENDING','UNKNOWN','VERIFIED')),
                    issue_number INTEGER,
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    verified_at TEXT
                );
                UPDATE schema_meta SET version=12;
                COMMIT;
            """)
            version = 12
        if version < 13:
            # 研究任务继续使用同一持久队列，但需要新的模式、PARTIAL 终态和
            # 独立成果身份。SQLite 不能修改 CHECK，因此在一个事务中重建队列表。
            task_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(agent_tasks)")
            }
            research_tables = connection.execute(
                """SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN
                   ('research_chats','agent_runs','research_artifacts',
                    'research_artifact_versions','research_reviews',
                    'research_artifact_decisions')"""
            ).fetchone()[0]
            if "execution_json" in task_columns and research_tables == 6:
                # 旧迁移测试会只回退版本号和某个旧表；v13 数据仍在时不能重建或删除。
                connection.execute("UPDATE schema_meta SET version=13")
            else:
                connection.executescript("""
                BEGIN IMMEDIATE;
                ALTER TABLE agent_tasks RENAME TO agent_tasks_v12;
                DROP INDEX IF EXISTS agent_tasks_queue;
                DROP INDEX IF EXISTS agent_tasks_one_active_chat;
                CREATE TABLE agent_tasks (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    chat TEXT NOT NULL,
                    message TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK(mode IN ('chat','council','research')),
                    request_json TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('QUEUED','RUNNING','COMPLETED','PARTIAL','FAILED','INTERRUPTED')),
                    turn_id TEXT REFERENCES web_turns(id),
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    execution_json TEXT,
                    artifact_id TEXT,
                    stage TEXT,
                    review_status TEXT,
                    stop_reason TEXT,
                    budget_json TEXT,
                    UNIQUE(owner,chat,message)
                );
                INSERT INTO agent_tasks
                    (id,owner,chat,message,mode,request_json,epoch,status,turn_id,
                     error_code,created_at,updated_at)
                SELECT id,owner,chat,message,mode,request_json,epoch,status,turn_id,
                       error_code,created_at,updated_at FROM agent_tasks_v12;
                DROP TABLE agent_tasks_v12;
                CREATE INDEX agent_tasks_queue
                    ON agent_tasks(owner,status,created_at,id);
                CREATE UNIQUE INDEX agent_tasks_one_active_chat
                    ON agent_tasks(owner,chat) WHERE status IN ('QUEUED','RUNNING');
                CREATE UNIQUE INDEX agent_tasks_one_active_artifact
                    ON agent_tasks(owner,artifact_id)
                    WHERE artifact_id IS NOT NULL AND status IN ('QUEUED','RUNNING');

                CREATE TABLE research_chats (
                    owner TEXT NOT NULL,
                    chat TEXT NOT NULL,
                    entry_mode TEXT NOT NULL CHECK(entry_mode IN ('persona','room')),
                    agent_id TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(owner,chat),
                    CHECK((entry_mode='persona' AND agent_id IS NOT NULL) OR
                          (entry_mode='room' AND agent_id IS NULL))
                );
                CREATE TABLE agent_runs (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    task_id TEXT NOT NULL REFERENCES agent_tasks(id),
                    agent_id TEXT NOT NULL,
                    config_version TEXT NOT NULL,
                    stage TEXT NOT NULL CHECK(stage IN ('generate','review','revise','verify')),
                    review_round INTEGER NOT NULL CHECK(review_round BETWEEN 0 AND 2),
                    target_artifact_id TEXT,
                    target_version INTEGER,
                    status TEXT NOT NULL CHECK(status IN ('RUNNING','COMPLETED','FAILED','INTERRUPTED')),
                    input_json TEXT NOT NULL,
                    output_json TEXT,
                    diagnostics_json TEXT NOT NULL DEFAULT '{}',
                    model_calls INTEGER NOT NULL DEFAULT 0,
                    tool_calls INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX agent_runs_task ON agent_runs(owner,task_id,created_at,id);
                CREATE TABLE research_artifacts (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    chat TEXT NOT NULL,
                    lead_agent_id TEXT NOT NULL,
                    current_version INTEGER NOT NULL CHECK(current_version >= 1),
                    status TEXT NOT NULL CHECK(status IN
                        ('DRAFT','APPROVED','SAVED','REJECTED','RECONCILE_REQUIRED')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE research_artifact_versions (
                    artifact_id TEXT NOT NULL REFERENCES research_artifacts(id),
                    version INTEGER NOT NULL CHECK(version >= 1),
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    completed_json TEXT NOT NULL,
                    unknowns_json TEXT NOT NULL,
                    generator_run_id TEXT NOT NULL REFERENCES agent_runs(id),
                    review_status TEXT NOT NULL CHECK(review_status IN
                        ('NOT_REQUESTED','IN_PROGRESS','PASSED','UNRESOLVED','INCOMPLETE')),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(artifact_id,version)
                );
                CREATE TABLE research_reviews (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    task_id TEXT NOT NULL REFERENCES agent_tasks(id),
                    artifact_id TEXT NOT NULL,
                    artifact_version INTEGER NOT NULL,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id),
                    agent_id TEXT NOT NULL,
                    round INTEGER NOT NULL CHECK(round IN (1,2)),
                    verdict TEXT NOT NULL CHECK(verdict IN
                        ('PASS','REVISE','INSUFFICIENT_EVIDENCE')),
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(artifact_id,artifact_version)
                        REFERENCES research_artifact_versions(artifact_id,version)
                );
                CREATE TABLE research_artifact_decisions (
                    request_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload_hash TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
                    status TEXT NOT NULL CHECK(status IN
                        ('APPROVED','SAVED','REJECTED','RECONCILE_REQUIRED')),
                    path TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(artifact_id,version)
                        REFERENCES research_artifact_versions(artifact_id,version),
                    UNIQUE(owner,artifact_id,version,decision)
                );
                UPDATE schema_meta SET version=13;
                COMMIT;
                """)
            version = 13
        if version < 14:
            # 旧记录缺省为长期有效。兼容只回退版本号的迁移测试。
            if not connection.in_transaction:
                connection.execute("BEGIN IMMEDIATE")
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(memories)")
            }
            if "expires_at" not in columns:
                connection.execute("ALTER TABLE memories ADD COLUMN expires_at TEXT")
            connection.execute("UPDATE schema_meta SET version=14")
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
