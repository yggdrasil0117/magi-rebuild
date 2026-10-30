"""真实Deep Agents图执行脚本模型调用，检验预览与文件确认边界。"""

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from magi.personal.documents import TaskDocuments
from magi.personal.models import PersonalError
from magi.web.app import create_app
from magi.web.schemas import TurnInput
from magi.web.service import WebError, WebService
from tests.test_harness_runtime import ScriptedModel, calls
from tests.test_task_documents import setup as setup


def user_request(text="准备工作单"):
    """固定消息用于验证同消息重试不再次推理或重复准备。"""
    return TurnInput(
        chat_id="study", message_id="u1", reply_message_id="a1", user_text=text
    )


def use_model(monkeypatch, responses):
    """只替换模型边界，保留真实会话、工具执行器、checkpoint和业务库。"""
    model = ScriptedModel(responses=responses)
    monkeypatch.setattr("magi.harness.runtime.build_model", lambda settings: model)
    return model


def test_model_prepares_preview_and_page_confirms(setup, monkeypatch):
    settings, service, plan_id = setup
    model = use_model(
        monkeypatch,
        [
            calls(("magi_prepare_task_document", {"plan_id": plan_id, "version": 1})),
            AIMessage(content="预览已准备，请在页面查看并确认。"),
        ],
    )
    turn = service.turn(user_request(), "chat")
    assert turn["status"] == "COMPLETED" and len(turn["documents"]) == 1
    document = turn["documents"][0]
    assert not service.documents.directory.exists()
    assert len(model.requests) == 2
    assert "file_created_by_this_call" in repr(model.requests[-1])
    assert "实现接口；完成条件" not in repr(model.requests[-1])
    assert all("magi_prepare_task_document" in tools for tools in model.tool_bindings)
    assert all(
        not any("confirm" in tool for tool in tools) for tools in model.tool_bindings
    )
    assert service.turn(user_request(), "chat")["documents"] == turn["documents"]
    assert len(model.requests) == 2
    token = "offline-document-calling-test-token"
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(settings, service=service, token=token)) as client:
        preview = client.get("/v1/documents/" + document["id"], headers=headers).json()
        result = client.post(
            "/v1/documents/" + document["id"] + "/confirm",
            headers=headers,
            json={"confirmed": True, "payload_hash": preview["payload_hash"]},
        )
        assert result.status_code == 200 and result.json()["verified"]
    reopened = WebService(settings, service.owner)
    assert reopened.history("study")[0]["documents"][0]["status"] == "CREATED"
    assert reopened.history("other-chat") == []
    assert TaskDocuments(service.store, "other-owner").for_turn(turn["id"]) == []


def test_repeated_tool_calls_in_one_turn_share_one_preview(setup, monkeypatch):
    _, service, plan_id = setup
    spec = ("magi_prepare_task_document", {"plan_id": plan_id, "version": 1})
    use_model(monkeypatch, [calls(spec, spec), AIMessage(content="待确认")])
    turn = service.turn(user_request(), "chat")
    assert len(turn["documents"]) == 1
    assert not service.documents.directory.exists()


def test_normal_answer_does_not_prepare_document(setup, monkeypatch):
    _, service, _ = setup
    use_model(monkeypatch, [AIMessage(content="你好。")])
    assert service.turn(user_request("你好"), "chat")["documents"] == []
    assert not service.documents.directory.exists()


@pytest.mark.parametrize(
    "name,args",
    [
        ("magi_confirm_document", {"confirmed": True}),
        ("magi_prepare_task_document", {"plan_id": "missing", "version": 1}),
        (
            "magi_prepare_task_document",
            {"plan_id": "missing", "version": 1, "confirmed": True},
        ),
        ("magi_prepare_task_document", {"plan_id": "missing", "version": "1"}),
        (
            "magi_prepare_task_document",
            {"plan_id": "valid", "version": 1, "owner": "other"},
        ),
        (
            "magi_prepare_task_document",
            {"plan_id": "valid", "version": 1, "path": "../outside.md"},
        ),
    ],
)
def test_unregistered_tools_and_invalid_arguments_do_not_write(
    setup, monkeypatch, name, args
):
    _, service, plan_id = setup
    if name == "magi_prepare_task_document" and (
        len(args) > 2 or isinstance(args["version"], str)
    ):
        args = {**args, "plan_id": plan_id}  # 使用真实计划，确保拒绝来自参数边界。
    use_model(monkeypatch, [calls((name, args))])
    with pytest.raises((WebError, PersonalError)):
        service.turn(user_request(), "chat")
    assert service.history("study")[0]["status"] == "FAILED"
    assert service.history("study")[0]["documents"] == []
    assert not service.documents.directory.exists()


def test_failed_final_answer_keeps_real_preview(setup, monkeypatch):
    settings, service, plan_id = setup
    use_model(
        monkeypatch,
        [calls(("magi_prepare_task_document", {"plan_id": plan_id, "version": 1}))],
    )
    with pytest.raises(WebError):
        service.turn(user_request(), "chat")
    history = service.history("study")
    assert history[0]["status"] == "FAILED" and len(history[0]["documents"]) == 1
    reopened = WebService(settings, service.owner)
    reopened.recover()
    assert reopened.history("study")[0]["documents"] == history[0]["documents"]
    assert not service.documents.directory.exists()


def test_model_claim_is_not_tool_evidence(setup, monkeypatch):
    _, service, _ = setup
    use_model(monkeypatch, [AIMessage(content="我已经生成了工作单。")])
    assert service.turn(user_request(), "chat")["documents"] == []


def test_cannot_bind_preview_to_other_owner_turn(setup, monkeypatch):
    _, service, plan_id = setup
    use_model(monkeypatch, [AIMessage(content="你好")])
    turn = service.turn(user_request(), "chat")
    other = TaskDocuments(service.store, "other")
    with service.store.transaction() as db:
        db.execute("UPDATE web_turns SET status='RUNNING' WHERE id=?", (turn["id"],))
    with pytest.raises(PersonalError):
        other.preview(plan_id, 1, "fake", source_turn_id=turn["id"])


def test_schema5_upgrade_preserves_manual_preview(setup):
    _, service, plan_id = setup
    old = service.documents.preview(plan_id, 1, "manual")
    with service.store.transaction() as db:
        db.execute("DROP INDEX task_documents_turn")
        db.execute("ALTER TABLE task_documents DROP COLUMN source_turn_id")
        db.execute("UPDATE schema_meta SET version=5")
    assert service.documents.get(old["id"]) == old
    assert old["source_turn_id"] is None
    with service.store.connect() as db:
        assert db.execute("SELECT version FROM schema_meta").fetchone()[0] == 6
