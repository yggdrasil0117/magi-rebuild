"""用实际网页聊天循环和模拟 GitHub 验证外部动作边界。"""

import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from magi.web.app import create_app
from magi.web.issues import GitHubIssues
from magi.web.schemas import IssueDecision
from magi.web.service import WebError, WebService
from tests.test_harness_runtime import ScriptedModel, calls
from tests.test_web_mvp import env as env
from tests.test_web_mvp import request


@pytest.fixture
def issue_env(env, monkeypatch):
    settings, _, _, _ = env
    settings = replace(settings, num_ctx=32768)
    monkeypatch.setenv("MAGI_GITHUB_ISSUES_REPO", "sandbox-owner/test-repo")
    monkeypatch.setenv("MAGI_GITHUB_ISSUES_TOKEN", "synthetic-only")
    service = WebService(settings, "owner")
    model = ScriptedModel(
        responses=[
            calls(
                (
                    "magi_prepare_issue",
                    {"title": "核对超时", "body": "复现一次并保存日志。"},
                )
            ),
            AIMessage(content="草稿已准备，等待批准。"),
        ]
    )
    monkeypatch.setattr("magi.harness.runtime.build_model", lambda _: model)
    return settings, service, model


def test_agent_draft_approval_timeout_reconcile_and_receipt(issue_env):
    settings, service, model = issue_env
    requests = []
    remote = {}

    def simulate(req):
        requests.append((req.method, str(req.url)))
        if req.method == "POST":
            remote.update(json.loads(req.content))
            remote.update(
                number=7,
                repository_url="https://api.github.com/repos/sandbox-owner/test-repo",
            )
            # 模拟远端已经创建，本机没有读到回执。
            raise httpx.ReadTimeout("simulated", request=req)
        if req.url.path.endswith("/8"):
            return httpx.Response(
                200, json=remote | {"number": 8, "body": "另一个 Issue"}
            )
        return httpx.Response(200, json=remote)

    token = "isolated-issue-workflow-token"
    headers = {"Authorization": "Bearer " + token}
    with httpx.Client(transport=httpx.MockTransport(simulate)) as github:
        service.issues.github = GitHubIssues(github)
        with TestClient(create_app(settings, token=token, service=service)) as client:
            chat = client.post(
                "/v1/chat",
                json=request(issue_enabled=True).model_dump(),
                headers=headers,
            )
            assert chat.status_code == 200
            action = chat.json()["issues"][0]
            assert action["status"] == "DRAFT" and not requests
            assert isinstance(model.requests[-1][-1], ToolMessage)
            assert '"status": "DRAFT"' in model.requests[-1][-1].text
            path = "/v1/issues/" + action["id"]
            assert client.get(path).status_code == 401
            with pytest.raises(WebError) as error:
                WebService(settings, "other").issues.get(action["id"])
            assert error.value.status == 404
            body = {"decision": "approve", "payload_hash": action["payload_hash"]}
            assert (
                client.post(
                    path + "/decision",
                    json=body | {"payload_hash": "0" * 64},
                    headers=headers,
                ).status_code
                == 409
            )
            assert not requests
            assert (
                client.post(path + "/decision", json=body, headers=headers).json()[
                    "status"
                ]
                == "UNKNOWN"
            )
            assert (
                client.post(path + "/decision", json=body, headers=headers).json()[
                    "status"
                ]
                == "UNKNOWN"
            )
            assert requests == [
                ("POST", "https://api.github.com/repos/sandbox-owner/test-repo/issues")
            ]
            assert remote["body"] == action["outbound_body"]
            assert (
                client.post(
                    path + "/reconcile", json={"issue_number": 8}, headers=headers
                ).status_code
                == 409
            )
            verified = client.post(
                path + "/reconcile", json={"issue_number": 7}, headers=headers
            ).json()
            assert verified["status"] == "VERIFIED" and verified["issue_number"] == 7
            assert (
                client.post(path + "/decision", json=body, headers=headers).json()
                == verified
            )

            model.responses.extend(
                [
                    calls(("magi_get_issue_receipt", {"action_id": action["id"]})),
                    AIMessage(content="已核对 Issue #7。"),
                ]
            )
            reply = service.turn(request("2", issue_enabled=True), "chat")
            assert reply["text"] == "已核对 Issue #7。"
            assert '"status": "VERIFIED"' in model.requests[-1][-1].text
            assert [method for method, _ in requests] == ["POST", "GET", "GET"]


def test_sending_restart_becomes_unknown_and_is_not_resent(issue_env):
    settings, service, _ = issue_env
    action = service.turn(request(issue_enabled=True), "chat")["issues"][0]
    with service.store.transaction() as db:
        db.execute(
            "UPDATE issue_actions SET status='SENDING' WHERE id=?", (action["id"],)
        )
    restored = WebService(settings, "owner")
    restored.recover()

    def unexpected_request(*_args, **_kwargs):
        pytest.fail("重复批准不应连接远端")

    restored.issues.github.request = unexpected_request
    command = IssueDecision(decision="approve", payload_hash=action["payload_hash"])
    assert restored.issues.decide(action["id"], command)["status"] == "UNKNOWN"
    assert restored.history("study")[0]["issues"][0]["status"] == "UNKNOWN"


def test_successful_approval_reuses_verified_result(issue_env):
    _, service, _ = issue_env
    action = service.turn(request(issue_enabled=True), "chat")["issues"][0]
    sent = []

    def simulate(req):
        sent.append(req)
        return httpx.Response(
            201,
            json=json.loads(req.content)
            | {
                "number": 22,
                "repository_url": "https://api.github.com/repos/sandbox-owner/test-repo",
            },
        )

    command = IssueDecision(decision="approve", payload_hash=action["payload_hash"])
    with httpx.Client(transport=httpx.MockTransport(simulate)) as github:
        service.issues.github = GitHubIssues(github)
        created = service.issues.decide(action["id"], command)
        assert created["url"] == "https://github.com/sandbox-owner/test-repo/issues/22"
        assert created["status"] == "VERIFIED"
        assert service.issues.decide(action["id"], command) == created
        assert len(sent) == 1


@pytest.mark.parametrize("change", ["epoch", "repo", "reject"])
def test_draft_stale_or_rejected_never_sends(issue_env, change):
    _, service, _ = issue_env
    action = service.turn(request(issue_enabled=True), "chat")["issues"][0]
    command = IssueDecision(decision="approve", payload_hash=action["payload_hash"])

    def unexpected_request(*_args, **_kwargs):
        pytest.fail("失效或拒绝草稿不应连接远端")

    service.issues.github.request = unexpected_request
    if change == "epoch":
        with service.store.transaction() as db:
            db.execute("UPDATE app_state SET context_epoch=context_epoch+1")
    elif change == "repo":
        service.issues.repo = "sandbox-owner/changed"
    else:
        rejected = service.issues.decide(
            action["id"], command.model_copy(update={"decision": "reject"})
        )
        assert rejected["status"] == "REJECTED"
    with pytest.raises(WebError):
        service.issues.decide(action["id"], command)
