import json
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from magi.harness.config import HarnessConfig
from magi.personal.models import PersonalError
from magi.personal.store import PersonalStore
from magi.web.app import create_app
from magi.web.schemas import TurnInput
from magi.web.service import WebError, WebService


class Extractor:
    def __init__(self):
        self.calls = 0
        self.value = "先讲原理，再给代码"
        self.key = "回答偏好"
        self.none = False
        self.error = False
        self.callback = None
        self.bad_source = False

    def extract(self, snapshot, diagnostics):
        self.calls += 1
        if self.callback:
            self.callback()
        if self.error:
            raise TimeoutError("private-secret-error")
        if self.none:
            return None
        return dict(
            key=self.key,
            value=self.value,
            reason="稳定偏好",
            sources=[
                dict(
                    evidence_ref="user:current",
                    quote="伪造原话" if self.bad_source else snapshot["question"],
                )
            ],
        )


@pytest.fixture
def env(tmp_path):
    settings = HarnessConfig(
        docs_root=tmp_path,
        db_path=tmp_path / "checkpoint.db",
        events_path=tmp_path / "events.jsonl",
        business_db_path=tmp_path / "business.db",
    )

    @contextmanager
    def session_factory(*args):
        session = MagicMock()
        session.ask.return_value = "已回复，不包含用于抽取的新用户事实"
        yield session

    extractor = Extractor()
    service = WebService(
        settings, "owner", session_factory=session_factory, memory_extractor=extractor
    )
    return settings, service, extractor


def turn(service, number=1):
    history = service.history("demo")
    return service.turn(
        TurnInput(
            chat_id="demo",
            message_id=f"u{number}",
            reply_message_id=f"a{number}",
            parent_reply_message_id=history[-1]["reply"] if history else None,
            user_text="以后解释代码时，我喜欢先讲原理，再给代码。",
        ),
        "chat",
    )


def proposal(service, number=1):
    t = turn(service, number)
    assert service.propose_memory(t["id"])["status"] == "PROPOSED"
    return service.suggestions.pending("demo")[-1]


def test_proposal_is_inactive_until_edited_confirmation_and_is_idempotent(env):
    _, service, extractor = env
    p = proposal(service)
    assert not service.memories.list_active() and service.memories.current_epoch() == 0
    assert p["sources"][0]["quote"] == service.history("demo")[0]["user_text"]
    result = service.decide_memory_suggestion(p["id"], "先举例，再讲原理")
    assert result["committed"] and service.memories.current_epoch() == 1
    assert service.memories.list_active()[0].value == "先举例，再讲原理"
    assert service.decide_memory_suggestion(p["id"], "先举例，再讲原理")["duplicate"]
    with pytest.raises(PersonalError):
        service.decide_memory_suggestion(p["id"], "换掉已确认内容")
    assert not service.suggestions.pending("demo") and extractor.calls == 1


def test_ignore_and_exact_duplicate_do_not_change_memory(env):
    _, service, extractor = env
    p = proposal(service)
    assert service.decide_memory_suggestion(p["id"])["status"] == "DISMISSED"
    assert service.decide_memory_suggestion(p["id"])["status"] == "DISMISSED"
    with pytest.raises(PersonalError):
        service.decide_memory_suggestion(p["id"], p["value"])
    t = turn(service, 2)
    assert service.propose_memory(t["id"])["status"] == "DUPLICATE"
    assert service.memories.current_epoch() == 0 and extractor.calls == 2


def test_existing_same_value_skipped_and_conflicting_value_is_update(env):
    _, service, extractor = env
    old = service.memories.add(
        key=extractor.key, value=extractor.value, scope="global", request_id="old"
    )
    t = turn(service)
    assert service.propose_memory(t["id"])["status"] == "DUPLICATE"
    extractor.value = "先举例再解释"
    p = proposal(service, 2)
    assert p["target_id"] == old.object_id and p["expected_version"] == 1
    receipt = service.decide_memory_suggestion(p["id"], p["value"])
    assert receipt["object_id"] == old.object_id and receipt["result_version"] == 2
    assert len(service.memories.list_active()) == 1


