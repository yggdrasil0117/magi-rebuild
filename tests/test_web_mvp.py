import json
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from magi.council.service import Council, Opinion
from magi.harness.config import HarnessConfig
from magi.personal.models import PersonalError
from magi.web.app import create_app
from magi.web.schemas import Confirmation, TurnInput
from magi.web.service import WebError, WebService

OPINION = {
    "view": "分析约束",
    "recommendation": "先验证实验",
    "concerns": ["休息"],
    "unknowns": [],
    "evidence_refs": ["user:current"],
}
SYNTHESIS = {
    "agreement": ["缩小范围"],
    "disagreements": ["投入比例"],
    "unknowns": [],
    "candidate": {
        "title": "本周安排",
        "steps": [
            {"action": "实验", "done_when": "记录结果", "minutes": 60, "kind": "work"}
        ],
        "assumptions": ["尚有两小时"],
        "source_excerpts": [{"evidence_ref": "user:current", "quote": "本周六小时"}],
        "unknowns": ["实验环境是否就绪"],
        "revision_reason": "保留实验，缩减求职以适应剩余时间",
    },
}


@pytest.fixture
def env(tmp_path):
    settings = HarnessConfig(
        docs_root=tmp_path,
        db_path=tmp_path / "checkpoint.db",
        events_path=tmp_path / "events.jsonl",
        business_db_path=tmp_path / "personal.db",
    )
    session = MagicMock()
    session.agent.get_state.return_value.next = ()
    session.messages.return_value = [AIMessage(content="日常背景")]
    session.ask.return_value = "普通回复"

    @contextmanager
    def factory(*args):
        yield session

    calls = []

    def invoke(system, payload, schema, budget, timeout):
        calls.append((payload, schema, budget))
        return json.dumps(
            OPINION if schema is Opinion else SYNTHESIS, ensure_ascii=False
        )

    service = WebService(
        settings, "owner", council=Council(settings, invoke), session_factory=factory
    )
    service.recover()
    return settings, service, session, calls


def request(number="1", **kwargs):
    return TurnInput(
        chat_id="study",
        message_id="u" + number,
        reply_message_id="a" + number,
        parent_reply_message_id="a" + str(int(number) - 1) if int(number) > 1 else None,
        user_text="本周六小时，论文与求职并行",
        **kwargs,
    )


def confirm(service, turn, **kwargs):
    p = service.proposal(turn["proposal_id"], "study", turn["reply"])
    data = dict(
        chat_id="study", reply_message_id=turn["reply"], payload_hash=p["payload_hash"]
    )
    data.update(kwargs)
    return service.confirm(p["id"], Confirmation(**data))


def test_full_create_restart_feedback_versions(env):
    settings, service, session, calls = env
    daily = service.turn(request(), "chat")
    assert daily["status"] == "COMPLETED" and not calls
    first = service.turn(request("2"), "council")
    assert first["status"] == "COMPLETED" and len(calls) == 4
    assert calls[0][0] == calls[1][0] == calls[2][0]
    assert "日常背景" in calls[0][0]
    assert not service.plans.list_current()
    receipt = confirm(service, first)
    assert receipt["committed"] and receipt["result_version"] == 1
    assert confirm(service, first)["duplicate"]
    assert service.turn(request("2"), "council")["id"] == first["id"]
    assert len(calls) == 4
    restarted = WebService(
        settings,
        "owner",
        council=service.council,
        session_factory=service.session_factory,
    )
    restarted.recover()
    old = restarted.plans.get(receipt["object_id"])
    assert old.source == "confirmed_web_proposal"
    assert "[任务]" in old.body and "任务 60 分钟" in old.body
    assert "本周六小时" in old.body and "实验环境是否就绪" in old.body
    assert "模型假设（待用户确认）" in old.body
    revision = restarted.turn(
        request("3", target_plan_id=old.id, expected_version=1), "council"
    )
    update = confirm(restarted, revision)
    assert update["result_version"] == 2
    assert restarted.plans.get(old.id, 1).body == old.body
    assert "缩减" in restarted.plans.get(old.id, 2).body
    assert session.agent.update_state.call_count == 2


def test_same_id_changed_input_rejected(env):
    _, service, _, calls = env
    service.turn(request(), "council")
    with pytest.raises(WebError, match="输入已改变"):
        service.turn(request().model_copy(update={"user_text": "changed"}), "council")
    assert len(calls) == 4


