"""真实Deep Agents图与SQLite续接；三人格和聊天内容用脚本模型，不访问云端。"""

from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage

from magi.harness.runtime import open_personal_session
from magi.web.service import WebError, WebService
from tests.test_harness_runtime import ScriptedModel
from tests.test_web_mvp import confirm, request
from tests.test_web_mvp import env as env


@pytest.mark.parametrize("daily_first", [False, True])
def test_completed_council_can_continue_and_restore_real_checkpoint(env, daily_first):
    """新会话先审议或先聊天均能反馈修订；注入历史不额外调用日常模型。"""
    settings, service, _, calls = env
    model = ScriptedModel(responses=[AIMessage(content="日常回复")])

    @contextmanager
    def factory(settings, logical):
        with open_personal_session(settings, logical, model=model) as session:
            yield session

    service.session_factory = factory
    number = 1
    if daily_first:
        assert service.turn(request(), "chat")["status"] == "COMPLETED"
        number += 1
    first = service.turn(request(str(number)), "council")
    receipt = confirm(service, first)
    with factory(settings, service.logical_id("study")) as session:
        assert not session.agent.get_state(session.config).next
        first_ids = [m.id for m in session.messages()]
    # 模拟业务提交后synced标记尚未写入便退出；恢复必须以相同消息ID重写。
    with service.store.transaction() as db:
        db.execute("UPDATE web_turns SET synced=0 WHERE id=?", (first["id"],))
    restored = WebService(
        settings, "owner", council=service.council, session_factory=factory
    )
    restored.recover()
    with factory(settings, restored.logical_id("study")) as session:
        assert not session.agent.get_state(session.config).next
        assert [m.id for m in session.messages()] == first_ids
    revised = restored.turn(
        request(
            str(number + 1), target_plan_id=receipt["object_id"], expected_version=1
        ),
        "council",
    )
    assert revised["status"] == "COMPLETED"
    assert confirm(restored, revised)["result_version"] == 2
    assert len(calls) == 8
    assert len(model.requests) == int(daily_first)


def test_sync_interruption_before_end_is_repaired_without_generation(env):
    """写消息后、结束调度前失败，synced保持未完成，恢复重写不重复消息或生成。"""
    settings, service, _, calls = env
    model = ScriptedModel(responses=[])
    fail = [True]

    @contextmanager
    def factory(settings, logical):
        with open_personal_session(settings, logical, model=model) as session:
            graph = session.agent

            def update(config, values, **kwargs):
                if values is None and fail[0]:
                    raise RuntimeError("simulated failure before END")
                return graph.update_state(config, values, **kwargs)

            session.agent = Mock(wraps=graph)
            session.agent.update_state.side_effect = update
            yield session

    service.session_factory = factory
    with pytest.raises(WebError):
        service.turn(request(), "council")
    assert len(calls) == 4
    with factory(settings, service.logical_id("study")) as session:
        assert session.agent.get_state(session.config).next
        assert len(session.messages()) == 2
    fail[0] = False
    service.recover()
    with factory(settings, service.logical_id("study")) as session:
        assert not session.agent.get_state(session.config).next
        assert len(session.messages()) == 2
    assert service.turn(request(), "council")["synced"]
    assert len(calls) == 4 and not model.requests
