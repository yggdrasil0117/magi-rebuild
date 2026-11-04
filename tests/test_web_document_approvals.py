"""实际工作单接口的持久审批、拒绝、并发与中断恢复。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magi.personal.models import PersonalError
from magi.personal.store import SCHEMA_VERSION
from magi.web.app import create_app
from magi.web.service import WebService
from tests.test_task_documents import setup as setup


def preview(service, plan_id, request_id="p"):
    """使用生产准备入口，返回已进入框架暂停状态的预览。"""
    return service.document_action("preview", plan_id, 1, request_id)


def decide(service, saved, action="confirm", interrupt_id=None):
    """模拟可信路由传入用户按钮决定，默认使用页面实际显示的中断编号。"""
    return service.document_action(
        action,
        saved["id"],
        saved["payload_hash"],
        interrupt_id or saved["approval"]["interrupt_id"],
    )


def test_prepare_pending_and_approve_are_separate(setup):
    _, service, plan_id = setup
    saved = preview(service, plan_id)
    assert saved["approval"]["status"] == "PENDING"
    assert not service.documents.directory.exists()
    assert preview(service, plan_id) == saved
    result = decide(service, saved)
    assert result["approval"]["status"] == "CREATED" and result["verified"]
    assert decide(service, saved)["verified"]
    assert len(list(service.documents.directory.iterdir())) == 1


def test_reject_is_persistent_and_cannot_be_approved(setup):
    settings, service, plan_id = setup
    saved = preview(service, plan_id)
    assert decide(service, saved, "reject")["approval"]["status"] == "REJECTED"
    assert decide(service, saved, "reject")["approval"]["status"] == "REJECTED"
    reopened = WebService(settings, service.owner)
    reopened.recover()
    with pytest.raises(PersonalError) as error:
        decide(reopened, saved)
    assert error.value.code == "APPROVAL_CONFLICT"
    assert not service.documents.directory.exists()
    assert preview(reopened, plan_id)["approval"]["status"] == "REJECTED"
    fresh = preview(reopened, plan_id, "new")
    assert fresh["id"] != saved["id"] and fresh["approval"]["status"] == "PENDING"


def test_pending_reopens_without_execution(setup):
    settings, service, plan_id = setup
    saved = preview(service, plan_id)
    reopened = WebService(settings, service.owner)
    reopened.recover()
    assert reopened.document_view(saved["id"])["approval"] == saved["approval"]
    assert not service.documents.directory.exists()
    assert decide(reopened, saved)["verified"]


def test_bad_hash_or_stale_interrupt_does_not_decide(setup):
    _, service, plan_id = setup
    saved = preview(service, plan_id)
    with pytest.raises(PersonalError):
        service.document_action("confirm", saved["id"], "0" * 64)
    with pytest.raises(PersonalError) as error:
        decide(service, saved, interrupt_id="stale")
    assert error.value.code == "STALE_APPROVAL"
    assert service.approvals.status(saved["id"])["decision"] is None
    assert not service.documents.directory.exists()


def test_other_owner_cannot_view_or_decide(setup):
    settings, service, plan_id = setup
    saved = preview(service, plan_id)
    other = WebService(settings, "other-owner")
    with pytest.raises(PersonalError):
        other.document_view(saved["id"])
    with pytest.raises(PersonalError):
        decide(other, saved)


def test_old_preview_and_old_confirmation_body_are_compatible(setup):
    _, service, plan_id = setup
    saved = service.documents.preview(plan_id, 1, "old")
    assert service.document_view(saved["id"])["approval"]["status"] == "NOT_STARTED"
    result = service.document_action("confirm", saved["id"], saved["payload_hash"])
    assert result["verified"] and result["approval"]["decision"] == "approve"


def test_opposite_concurrent_decisions_only_one_wins(setup):
    _, service, plan_id = setup
    saved = preview(service, plan_id)

    def request(action):
        """两个请求共用生产串行门，输家必须收到决定冲突。"""
        try:
            return decide(service, saved, action)["approval"]["decision"]
        except PersonalError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(request, ["confirm", "reject"]))
    assert results.count("APPROVAL_CONFLICT") == 1
    decision = service.approvals.status(saved["id"])["decision"]
    assert len(list(service.documents.directory.glob("*.md"))) == (
        1 if decision == "approve" else 0
    )


def test_crash_after_decision_requires_explicit_retry(setup, monkeypatch):
    settings, service, plan_id = setup
    saved = preview(service, plan_id)
    original_graph = service.approvals._graph

    @contextmanager
    def crash():
        """决定已提交、进入恢复图之前终止，不应启动时自动执行。"""
        if service.approvals._record(saved["id"])["decision"] is not None:
            raise SystemExit("simulated process stop")
        with original_graph() as graph:
            yield graph

    monkeypatch.setattr(service.approvals, "_graph", crash)
    with pytest.raises(SystemExit):
        decide(service, saved)
    reopened = WebService(settings, service.owner)
    reopened.recover()
    assert reopened.document_view(saved["id"])["approval"]["status"] == "APPROVED"
    assert not reopened.documents.directory.exists()
    assert decide(reopened, saved)["verified"]


def test_completed_file_survives_graph_commit_interruption(setup, monkeypatch):
    settings, service, plan_id = setup
    saved = preview(service, plan_id)
    original = service.documents.confirm

    def crash(*args):
        """真实业务写入完成后中断，重试只能核对原文件，不再新建。"""
        original(*args)
        raise SystemExit("after business commit")

    monkeypatch.setattr(service.documents, "confirm", crash)
    with pytest.raises(SystemExit):
        decide(service, saved)
    reopened = WebService(settings, service.owner)
    assert decide(reopened, saved)["verified"]
    assert len(list(reopened.documents.directory.iterdir())) == 1


def test_failed_tool_blocks_replay_and_preserves_user_file(setup):
    _, service, plan_id = setup
    saved = preview(service, plan_id)
    service.documents.directory.mkdir()
    Path(saved["path"]).write_text("user file", encoding="utf-8")
    with pytest.raises(PersonalError):
        decide(service, saved)
    assert service.document_view(saved["id"])["approval"]["status"] == "BLOCKED"
    with pytest.raises(PersonalError) as error:
        decide(service, saved)
    assert error.value.code == "APPROVAL_BLOCKED"
    assert Path(saved["path"]).read_text(encoding="utf-8") == "user file"


def test_api_reject_requires_auth_and_explicit_confirmation(setup):
    settings, service, plan_id = setup
    saved = preview(service, plan_id)
    token = "offline-approval-api-test-token"
    headers = {"Authorization": "Bearer " + token}
    url = f"/v1/documents/{saved['id']}/reject"
    body = {
        "confirmed": True,
        "payload_hash": saved["payload_hash"],
        "interrupt_id": saved["approval"]["interrupt_id"],
    }
    with TestClient(create_app(settings, service=service, token=token)) as client:
        assert client.post(url, json=body).status_code == 401
        assert (
            client.post(
                url, headers=headers, json={**body, "confirmed": "true"}
            ).status_code
            == 422
        )
        assert (
            client.post(url, headers=headers, json=body).json()["approval"]["status"]
            == "REJECTED"
        )
        assert (
            client.post(
                url.replace("reject", "confirm"), headers=headers, json=body
            ).status_code
            == 409
        )


def test_schema6_upgrade_preserves_existing_preview(setup):
    _, service, plan_id = setup
    saved = service.documents.preview(plan_id, 1, "legacy")
    with service.store.transaction() as db:
        db.execute("DROP TABLE plan_execution_events")
        db.execute("DROP TABLE document_approvals")
        db.execute("UPDATE schema_meta SET version=6")
    assert service.document_view(saved["id"])["content"] == saved["content"]
    with service.store.connect() as db:
        assert (
            db.execute("SELECT version FROM schema_meta").fetchone()[0]
            == SCHEMA_VERSION
        )