def test_unmatched_user_excerpt_never_becomes_web_proposal(env):
    _, service, _, _ = env
    original = service.council.invoke

    def invoke(system, payload, schema, *args):
        raw = original(system, payload, schema, *args)
        if schema is not Opinion:
            value = json.loads(raw)
            value["candidate"]["source_excerpts"][0]["quote"] = "用户已确认实验成功"
            return json.dumps(value)
        return raw

    service.council.invoke = invoke
    turn = service.turn(request(), "council")
    assert turn["status"] == "PARTIAL" and not turn.get("proposal_id")
    assert not service.plans.list_current()


def test_general_plan_save_restart_and_revision_without_time_fields(env):
    settings, service, _, _ = env
    original = service.council.invoke

    def invoke(system, payload, schema, *args):
        raw = original(system, payload, schema, *args)
        if schema is not Opinion:
            value = json.loads(raw)
            value["candidate"]["steps"] = [
                {"action": "比较方案", "done_when": "记录取舍", "kind": "采购"}
            ]
            return json.dumps(value)
        return raw

    service.council.invoke = invoke
    turn = service.turn(request(), "council")
    saved = confirm(service, turn)
    restarted = WebService(
        settings,
        "owner",
        council=service.council,
        session_factory=service.session_factory,
    )
    restarted.recover()
    old = restarted.plans.get(saved["object_id"])
    assert "[采购]" in old.body and "记录取舍" in old.body
    assert "合计" not in old.body and "旧版候选" not in old.body
    revised = restarted.turn(
        request("2", target_plan_id=old.id, expected_version=1), "council"
    )
    receipt = confirm(restarted, revised)
    assert receipt["result_version"] == 2
    assert restarted.plans.get(old.id, 1).body == old.body


def test_selected_original_source_is_frozen_and_saved(env):
    _, service, _, _ = env
    original = service.council.invoke

    def invoke(system, payload, schema, *args):
        raw = original(system, payload, schema, *args)
        if schema is not Opinion:
            value = json.loads(raw)
            value["candidate"].pop("source_excerpts")
            value["candidate"]["source_ids"] = ["source:0"]
            return json.dumps(value)
        return raw

    service.council.invoke = invoke
    turn = service.turn(request(), "council")
    saved = confirm(service, turn)
    assert request().user_text in service.plans.get(saved["object_id"]).body


@pytest.mark.parametrize(
    "change",
    [{"chat_id": "other"}, {"reply_message_id": "wrong"}, {"payload_hash": "0" * 64}],
)
def test_confirmation_binding(env, change):
    _, service, _, _ = env
    turn = service.turn(request(), "council")
    with pytest.raises(WebError):
        confirm(service, turn, **change)
    assert not service.plans.list_current()


def bump_epoch(service):
    with service.store.transaction() as db:
        db.execute("UPDATE app_state SET context_epoch=context_epoch+1")


def test_stale_unsaved_rejected_but_committed_replay_survives(env):
    _, service, _, _ = env
    one = service.turn(request(), "council")
    two = service.turn(request("2"), "council")
    saved = confirm(service, one)
    bump_epoch(service)
    assert confirm(service, one)["object_id"] == saved["object_id"]
    with pytest.raises(PersonalError):
        confirm(service, two)


def test_update_version_conflict_keeps_old_revision(env):
    _, service, _, _ = env
    first = confirm(service, service.turn(request(), "council"))
    p = first["object_id"]
    a = service.turn(request("2", target_plan_id=p, expected_version=1), "council")
    b = service.turn(request("3", target_plan_id=p, expected_version=1), "council")
    confirm(service, a)
    with pytest.raises(PersonalError):
        confirm(service, b)
    assert service.plans.get(p, 1).version == 1
    assert service.plans.get(p).version == 2


@pytest.mark.parametrize("failure_index", [0, 3])
def test_partial_no_candidate(env, failure_index):
    _, service, _, _ = env
    original = service.council.invoke
    index = 0

    def invoke(*args):
        nonlocal index
        current = index
        index += 1
        if current == failure_index:
            return "invalid json"
        return original(*args)

    service.council.invoke = invoke
    result = service.turn(request(), "council")
    assert result["status"] == "PARTIAL"
    assert not result["proposal_id"]
    assert "综合未完成" in result["text"]
    assert index <= 4


def test_busy_and_restart_do_not_replay(env):
    settings, service, _, calls = env
    service.gate.acquire()
    try:
        with pytest.raises(WebError) as error:
            service.turn(request(), "council")
        assert error.value.code == "BUSY"
    finally:
        service.gate.release()
    turn = service.turn(request(), "council")
    with service.store.transaction() as db:
        db.execute("UPDATE web_turns SET status='RUNNING' WHERE id=?", (turn["id"],))
    service.recover()
    assert service.turn(request(), "council")["status"] == "INTERRUPTED"
    assert len(calls) == 4


