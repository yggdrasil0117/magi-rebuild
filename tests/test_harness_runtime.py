# ruff: noqa: E402
"""Offline integration checks against the real Deep Agents graph and SQLite saver."""

import json
import socket
from copy import deepcopy
from dataclasses import replace

import pytest

pytest.importorskip("deepagents", reason="Install the optional harness dependencies")

from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from magi.harness.config import HarnessConfig
from magi.harness.runtime import open_personal_session, open_session


class ScriptedModel(BaseChatModel):
    """Implements the chat-model boundary while recording actual graph inputs."""

    model: str = "test"
    responses: list[AIMessage]
    requests: list = Field(default_factory=list)
    tool_bindings: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "magi-offline-scripted"

    def _get_ls_params(self, stop=None, **kwargs):
        return {"ls_provider": "ollama", "ls_model_name": self.model}

    def bind_tools(self, tools, **kwargs):
        self.tool_bindings.append(
            [convert_to_openai_tool(tool)["function"]["name"] for tool in tools]
        )
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.requests.append(deepcopy(messages))
        index = len(self.requests) - 1
        if index >= len(self.responses):
            raise AssertionError("The graph made an unexpected extra model request")
        return ChatResult(
            generations=[
                ChatGeneration(message=self.responses[index].model_copy(deep=True))
            ]
        )


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject_network(*args, **kwargs):
        raise AssertionError("Harness integration tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_network)


@pytest.fixture
def settings(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.txt").write_text("本周阅读资料，识别码 NOTE-731。", encoding="utf-8")
    return HarnessConfig(
        docs_root=docs,
        db_path=tmp_path / "state" / "conversation.db",
        events_path=tmp_path / "state" / "events.jsonl",
    )


def calls(*specifications):
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": arguments, "id": f"call-{index}"}
            for index, (name, arguments) in enumerate(specifications)
        ],
    )


def event_types(session):
    return [event["type"] for event in session.events.records]


def assert_failed(session):
    events = event_types(session)
    assert events[-1] == "run_failed"
    assert "run_completed" not in events


def test_sqlite_reopen_restores_history_without_duplicating_user_messages(settings):
    first_model = ScriptedModel(responses=[AIMessage(content="已了解你的研究目标。")])
    with open_session(settings, "study", model=first_model) as first:
        first.ask("我的研究方向是规约验证。")

    second_model = ScriptedModel(responses=[AIMessage(content="继续讨论规约验证。")])
    with open_session(settings, "study", model=second_model) as reopened:
        assert [m.text for m in reopened.messages() if isinstance(m, HumanMessage)] == [
            "我的研究方向是规约验证。"
        ]
        reopened.ask("继续刚才的话题。")
        assert [m.text for m in reopened.messages() if isinstance(m, HumanMessage)] == [
            "我的研究方向是规约验证。",
            "继续刚才的话题。",
        ]
        assert len(reopened.messages()) == 4

    prompt = second_model.requests[0]
    assert [m.text for m in prompt if isinstance(m, HumanMessage)] == [
        "我的研究方向是规约验证。",
        "继续刚才的话题。",
    ]
    assert any(m.text == "已了解你的研究目标。" for m in prompt)
    assert settings.db_path.stat().st_size > 0


def test_different_session_ids_do_not_share_messages(settings):
    with open_session(
        settings,
        "first",
        model=ScriptedModel(responses=[AIMessage(content="第一会话。")]),
    ) as first:
        first.ask("只属于第一会话的内容。")

    model = ScriptedModel(responses=[AIMessage(content="第二会话。")])
    with open_session(settings, "second", model=model) as second:
        assert second.messages() == []
        second.ask("你好。")
    assert all("只属于第一会话" not in message.text for message in model.requests[0])


def test_existing_file_tools_are_used_and_only_read_tools_are_exposed(settings):
    model = ScriptedModel(
        responses=[
            calls(("magi_list_files", {"path": "."})),
            calls(("magi_read_file", {"path": "note.txt"})),
            AIMessage(content="识别码为 NOTE-731，来源 note.txt。"),
        ]
    )
    with open_session(settings, "files", model=model) as session:
        assert "NOTE-731" in session.ask("列出目录并读取 note.txt。")
        messages = session.messages()
        assert event_types(session).count("tool_succeeded") == 2
        assert event_types(session)[-1] == "run_completed"

    assert model.tool_bindings
    assert all(
        set(tools) == {"magi_read_file", "magi_list_files", "magi_search_files"}
        for tools in model.tool_bindings
    )
    returned = [m for m in messages if isinstance(m, ToolMessage)]
    assert "note.txt" in returned[0].text
    assert returned[1].text == "本周阅读资料，识别码 NOTE-731。"
    assert any(
        isinstance(m, ToolMessage) and "NOTE-731" in m.text for m in model.requests[-1]
    )
    # Runtime logs contain operational metadata, not the private file body or prompt.
    event_log = settings.events_path.read_text(encoding="utf-8")
    assert "NOTE-731" not in event_log
    records = [json.loads(line) for line in event_log.splitlines()]
    assert all(record["session_id"] == "files" for record in records)