def test_stale_candidate_cannot_be_confirmed(env):
    _, service, _ = env
    p = proposal(service)
    service.memories.add(
        key="其他背景", value="新背景", scope="global", request_id="new"
    )
    assert not service.suggestions.pending("demo")
    with pytest.raises(PersonalError):
        service.decide_memory_suggestion(p["id"], p["value"])


def test_owner_auth_and_confirmation_validation(env):
    settings, service, extractor = env
    p = proposal(service)
    token = "memory-proposal-test-token-long-enough"
    with TestClient(create_app(settings, service=service, token=token)) as client:
        path = f"/v1/memory-suggestions/{p['id']}/confirm"
        assert (
            client.post(path, json={"confirmed": True, "value": p["value"]}).status_code
            == 401
        )
        headers = {"Authorization": "Bearer " + token}
        assert (
            client.post(
                path, headers=headers, json={"confirmed": False, "value": p["value"]}
            ).status_code
            == 422
        )
        assert (
            client.get("/v1/chats/demo/memory-suggestions", headers=headers).json()[0][
                "id"
            ]
            == p["id"]
        )
    other = WebService(settings, "other", memory_extractor=extractor)
    assert not other.suggestions.pending("demo")
    with pytest.raises(PersonalError):
        other.decide_memory_suggestion(p["id"], p["value"])
    with pytest.raises(WebError):
        other.propose_memory(p["turn_id"])


@pytest.mark.parametrize(
    "mode,expected",
    [("none", "NO_SUGGESTION"), ("error", "FAILED"), ("bad_source", "FAILED")],
)
def test_empty_or_failed_extraction_does_not_break_primary_or_replay(
    env, mode, expected
):
    _, service, extractor = env
    setattr(extractor, mode, True)
    t = turn(service)
    assert service.propose_memory(t["id"])["status"] == expected
    service.recover()
    assert service.propose_memory(t["id"])["status"] == expected
    assert service.get_turn(t["id"])["status"] == "COMPLETED"
    assert extractor.calls == 1 and not service.memories.list_active()
    assert "private-secret" not in json.dumps(service.suggestions.run_status(t["id"]))


def test_interrupted_run_is_not_replayed(env):
    _, service, extractor = env
    t = turn(service)
    service.suggestions.start(t)
    service.recover()
    assert service.propose_memory(t["id"])["status"] == "INTERRUPTED"
    assert extractor.calls == 0


def test_busy_snapshot_is_skipped_without_later_replay(env):
    _, service, extractor = env
    t = turn(service)
    service.gate.acquire()
    try:
        assert service.propose_memory(t["id"])["status"] == "SKIPPED_BUSY"
    finally:
        service.gate.release()
    assert service.propose_memory(t["id"])["status"] == "SKIPPED_BUSY"
    assert extractor.calls == 0


def test_oversized_snapshot_does_not_call_model(env):
    _, service, extractor = env
    for n in range(4):
        service.memories.add(
            key=f"背景{n}", value="长" * 4000, scope="global", request_id=f"seed{n}"
        )
    t = turn(service)
    assert service.propose_memory(t["id"])["status"] == "SKIPPED_CONTEXT_SIZE"
    assert extractor.calls == 0


def test_pending_limit_bounds_model_calls(env):
    _, service, extractor = env
    for n in range(1, 4):
        extractor.key = f"偏好{n}"
        proposal(service, n)
    t = turn(service, 4)
    assert service.propose_memory(t["id"])["status"] == "SKIPPED_LIMIT"
    assert extractor.calls == 3


def test_chat_not_blocked_during_extraction_and_epoch_change_discards_result(env):
    _, service, extractor = env
    first = turn(service)
    extractor.callback = lambda: turn(service, 2)
    assert service.propose_memory(first["id"])["status"] == "PROPOSED"
    assert len(service.history("demo")) == 2
    extractor.callback = lambda: service.memories.add(
        key="新背景", value="变更", scope="global", request_id="new"
    )
    next_turn = turn(service, 3)
    assert service.propose_memory(next_turn["id"])["status"] == "SKIPPED_STALE_CONTEXT"