def test_sync_failure_repaired_without_model_repeat(env):
    _, service, session, calls = env
    session.agent.update_state.side_effect = RuntimeError("checkpoint unavailable")
    with pytest.raises(WebError):
        service.turn(request(), "council")
    assert len(calls) == 4
    session.agent.update_state.side_effect = None
    service.recover()
    assert service.turn(request(), "council")["synced"]
    assert len(calls) == 4


def test_epoch_change_does_not_sync_old_result(env):
    _, service, session, _ = env
    turn = service.turn(request(), "council")
    with service.store.transaction() as db:
        db.execute("UPDATE web_turns SET synced=0 WHERE id=?", (turn["id"],))
    bump_epoch(service)
    session.agent.update_state.reset_mock()
    service.recover()
    session.agent.update_state.assert_not_called()


def test_http_identity_and_history(env):
    settings, service, _, _ = env
    token = "test-token-which-is-long-enough"
    with TestClient(create_app(settings, token=token, service=service)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/v1/plans").status_code == 401
        headers = {"Authorization": "Bearer " + token}
        response = client.post("/v1/chat", json=request().model_dump(), headers=headers)
        assert response.status_code == 200
        assert (
            client.get("/v1/chats/study/turns", headers=headers).json()[0]["text"]
            == "普通回复"
        )
        bad = request().model_dump() | {"owner": "attacker"}
        assert client.post("/v1/chat", json=bad, headers=headers).status_code == 422


def test_other_owner_cannot_access_proposal_or_turn(env):
    settings, service, _, _ = env
    turn = service.turn(request(), "council")
    other = WebService(settings, "other")
    with pytest.raises(WebError):
        other.proposal(turn["proposal_id"], "study", turn["reply"])
    with pytest.raises(WebError):
        other.get_turn(turn["id"])


def test_unknown_evidence_and_oversized_snapshot(env):
    _, service, _, _ = env
    service.council.invoke = lambda *args: json.dumps(
        OPINION | {"evidence_refs": ["file:invented"]}
    )
    result = service.turn(request(), "council")
    assert result["status"] == "FAILED" and not result["proposal_id"]
    with pytest.raises(ValueError, match="过长"):
        service.council.run({"question": "x" * 20000})


def test_explicit_time_budget_rejects_oversized_candidate(env):
    _, service, _, _ = env
    result = service.turn(request(time_budget_minutes=30), "council")
    assert result["status"] == "PARTIAL"
    assert not result["proposal_id"]
    assert result["result"]["synthesis_error"] == "ValueError"


def test_read_connection_released(env):
    _, service, _, _ = env
    with service.store.connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    import sqlite3

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_old_branch_rejected_without_model_call(env):
    _, service, _, calls = env
    service.turn(request(), "council")
    with pytest.raises(WebError) as error:
        service.turn(
            request("2").model_copy(update={"parent_reply_message_id": "wrong"}),
            "council",
        )
    assert error.value.code == "BRANCH_CONFLICT"
    assert len(calls) == 4


def test_interrupted_run_preserves_finished_persona_without_replay(env):
    _, service, _, calls = env
    original = service.council.invoke

    def interrupted(*args):
        if calls:
            raise KeyboardInterrupt()
        return original(*args)

    service.council.invoke = interrupted
    with pytest.raises(KeyboardInterrupt):
        service.turn(request(), "council")
    service.recover()
    turn = service.turn(request(), "council")
    assert turn["status"] == "INTERRUPTED"
    assert len(turn["result"]["personas"]) == 1
    assert len(calls) == 1


def test_invalid_http_input_has_useful_code_without_echoing_private_values(env):
    settings, service, session, calls = env
    token = "test-token-which-is-long-enough"
    with TestClient(create_app(settings, token=token, service=service)) as client:
        response = client.post(
            "/v1/chat",
            headers={"Authorization": "Bearer " + token},
            json=request().model_dump()
            | {"user_text": "", "private": "PRIVATE-INVALID-VALUE"},
        )
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "INVALID_INPUT" and payload["message"]
    assert "PRIVATE-INVALID-VALUE" not in json.dumps(payload)
    assert {e["field"] for e in payload["fields"]} == {"body.user_text", "body.private"}
    assert not service.history("study")
    session.ask.assert_not_called()
    assert not calls
