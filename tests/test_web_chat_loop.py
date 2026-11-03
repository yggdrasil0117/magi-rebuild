"""用脚本模型走网页默认会话，验证工具配对及重启后的历史。"""

from dataclasses import replace

import pytest
from httpx import ConnectError, ReadTimeout
from langchain_core.messages import AIMessage, ToolMessage

from magi.harness.runtime import open_personal_session
from magi.web.service import WebError, WebService
from tests.test_harness_runtime import ScriptedModel, calls
from tests.test_web_mvp import confirm, request
from tests.test_web_mvp import env as env


def test_web_default_reads_file_and_restores_tool_messages(env, monkeypatch):
    settings, _, _, _ = env
    (settings.docs_root / "note.txt").write_text("NOTE-731", encoding="utf-8")
    model = ScriptedModel(
        responses=[
            calls(("magi_read_file", {"path": "note.txt"})),
            AIMessage(content="资料已读"),
            AIMessage(content="继续回答"),
        ]
    )
    monkeypatch.setattr("magi.harness.runtime.build_model", lambda _: model)
    service = WebService(settings, "owner")
    assert service.turn(request(), "chat")["text"] == "资料已读"
    restored = WebService(settings, "owner")
    restored.recover()
    assert restored.turn(request(), "chat")["text"] == "资料已读"
    assert restored.turn(request("2"), "chat")["text"] == "继续回答"
    for messages in model.requests[1:]:
        result = next(m for m in messages if isinstance(m, ToolMessage))
        assert result.tool_call_id == "call-0" and "NOTE-731" in result.text
    assert len(model.requests) == 3


def test_web_default_rejects_entire_invalid_batch(env, monkeypatch):
    settings, _, _, _ = env
    model = ScriptedModel(
        responses=[
            calls(("magi_list_files", {"path": "."}), ("magi_read_file", {"path": 42})),
            AIMessage(content="新一轮回答"),
        ]
    )
    monkeypatch.setattr("magi.harness.runtime.build_model", lambda _: model)
    service = WebService(settings, "owner")
    with pytest.raises(WebError) as failure:
        service.turn(request(), "chat")
    assert service.get_turn(failure.value.run_id)["status"] == "FAILED"
    log = settings.events_path.read_text(encoding="utf-8")
    assert '"stage": "tool_validation"' in log and "tool_started" not in log
    assert service.turn(request("2"), "chat")["text"] == "新一轮回答"
    assert len(model.requests[-1]) == 2


def test_connection_retry_does_not_reexecute_completed_tool(env, monkeypatch):
    settings, _, _, _ = env
    model = ScriptedModel(
        responses=[
            calls(("magi_list_files", {"path": "."})),
            AIMessage(content="已读取目录"),
        ]
    )
    original = ScriptedModel._generate
    failed = False

    def generate(self, messages, *args, **kwargs):
        nonlocal failed
        if len(self.requests) == 1 and not failed:
            failed = True
            raise ConnectError("模拟尚未建立连接")
        return original(self, messages, *args, **kwargs)

    monkeypatch.setattr(ScriptedModel, "_generate", generate)
    monkeypatch.setattr("magi.harness.runtime.build_model", lambda _: model)
    assert WebService(settings, "owner").turn(request(), "chat")["text"] == "已读取目录"
    log = settings.events_path.read_text(encoding="utf-8")
    assert log.count('"type": "model_request"') == 3
    assert log.count('"type": "model_retry"') == 1
    assert log.count('"type": "tool_started"') == 1


def test_preview_survives_later_model_failure_without_replay(env, monkeypatch):
    settings, existing, _, _ = env
    plan = confirm(existing, existing.turn(request(), "council"))
    model = ScriptedModel(
        responses=[
            calls(
                (
                    "magi_prepare_task_document",
                    {
                        "plan_id": plan["object_id"],
                        "version": 1,
                    },
                )
            ),
        ]
    )
    original = ScriptedModel._generate

    def generate(self, messages, *args, **kwargs):
        if self.requests:
            self.requests.append(messages)
            raise ReadTimeout("模拟读取模型响应超时")
        return original(self, messages, *args, **kwargs)

    monkeypatch.setattr(ScriptedModel, "_generate", generate)
    monkeypatch.setattr("magi.harness.runtime.build_model", lambda _: model)
    service = WebService(settings, "owner")
    with pytest.raises(WebError):
        service.turn(request("2"), "chat")
    restored = WebService(settings, "owner")
    restored.recover()
    turn = restored.turn(request("2"), "chat")
    assert turn["status"] == "FAILED"
    assert len(turn["documents"]) == 1
    assert turn["documents"][0]["status"] == "PREVIEW"
    assert len(model.requests) == 2