@pytest.mark.parametrize("limit, count", [(0, 1), (1, 2)])
def test_excessive_tool_batch_is_rejected_before_any_execution(settings, limit, count):
    model = ScriptedModel(
        responses=[calls(*[("magi_read_file", {"path": "note.txt"})] * count)]
    )
    with open_session(
        replace(settings, max_tool_calls=limit), "budget", model=model
    ) as session:
        with pytest.raises(ToolCallLimitExceededError):
            session.ask("读取资料。")
        assert "tool_started" not in event_types(session)
        assert "tool_succeeded" not in event_types(session)
        assert len(model.requests) == 1
        assert_failed(session)


def test_model_budget_stops_before_extra_generation(settings):
    model = ScriptedModel(responses=[calls(("magi_read_file", {"path": "note.txt"}))])
    with open_session(
        replace(settings, max_model_calls=1), "model-budget", model=model
    ) as session:
        with pytest.raises(ModelCallLimitExceededError):
            session.ask("读资料再回答。")
        assert len(model.requests) == 1
        assert_failed(session)


def test_budgets_reset_for_each_user_turn(settings):
    model = ScriptedModel(
        responses=[
            calls(("magi_read_file", {"path": "note.txt"})),
            AIMessage(content="第一次阅读完成。"),
            calls(("magi_read_file", {"path": "note.txt"})),
            AIMessage(content="第二次阅读完成。"),
        ]
    )
    with open_session(
        replace(settings, max_model_calls=2, max_tool_calls=1),
        "reset-budget",
        model=model,
    ) as session:
        assert session.ask("第一次读资料。") == "第一次阅读完成。"
        first_run = session.events.run_id
        assert session.ask("重新读资料。") == "第二次阅读完成。"
        assert session.events.run_id != first_run
        assert event_types(session).count("model_request") == 2
        assert event_types(session).count("tool_succeeded") == 1


@pytest.mark.parametrize("name", ["write_file", "execute", "task", "made_up_tool"])
def test_unexposed_tool_cannot_be_requested_by_the_model(settings, name):
    model = ScriptedModel(
        responses=[
            calls(
                ("magi_read_file", {"path": "note.txt"}),
                (name, {"path": "evil.txt", "content": "must not be written"}),
            )
        ]
    )
    with open_session(settings, "forbidden", model=model) as session:
        with pytest.raises(PermissionError):
            session.ask("执行请求。")
        assert "tool_started" not in event_types(session)
        assert "tool_rejected" in event_types(session)
        assert_failed(session)
    assert not (settings.docs_root / "evil.txt").exists()


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": "../outside.txt"},
        {"path": "missing.txt"},
        {"path": ""},
        {"path": 1},
        {"path": "note.txt", "unexpected": True},
    ],
)
def test_invalid_file_requests_fail_without_recording_success(settings, arguments):
    (settings.docs_root.parent / "outside.txt").write_text("private", encoding="utf-8")
    model = ScriptedModel(responses=[calls(("magi_read_file", arguments))])
    with open_session(settings, "bad-read", model=model) as session:
        with pytest.raises(ValueError):
            session.ask("读取资料。")
        assert "tool_failed" in event_types(session)
        assert "tool_succeeded" not in event_types(session)
        assert_failed(session)
        assert len(model.requests) == 1


def test_unfinished_checkpoint_is_not_automatically_replayed(settings):
    failed_model = ScriptedModel(
        responses=[calls(("magi_read_file", {"path": "missing.txt"}))]
    )
    with open_session(settings, "interrupted", model=failed_model) as session:
        with pytest.raises(ValueError):
            session.ask("读取不存在的文件。")

    unused_model = ScriptedModel(responses=[])
    with open_session(settings, "interrupted", model=unused_model) as reopened:
        before = [message.model_dump() for message in reopened.messages()]
        with pytest.raises(RuntimeError, match="不自动重放"):
            reopened.ask("继续执行。")
        assert unused_model.requests == []
        assert [message.model_dump() for message in reopened.messages()] == before
        assert "tool_started" not in event_types(reopened)
        assert_failed(reopened)


def test_empty_final_answer_is_not_success(settings):
    model = ScriptedModel(responses=[AIMessage(content=" \n ")])
    with open_session(settings, "empty-answer", model=model) as session:
        with pytest.raises(ValueError, match="完整文本回答"):
            session.ask("你好。")
        assert_failed(session)


@pytest.mark.parametrize(
    "metadata", [{"done_reason": "length"}, {"finish_reason": "length"}]
)
def test_nonempty_truncated_output_is_not_success(settings, metadata):
    model = ScriptedModel(
        responses=[AIMessage(content="这段话尚未", response_metadata=metadata)]
    )
    with open_session(settings, "truncated", model=model) as session:
        with pytest.raises(ValueError, match="回答不完整"):
            session.ask("你好。")
        assert len(model.requests) == 1
        assert_failed(session)


