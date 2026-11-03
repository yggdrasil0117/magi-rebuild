"""验证状态查询在模型运行期间可用，且查询和重复发送都不再生成。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

from magi.web.app import create_app
from magi.web.service import WebService
from tests.test_web_mvp import env as env
from tests.test_web_mvp import request


def test_turn_status_reads_committed_lifecycle_without_replaying(env):
    settings, service, session, _ = env
    entered, release = Event(), Event()

    def answer(_text):
        entered.set()
        if not release.wait(10):
            raise TimeoutError
        return "已经生成并保存的回答"

    session.ask.side_effect = answer
    token = "isolated-turn-status-test-token"
    headers = {"Authorization": "Bearer " + token}
    path = "/v1/chats/study/turn-status?message_id=u1"
    body = request().model_dump()
    with TestClient(create_app(settings, token=token, service=service)) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=headers).json() == {"turn": None}
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                client.post, "/v1/chat", json=body, headers=headers
            )
            try:
                assert entered.wait(5)
                running = client.get(path, headers=headers).json()["turn"]
                assert running["status"] == "RUNNING"
                assert set(running) == {"id", "status", "created_at"}
                assert WebService(settings, "other").turn_status("study", "u1") == {
                    "turn": None
                }
            finally:
                release.set()
            original = future.result(timeout=10)
        assert original.status_code == 200
        status = client.get(path, headers=headers).json()["turn"]
        assert status["status"] == "COMPLETED"
        assert status["id"] == running["id"] == original.json()["id"]
        assert (
            client.post("/v1/chat", json=body, headers=headers).json()
            == original.json()
        )
        session.ask.assert_called_once()
