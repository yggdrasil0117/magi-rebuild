"""执行记录业务闭环、并发、幂等和版本隔离测试，不调用生成模型。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from magi.personal.executions import PlanExecutions
from magi.personal.models import PersonalError
from magi.personal.plans import PlanService
from magi.personal.store import PersonalStore


@pytest.fixture
def execution_env(tmp_path):
    store = PersonalStore(tmp_path / "business.db")
    plans = PlanService(store)
    receipt = plans.create(
        title="学习接口",
        body="完成接口并记录验证结果",
        request_id="plan-create",
        source_epoch=0,
    )
    return store, plans, receipt.object_id, PlanExecutions(store, "owner")


def write(service, plan_id, number=0, **overrides):
    values = dict(
        request_id="record-" + str(number),
        expected_revision=number,
        status="IN_PROGRESS",
        note="实际进展" + str(number),
        confirmed=True,
    )
    values.update(overrides)
    return service.record(plan_id, 1, **values)


def test_lifecycle_replay_and_restart(execution_env):
    store, plans, plan_id, service = execution_env
    assert service.view(plan_id, 1)["status"] == "NOT_STARTED"
    first = write(service, plan_id)
    write(service, plan_id, 1, status="BLOCKED", note="接口超时，待排查")
    write(service, plan_id, 2, status="COMPLETED", note="已运行验证并记录结果")
    assert write(service, plan_id)["event_id"] == first["event_id"]
    assert write(service, plan_id)["duplicate"]
    restored = PlanExecutions(PersonalStore(store.path), "owner").view(plan_id, 1)
    assert restored["revision"] == 3 and restored["status"] == "COMPLETED"
    assert len(restored["events"]) == 3 and "用户自报" in restored["provenance"]
    assert plans.get(plan_id).version == 1
    write(service, plan_id, 3, status="IN_PROGRESS", note="复查发现问题，重新开始")
    assert service.view(plan_id, 1)["status"] == "IN_PROGRESS"


@pytest.mark.parametrize(
    "override,code",
    [
        ({"confirmed": False}, "INVALID_INPUT"),
        ({"status": "AUTO_VERIFIED"}, "INVALID_INPUT"),
        ({"note": " "}, "INVALID_INPUT"),
        ({"expected_revision": True}, "INVALID_INPUT"),
        ({"note": "x" * 2001}, "INVALID_INPUT"),
    ],
)
def test_rejections_do_not_write(execution_env, override, code):
    store, plans, plan_id, service = execution_env
    with pytest.raises(PersonalError) as exc:
        write(service, plan_id, **override)
    assert exc.value.code == code and service.view(plan_id, 1)["revision"] == 0


def test_conflicts_and_missing_version(execution_env):
    store, plans, plan_id, service = execution_env
    write(service, plan_id)
    with pytest.raises(PersonalError, match="不同执行记录"):
        write(service, plan_id, note="换了内容")
    with pytest.raises(PersonalError, match="已更新"):
        write(service, plan_id, request_id="different")
    with pytest.raises(PersonalError) as exc:
        service.view(plan_id, 2)
    assert exc.value.code == "NOT_FOUND"
    assert service.view(plan_id, 1)["revision"] == 1


def test_plan_versions_do_not_inherit_completion(execution_env):
    store, plans, plan_id, service = execution_env
    write(service, plan_id, status="COMPLETED", note="旧版已执行")
    plans.update(
        plan_id,
        expected_version=1,
        title="新安排",
        body="新增实际任务",
        request_id="plan-v2",
        source_epoch=0,
    )
    assert service.view(plan_id, 2)["status"] == "NOT_STARTED"
    assert service.view(plan_id, 1)["status"] == "COMPLETED"
    assert plan_id not in service.current_statuses()
    assert PlanExecutions(store, "other").view(plan_id, 1)["revision"] == 0
    assert plans.get(plan_id, 1).body == "完成接口并记录验证结果"


def test_two_writers_cannot_silently_overwrite(execution_env):
    store, plans, plan_id, service = execution_env

    def submit(index):
        try:
            return write(
                PlanExecutions(store, "owner"),
                plan_id,
                request_id="thread-" + str(index),
            )["committed"]
        except PersonalError as exc:
            return str(exc.code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, [1, 2]))
    assert sorted(map(str, outcomes)) == ["True", "VERSION_CONFLICT"]
    assert service.view(plan_id, 1)["revision"] == 1


def test_storage_abort_rolls_back_and_same_request_can_retry(execution_env):
    store, plans, plan_id, service = execution_env
    with store.transaction() as db:
        db.execute(
            "CREATE TRIGGER abort_execution BEFORE INSERT ON plan_execution_events BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        write(service, plan_id)
    assert service.view(plan_id, 1)["revision"] == 0
    with store.transaction() as db:
        db.execute("DROP TRIGGER abort_execution")
    assert write(service, plan_id)["revision"] == 1


def test_bounded_model_context_preserves_full_history(execution_env):
    store, plans, plan_id, service = execution_env
    for i in range(4):
        write(service, plan_id, i, note="进展" * 500)
    context = service.context(plan_id, 1)
    assert context["history_truncated"] and context["notes_truncated"]
    assert len(context["events"]) == 3 and all(
        len(e["note"]) == 300 for e in context["events"]
    )
    assert len(service.view(plan_id, 1)["events"][0]["note"]) == 1000


def test_schema7_upgrade_keeps_plan_and_initializes_empty_execution(execution_env):
    """构造真实schema7结构而非仅改版本号，升级后原计划与正文保持。"""
    store, plans, plan_id, service = execution_env
    with store.transaction() as db:
        db.execute("DROP TABLE plan_execution_events")
        db.execute("UPDATE schema_meta SET version=7")
    upgraded = PersonalStore(store.path)
    assert PlanService(upgraded).get(plan_id, 1).body == "完成接口并记录验证结果"
    assert PlanExecutions(upgraded, "owner").view(plan_id, 1)["revision"] == 0
    with upgraded.connect() as db:
        assert db.execute("SELECT version FROM schema_meta").fetchone()[0] == 10
