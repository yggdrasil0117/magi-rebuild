"""执行跟踪HTTP与修订集成：真实SQLite，脚本模型，不发起云端调用。"""

import json

from fastapi.testclient import TestClient

from magi.council.generation import model_snapshot
from magi.web.app import create_app
from tests.test_web_mvp import confirm, request
from tests.test_web_mvp import env as env


def test_http_confirmation_replay_conflict_and_busy(env):
    settings, service, session, calls = env
    plan = service.plans.create(
        title="执行测试", body="做一项实际任务", request_id="seed", source_epoch=0
    )
    path = "/v1/plans/" + plan.object_id + "/execution"
    headers = {"Authorization": "Bearer " + "x" * 24}
    with TestClient(create_app(settings, token="x" * 24, service=service)) as client:
        assert client.get(path + "?version=1").status_code == 401
        state = client.get(path + "?version=1", headers=headers).json()
        assert state["status"] == "NOT_STARTED"
        body = dict(
            request_id="record",
            version=1,
            expected_revision=0,
            status="BLOCKED",
            note="接口超时",
            confirmed=True,
        )
        assert (
            client.post(
                path, headers=headers, json=body | {"confirmed": False}
            ).status_code
            == 422
        )
        assert (
            client.post(
                path, headers=headers, json=body | {"status": "UNKNOWN"}
            ).status_code
            == 422
        )
        first = client.post(path, headers=headers, json=body)
        assert first.status_code == 200 and first.json()["committed"]
        assert client.post(path, headers=headers, json=body).json()["duplicate"]
        assert (
            client.post(
                path, headers=headers, json=body | {"note": "其他内容"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                path, headers=headers, json=body | {"request_id": "stale"}
            ).status_code
            == 409
        )
        assert (
            client.get("/v1/plans", headers=headers).json()[0]["execution_status"]
            == "BLOCKED"
        )
        service.gate.acquire()
        try:
            assert client.post(path, headers=headers, json=body).status_code == 429
        finally:
            service.gate.release()
        state = client.get(path + "?version=1", headers=headers).json()
        assert state["revision"] == 1 and len(state["events"]) == 1


def test_revision_receives_execution_and_new_version_is_unstarted(env):
    settings, service, session, calls = env
    plan = service.plans.create(
        title="本周安排", body="实验与求职并行", request_id="seed", source_epoch=0
    )
    service.executions.record(
        plan.object_id,
        1,
        request_id="blocked",
        expected_revision=0,
        status="BLOCKED",
        note="实验数据不足，先记录缺口",
        confirmed=True,
    )
    turn = service.turn(
        request(target_plan_id=plan.object_id, expected_version=1), "council"
    )
    assert turn["status"] == "COMPLETED"
    frozen = json.loads(calls[0][0])
    assert frozen["old_plan"]["execution"]["status"] == "BLOCKED"
    assert (
        model_snapshot(frozen)["old_plan"]["execution"]["events"][0]["note"]
        == "实验数据不足，先记录缺口"
    )
    assert len({c[0] for c in calls[:3]}) == 1
    assert service.plans.get(plan.object_id).version == 1
    receipt = confirm(service, turn)
    assert receipt["result_version"] == 2
    assert service.executions.view(plan.object_id, 2)["revision"] == 0
    assert service.executions.view(plan.object_id, 1)["status"] == "BLOCKED"


def test_new_execution_invalidates_unsaved_revision_but_not_saved_receipt(env):
    settings, service, session, calls = env
    plan = service.plans.create(
        title="本周安排", body="实验与求职并行", request_id="seed", source_epoch=0
    )
    turn = service.turn(
        request(target_plan_id=plan.object_id, expected_version=1), "council"
    )
    service.executions.record(
        plan.object_id,
        1,
        request_id="after-generation",
        expected_revision=0,
        status="COMPLETED",
        note="生成后又完成了旧任务",
        confirmed=True,
    )
    import pytest

    from magi.web.service import WebError

    with pytest.raises(WebError, match="执行记录已变化"):
        confirm(service, turn)
    assert service.plans.get(plan.object_id).version == 1
    fresh = service.turn(
        request("2", target_plan_id=plan.object_id, expected_version=1), "council"
    )
    saved = confirm(service, fresh)
    service.executions.record(
        plan.object_id,
        1,
        request_id="after-save",
        expected_revision=1,
        status="IN_PROGRESS",
        note="追加纠正记录",
        confirmed=True,
    )
    replay = confirm(service, fresh)
    assert replay["duplicate"] and replay["object_id"] == saved["object_id"]
