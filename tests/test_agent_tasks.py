"""验证任务入口、请求去重和恢复后的执行边界。"""

from threading import Event

import pytest
from fastapi.testclient import TestClient

from magi.web.app import create_app
from magi.web.service import WebError
from magi.web.tasks import AgentTasks
from tests.test_web_mvp import env as env
from tests.test_web_mvp import request


def test_task_http_accepts_before_generation_and_replays_original_id(env):
    settings, service, session, _ = env
    entered, release = Event(), Event()

    def answer(_):
        entered.set()
        if not release.wait(10):
            raise TimeoutError
        return "后台回答"

    session.ask.side_effect = answer
    token = "isolated-agent-queue-test-token"
    headers = {"Authorization": "Bearer " + token}
    body = {"mode": "chat", "turn": request().model_dump()}
    with TestClient(create_app(settings, token=token, service=service)) as client:
        try:
            assert client.post("/v1/tasks", json=body).status_code == 401
            result = client.post("/v1/tasks", json=body, headers=headers)
            assert result.status_code == 202
            task_id = result.json()["id"]
            assert entered.wait(5)
            assert (
                client.get(f"/v1/tasks/{task_id}", headers=headers).json()["status"]
                == "RUNNING"
            )
            assert (
                client.post("/v1/tasks", json=body, headers=headers).json()["id"]
                == task_id
            )
            conflict = {
                "mode": "chat",
                "turn": body["turn"] | {"user_text": "不同内容"},
            }
            assert (
                client.post("/v1/tasks", json=conflict, headers=headers).status_code
                == 409
            )
            assert (
                client.get("/v1/chats/study/tasks", headers=headers).json()[0]["id"]
                == task_id
            )
        finally:
            release.set()
    task = AgentTasks(service).get(task_id)
    assert task["status"] == "COMPLETED"
    assert service.get_turn(task["turn_id"])["text"] == "后台回答"
    session.ask.assert_called_once()


def test_task_epoch_change_fails_before_model_and_releases_chat(env):
    _, service, session, _ = env
    queue = AgentTasks(service)
    task = queue.submit(request(), "chat")
    with pytest.raises(WebError, match="后台任务"):
        service.turn(request(), "chat")
    with service.store.transaction() as db:
        db.execute(
            "UPDATE app_state SET context_epoch=context_epoch+1 WHERE singleton=1"
        )
    assert queue.run_one()
    assert queue.get(task["id"])["error_code"] == "CONTEXT_CHANGED"
    assert queue.get(task["id"])["status"] == "FAILED"
    assert not service.history("study")
    session.ask.assert_not_called()


def test_queue_does_not_take_unrelated_turn_as_recovery_receipt(env):
    _, service, session, _ = env
    original = service.turn(request(), "chat")
    changed = request().model_copy(update={"user_text": "不同消息内容"})
    queue = AgentTasks(service)
    task = queue.submit(changed, "chat")
    assert queue.claim()["id"] == task["id"]
    queue.recover()
    result = queue.get(task["id"])
    assert result["status"] == "INTERRUPTED"
    assert result["turn_id"] is None
    assert original["status"] == "COMPLETED"
    session.ask.assert_called_once()