def test_failed_commit_leaves_no_messages_and_replay_does_not_run_model(
    env, monkeypatch
):
    settings, _, _, _ = env
    model = ScriptedModel(responses=[AIMessage(content="不应入库")])
    monkeypatch.setattr("magi.harness.runtime.build_model", lambda _: model)
    service = WebService(settings, "owner")
    with service.store.transaction() as db:
        db.execute("""
            CREATE TRIGGER fail_completion BEFORE UPDATE OF status ON web_turns
            WHEN NEW.status='COMPLETED' BEGIN SELECT RAISE(ABORT, 'test failure'); END
        """)
    with pytest.raises(WebError):
        service.turn(request(), "chat")
    restored = WebService(settings, "owner")
    restored.recover()
    assert restored.turn(request(), "chat")["status"] == "FAILED"
    with restored.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM web_chat_history").fetchone()[0] == 0
    assert len(model.requests) == 1


def test_response_lost_after_commit_returns_original_turn(env, monkeypatch):
    settings, _, _, _ = env
    model = ScriptedModel(responses=[AIMessage(content="原始回答")])
    monkeypatch.setattr("magi.harness.runtime.build_model", lambda _: model)
    service = WebService(settings, "owner")

    def response_lost(*args):
        raise OSError("模拟完成提交后响应失败")

    monkeypatch.setattr(service, "get_turn", response_lost)
    with pytest.raises(WebError):
        service.turn(request(), "chat")
    restored = WebService(settings, "owner")
    restored.recover()
    turn = restored.turn(request(), "chat")
    assert (turn["status"], turn["text"]) == ("COMPLETED", "原始回答")
    with restored.store.connect() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM web_chat_history WHERE turn_id=?", (turn["id"],)
            ).fetchone()[0]
            == 1
        )
    assert len(model.requests) == 1


def test_legacy_tool_history_imported_once_without_graph_and_council_continues(
    env, monkeypatch
):
    settings, existing, _, _ = env
    settings = replace(settings, num_ctx=32768)
    (settings.docs_root / "old.txt").write_text("OLD-731", encoding="utf-8")
    legacy = ScriptedModel(
        responses=[
            calls(("magi_read_file", {"path": "old.txt"})),
            AIMessage(content="旧回答"),
            calls(("magi_read_file", {"path": "missing.txt"})),
        ]
    )
    with open_personal_session(
        settings, existing.logical_id("study"), model=legacy
    ) as old:
        assert old.ask("旧问题") == "旧回答"
        with pytest.raises(Exception, match="missing|不存在"):
            old.ask("未完成问题")

    def no_graph(*args, **kwargs):
        raise AssertionError("网页不应构造执行图")

    model = ScriptedModel(responses=[AIMessage(content="接着讨论")])
    monkeypatch.setattr("magi.harness.runtime.build_model", lambda _: model)
    service = WebService(settings, "owner", council=existing.council)
    council = service.turn(request(), "council")
    assert council["synced"] and council["status"] == "COMPLETED"
    monkeypatch.setattr("magi.harness.runtime.create_deep_agent", no_graph)
    restored = WebService(settings, "owner")
    restored.recover()
    assert restored.turn(request("2"), "chat")["text"] == "接着讨论"
    messages = model.requests[0]
    assert sum(m.text == "旧问题" for m in messages) == 1
    assert all(m.text != "未完成问题" for m in messages)
    assert any(isinstance(m, ToolMessage) and "OLD-731" in m.text for m in messages)
    assert any(m.text.startswith("三人格综合") for m in messages)
    with restored.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM web_chat_history").fetchone()[0] == 3