def test_invalid_tool_call_is_not_treated_as_a_text_answer(settings):
    model = ScriptedModel(
        responses=[
            AIMessage(
                content="我已经读过文件。",
                invalid_tool_calls=[
                    {"name": "magi_read_file", "args": "not-json", "id": "bad"}
                ],
            )
        ]
    )
    with open_session(settings, "invalid-call", model=model) as session:
        with pytest.raises(ValueError, match="无法解析"):
            session.ask("读文件。")
        assert "tool_started" not in event_types(session)
        assert_failed(session)


def test_oversized_user_input_is_rejected_before_model_call(settings):
    model = ScriptedModel(responses=[])
    with open_session(settings, "long-input", model=model) as session:
        with pytest.raises(ValueError, match="容量预算"):
            session.ask("超长" * 5000)
        assert model.requests == []
        assert "context_budget_rejected" in event_types(session)
        assert "model_request" not in event_types(session)
        assert_failed(session)


def test_large_tool_result_is_not_silently_sent_into_a_small_window(settings):
    (settings.docs_root / "large.txt").write_text("x" * 12000, encoding="utf-8")
    model = ScriptedModel(responses=[calls(("magi_read_file", {"path": "large.txt"}))])
    with open_session(settings, "long-result", model=model) as session:
        with pytest.raises(ValueError, match="容量预算"):
            session.ask("读取 large.txt。")
        assert len(model.requests) == 1
        assert "tool_succeeded" in event_types(session)
        assert "context_budget_rejected" in event_types(session)
        assert_failed(session)


def test_capacity_check_includes_recovered_history(settings):
    with open_session(
        settings,
        "long-history",
        model=ScriptedModel(responses=[AIMessage(content="A" * 1000)]),
    ) as first:
        first.ask("Q" * 2000)

    unused_model = ScriptedModel(responses=[])
    with open_session(settings, "long-history", model=unused_model) as reopened:
        with pytest.raises(ValueError, match="容量预算"):
            reopened.ask("R" * 4000)
        assert unused_model.requests == []
        assert "context_budget_rejected" in event_types(reopened)
        assert_failed(reopened)

    # The same new input fits without the recovered history.
    fresh_model = ScriptedModel(responses=[AIMessage(content="ok")])
    with open_session(settings, "fresh-history", model=fresh_model) as fresh:
        assert fresh.ask("R" * 4000) == "ok"


def test_memory_epoch_rebuilds_thread_without_old_history(settings):
    from magi.personal.memory import MemoryService
    from magi.personal.store import PersonalStore

    old_model = ScriptedModel(responses=[AIMessage(content="OLD-SECRET acknowledged")])
    with open_personal_session(settings, "study", model=old_model) as old:
        old_thread = old.config["configurable"]["thread_id"]
        old.ask("OLD-SECRET belongs only to old history")

    memory = MemoryService(PersonalStore(settings.business_db_path))
    created = memory.add(
        key="preference", scope="global", value="CURRENT-ONLY", request_id="add"
    )
    new_model = ScriptedModel(responses=[AIMessage(content="using current memory")])
    with open_personal_session(settings, "study", model=new_model) as current:
        assert current.config["configurable"]["thread_id"] != old_thread
        current.ask("what is current?")

    serialized = repr(new_model.requests[0])
    assert "CURRENT-ONLY" in serialized
    assert "OLD-SECRET" not in serialized

    memory.delete(created.object_id, expected_version=1, request_id="delete")
    deleted_model = ScriptedModel(responses=[AIMessage(content="no memory")])
    with open_personal_session(settings, "study", model=deleted_model) as deleted:
        deleted.ask("what remains?")
    serialized = repr(deleted_model.requests[0])
    assert "CURRENT-ONLY" not in serialized
    assert "OLD-SECRET" not in serialized


def test_personal_session_exposes_read_only_plan_tools(settings):
    from magi.personal.memory import MemoryService
    from magi.personal.plans import PlanService
    from magi.personal.store import PersonalStore

    store = PersonalStore(settings.business_db_path)
    memory = MemoryService(store)
    plan = PlanService(store).create(
        title="Study",
        body="PLAN-BODY-771",
        request_id="plan",
        source_epoch=memory.current_epoch(),
    )
    model = ScriptedModel(
        responses=[
            calls(("magi_get_plan", {"plan_id": plan.object_id})),
            AIMessage(content="I found the saved plan."),
        ]
    )
    with open_personal_session(settings, "plans", model=model) as session:
        session.ask("read my plan")
    assert "PLAN-BODY-771" in repr(model.requests[-1])
    assert all(
        set(tools)
        == {
            "magi_read_file",
            "magi_list_files",
            "magi_search_files",
            "magi_list_plans",
            "magi_get_plan",
        }
        for tools in model.tool_bindings
    )

    memory.add(key="changed", scope="global", value="new", request_id="memory")
    withheld = ScriptedModel(
        responses=[
            calls(("magi_get_plan", {"plan_id": plan.object_id})),
            AIMessage(content="This plan needs review."),
        ]
    )
    with open_personal_session(settings, "plans", model=withheld) as session:
        session.ask("read my old plan")
    tool_input = repr(withheld.requests[-1])
    assert "body_withheld" in tool_input
    assert "PLAN-BODY-771" not in tool_input
