import pytest

from magi.personal.memory import MemoryService
from magi.personal.models import ErrorCode, PersonalError
from magi.personal.plans import PlanService
from magi.personal.store import PersonalStore


@pytest.fixture
def services(tmp_path):
    store = PersonalStore(tmp_path / "personal.sqlite3")
    return PlanService(store), MemoryService(store)


def test_create_reopen_update_and_read_old_revision(services):
    plans, memory = services
    created = plans.create(
        title="本周计划",
        body="完成实验",
        sources=["notes.txt"],
        request_id="plan-1",
        source_epoch=memory.current_epoch(),
    )
    assert created.result_version == 1
    restarted = PlanService(plans.store)
    assert restarted.get(created.object_id).body == "完成实验"

    updated = restarted.update(
        created.object_id,
        expected_version=1,
        title="本周计划",
        body="完成实验并写总结",
        sources=["notes.txt"],
        request_id="plan-2",
        source_epoch=memory.current_epoch(),
    )
    assert updated.result_version == 2
    assert restarted.get(created.object_id).body == "完成实验并写总结"
    assert restarted.get(created.object_id, version=1).body == "完成实验"


def test_repeated_request_returns_original_receipt_without_new_revision(services):
    plans, memory = services
    arguments = dict(
        title="计划",
        body="第一项",
        sources=[],
        request_id="same",
        source_epoch=memory.current_epoch(),
    )
    first = plans.create(**arguments)
    replay = plans.create(**arguments)
    assert replay.duplicate is True
    assert replay.operation_id == first.operation_id
    assert plans.get(first.object_id).version == 1

    # A committed request remains replayable even if later memory changes make
    # its original source epoch old; idempotency describes the original fact.
    memory.add(key="new", scope="global", value="changed", request_id="memory")
    assert plans.create(**arguments).operation_id == first.operation_id


def test_idempotency_and_optimistic_version_conflicts(services):
    plans, memory = services
    first = plans.create(
        title="计划", body="A", request_id="one", source_epoch=memory.current_epoch()
    )
    with pytest.raises(PersonalError) as changed_request:
        plans.create(
            title="计划",
            body="B",
            request_id="one",
            source_epoch=memory.current_epoch(),
        )
    assert changed_request.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    with pytest.raises(PersonalError) as stale:
        plans.update(
            first.object_id,
            expected_version=2,
            title="计划",
            body="B",
            request_id="two",
            source_epoch=memory.current_epoch(),
        )
    assert stale.value.code == ErrorCode.VERSION_CONFLICT
    assert plans.get(first.object_id).version == 1


def test_memory_epoch_change_marks_plan_as_needing_review(services):
    plans, memory = services
    created = plans.create(
        title="求职计划",
        body="投递三个岗位",
        request_id="plan",
        source_epoch=memory.current_epoch(),
    )
    assert plans.get(created.object_id).needs_review is False
    memory.add(
        key="weekly_capacity", scope="global", value="两小时", request_id="memory"
    )
    assert plans.get(created.object_id).needs_review is True
    summary = plans.list_current()[0]
    assert summary.needs_review is True


def test_stale_source_epoch_cannot_be_saved(services):
    plans, memory = services
    old_epoch = memory.current_epoch()
    memory.add(key="style", scope="global", value="brief", request_id="memory")
    with pytest.raises(PersonalError) as caught:
        plans.create(
            title="旧候选", body="旧内容", request_id="stale", source_epoch=old_epoch
        )
    assert caught.value.code == ErrorCode.VERSION_CONFLICT
    assert plans.list_current() == []


def test_transaction_failure_rolls_back_plan_revision_and_operation(
    services, monkeypatch
):
    plans, memory = services
    original = plans._finish

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected before commit")

    monkeypatch.setattr(plans, "_finish", fail)
    with pytest.raises(RuntimeError, match="injected"):
        plans.create(
            title="计划",
            body="不会提交",
            request_id="rollback",
            source_epoch=memory.current_epoch(),
        )
    assert plans.list_current() == []
    with plans.store.connect() as connection:
        assert (
            connection.execute("SELECT count(*) FROM plan_revisions").fetchone()[0] == 0
        )
        assert connection.execute("SELECT count(*) FROM operations").fetchone()[0] == 0


@pytest.mark.parametrize(
    "title,body", [("", "body"), ("title", ""), ("x" * 201, "body")]
)
def test_invalid_plan_content_is_rejected(services, title, body):
    plans, memory = services
    with pytest.raises(PersonalError) as caught:
        plans.create(
            title=title,
            body=body,
            request_id="bad",
            source_epoch=memory.current_epoch(),
        )
    assert caught.value.code == ErrorCode.INVALID_INPUT