def test_confirmation_recovers_after_memory_committed_before_state_update(
    env, monkeypatch
):
    _, service, _ = env
    p = proposal(service)
    original = service.suggestions.memories.add

    def fail_after_commit(**kwargs):
        original(**kwargs)
        raise RuntimeError("simulated process interruption")

    monkeypatch.setattr(service.suggestions.memories, "add", fail_after_commit)
    with pytest.raises(RuntimeError):
        service.decide_memory_suggestion(p["id"], p["value"])
    monkeypatch.setattr(service.suggestions.memories, "add", original)
    service.recover()
    assert service.decide_memory_suggestion(p["id"], p["value"])["duplicate"]
    assert (
        service.memories.current_epoch() == 1
        and len(service.memories.list_active()) == 1
    )


def test_schema3_migration_preserves_confirmed_memory(env):
    settings, service, _ = env
    created = service.memories.add(
        key="保留", value="不能丢失", scope="global", request_id="old"
    )
    with service.store.transaction() as db:
        db.execute("DROP TABLE task_documents")
        db.execute("DROP TABLE memory_suggestions")
        db.execute("DROP TABLE memory_suggestion_runs")
        db.execute("UPDATE schema_meta SET version=3")
    reopened = PersonalStore(settings.business_db_path)
    with reopened.connect() as db:
        assert db.execute("SELECT version FROM schema_meta").fetchone()[0] == 6
        assert (
            db.execute(
                "SELECT value FROM memories WHERE id=?", (created.object_id,)
            ).fetchone()[0]
            == "不能丢失"
        )


def test_failed_schema4_migration_does_not_leave_half_created_tables(tmp_path):
    import sqlite3

    path = tmp_path / "failed-migration.db"
    store = PersonalStore(path)
    with store.transaction() as db:
        db.execute("DROP TABLE memory_suggestions")
        db.execute("DROP TABLE memory_suggestion_runs")
        db.execute("CREATE TABLE memory_suggestions (unrelated TEXT)")
        db.execute("UPDATE schema_meta SET version=3")
    with pytest.raises(sqlite3.OperationalError):
        store.connect()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM schema_meta").fetchone()[0] == 3
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='memory_suggestion_runs'"
        ).fetchone()


@pytest.mark.parametrize(
    "mode", ["none", "suggest", "unknown_source", "tool", "truncated"]
)
def test_extractor_uses_only_current_user_source_and_bounded_request(
    env, monkeypatch, mode
):
    from langchain_core.messages import AIMessage

    from magi.personal.extraction import MemoryExtractor

    settings, _, _ = env
    seen = {}

    class Model:
        def bind(self, **kwargs):
            seen.update(kwargs)
            return self

        def invoke(self, messages):
            seen["payload"] = json.loads(messages[1].text)
            data = dict(
                decision="none" if mode == "none" else "suggest",
                key="偏好",
                value="原理优先",
                reason="长期偏好",
                source_ids=["source:404" if mode == "unknown_source" else "source:0"],
            )
            return AIMessage(
                content=json.dumps(data),
                tool_calls=[{"name": "write_memory", "args": {}, "id": "bad"}]
                if mode == "tool"
                else [],
                response_metadata={
                    "done_reason": "length" if mode == "truncated" else "stop"
                },
            )

    def build(config):
        assert config.num_predict == 512 and config.timeout_seconds == 20
        return Model()

    monkeypatch.setattr("magi.personal.extraction.build_model", build)
    extractor = MemoryExtractor(settings)
    snapshot = dict(question="以后先讲原理", memories=[], epoch=0)
    if mode in {"unknown_source", "tool", "truncated"}:
        with pytest.raises(ValueError):
            extractor.extract(snapshot, {})
    else:
        result = extractor.extract(snapshot, {})
        assert (result is None) == (mode == "none")
    assert set(seen["payload"]) == {"user_message", "active_memories", "source_options"}
    assert seen["format"]["properties"]["source_ids"]["items"]["enum"] == ["source:0"]
