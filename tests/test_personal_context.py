from magi.personal.context import ContextService
from magi.personal.memory import MemoryService
from magi.personal.store import PersonalStore


def services(tmp_path):
    store = PersonalStore(tmp_path / "personal.sqlite3")
    return MemoryService(store), ContextService(store)


def test_same_epoch_reuses_thread_and_memory_change_rebuilds_it(tmp_path):
    memory, context = services(tmp_path)
    empty, values = context.build_current_context("study")
    same = context.resolve_binding("study")
    assert same.thread_id == empty.thread_id
    assert values == []

    created = memory.add(
        key="marker", scope="global", value="CURRENT-222", request_id="add"
    )
    changed, values = context.build_current_context("study")
    assert changed.epoch == empty.epoch + 1
    assert changed.thread_id != empty.thread_id
    assert [item["value"] for item in values] == ["CURRENT-222"]

    memory.delete(created.object_id, expected_version=1, request_id="delete")
    deleted, values = context.build_current_context("study")
    assert deleted.thread_id not in {empty.thread_id, changed.thread_id}
    assert values == []
    assert len(context.bindings("study")) == 3


def test_session_scope_overrides_global_only_in_matching_session(tmp_path):
    memory, context = services(tmp_path)
    memory.add(key="style", scope="global", value="GLOBAL", request_id="g")
    memory.add(key="style", scope="session:study", value="SESSION", request_id="s")
    _, study = context.build_current_context("study")
    _, other = context.build_current_context("other")
    assert [(item["key"], item["value"]) for item in study] == [("style", "SESSION")]
    assert [(item["key"], item["value"]) for item in other] == [("style", "GLOBAL")]


def test_other_old_logical_session_also_moves_after_global_change(tmp_path):
    memory, context = services(tmp_path)
    first = context.resolve_binding("first")
    second = context.resolve_binding("second")
    memory.add(key="style", scope="global", value="NEW", request_id="global")
    assert context.resolve_binding("first").thread_id != first.thread_id
    assert context.resolve_binding("second").thread_id != second.thread_id
