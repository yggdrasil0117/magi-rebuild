import sqlite3

import pytest

from magi.personal.memory import MemoryService
from magi.personal.models import ErrorCode, PersonalError
from magi.personal.store import PersonalStore


@pytest.fixture
def service(tmp_path):
    return MemoryService(PersonalStore(tmp_path / "personal.sqlite3"))


def test_add_update_delete_and_restart(service):
    created = service.add(
        key="answer_style", scope="global", value="先给结论", request_id="memory-1"
    )
    assert created.result_version == 1
    assert service.current_epoch() == 1
    assert service.get(created.object_id).value == "先给结论"

    restarted = MemoryService(service.store)
    updated = restarted.update(
        created.object_id, expected_version=1, value="先解释依据", request_id="memory-2"
    )
    assert updated.result_version == 2
    assert restarted.current_epoch() == 2
    assert restarted.get(created.object_id).value == "先解释依据"

    deleted = restarted.delete(
        created.object_id, expected_version=2, request_id="memory-3"
    )
    tombstone = restarted.get(created.object_id)
    assert deleted.result_version == 3
    assert tombstone.status == "DELETED"
    assert tombstone.value is None
    assert restarted.list_active() == []
    assert restarted.current_epoch() == 3


def test_same_request_is_replayed_once(service):
    first = service.add(key="style", scope="global", value="brief", request_id="same")
    replay = service.add(key="style", scope="global", value="brief", request_id="same")
    assert replay.operation_id == first.operation_id
    assert replay.object_id == first.object_id
    assert replay.duplicate is True
    assert service.current_epoch() == 1
    assert len(service.list_active()) == 1


def test_same_request_with_different_parameters_is_rejected(service):
    service.add(key="style", scope="global", value="brief", request_id="same")
    with pytest.raises(PersonalError) as caught:
        service.add(key="style", scope="global", value="detailed", request_id="same")
    assert caught.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


def test_key_scope_and_version_conflicts_are_explicit(service):
    created = service.add(key="style", scope="global", value="brief", request_id="one")
    with pytest.raises(PersonalError) as duplicate:
        service.add(key="style", scope="global", value="other", request_id="two")
    assert duplicate.value.code == ErrorCode.ACTIVE_KEY_CONFLICT
    with pytest.raises(PersonalError) as stale:
        service.update(
            created.object_id, expected_version=2, value="other", request_id="three"
        )
    assert stale.value.code == ErrorCode.VERSION_CONFLICT
    assert service.current_epoch() == 1


@pytest.mark.parametrize(
    "key,scope,value",
    [
        ("", "global", "x"),
        ("x", "wrong", "x"),
        ("x", "session:bad space", "x"),
        ("x", "global", ""),
    ],
)
def test_invalid_input_does_not_write(service, key, scope, value):
    with pytest.raises(PersonalError) as caught:
        service.add(key=key, scope=scope, value=value, request_id="bad")
    assert caught.value.code == ErrorCode.INVALID_INPUT
    assert service.list_active() == []


def test_transaction_rolls_back_memory_and_epoch_on_failure(service, monkeypatch):
    original = service._finish

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected before commit")

    monkeypatch.setattr(service, "_finish", fail)
    with pytest.raises(RuntimeError, match="injected"):
        service.add(key="style", scope="global", value="brief", request_id="rollback")
    assert service.list_active() == []
    assert service.current_epoch() == 0
    with service.store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM operations").fetchone()[0] == 0


def test_schema_version_is_preserved_across_reopen(tmp_path):
    store = PersonalStore(tmp_path / "personal.sqlite3")
    with store.connect() as connection:
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 4
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE schema_meta SET version=99")
    with pytest.raises(RuntimeError, match="newer"):
        store.connect()
