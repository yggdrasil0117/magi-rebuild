"""任务工作单的确认、文件副作用和中断恢复边界。"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from magi.harness.config import HarnessConfig
from magi.personal.documents import TaskDocuments, checksum
from magi.personal.models import PersonalError
from magi.web.app import create_app
from magi.web.service import WebService


@pytest.fixture
def setup(tmp_path):
    """独立业务库与输出目录，不调用模型或触碰真实个人数据。"""
    settings = HarnessConfig(
        docs_root=tmp_path,
        db_path=tmp_path / "checkpoint.db",
        events_path=tmp_path / "events.jsonl",
        business_db_path=tmp_path / "business.db",
    )
    service = WebService(settings, "test-owner")
    plan = service.plans.create(
        title="接口练习",
        body="实现接口；完成条件：响应符合约定。",
        request_id="seed",
        source_epoch=0,
    )
    return settings, service, plan.object_id


def test_preview_and_confirm_are_separate_idempotent_operations(setup):
    _, service, plan_id = setup
    tool = service.documents
    preview = tool.preview(plan_id, 1, "preview")
    assert not tool.directory.exists()
    assert preview == tool.preview(plan_id, 1, "preview")
    before = service.memories.current_epoch()
    result = tool.confirm(preview["id"], preview["payload_hash"])
    assert result["status"] == "CREATED" and result["verified"]
    path = Path(result["path"])
    assert path.read_bytes() == preview["content"].encode()
    assert checksum(path.read_text(encoding="utf-8")) == preview["payload_hash"]
    assert tool.confirm(preview["id"], preview["payload_hash"]) == result
    assert len(list(tool.directory.iterdir())) == 1
    assert service.memories.current_epoch() == before
    assert service.plans.get(plan_id).version == 1


def test_frozen_version_survives_plan_update_and_restart(setup):
    settings, service, plan_id = setup
    preview = service.documents.preview(plan_id, 1, "old")
    service.plans.update(
        plan_id,
        title="新计划",
        body="新步骤",
        expected_version=1,
        request_id="update",
        source_epoch=0,
    )
    reopened = WebService(settings, "test-owner")
    assert reopened.documents.get(preview["id"]) == preview
    result = reopened.documents.confirm(preview["id"], preview["payload_hash"])
    assert "响应符合约定" in Path(result["path"]).read_text(encoding="utf-8")
    assert "新步骤" not in preview["content"]
    with pytest.raises(PersonalError):
        reopened.documents.preview(plan_id, 2, "old")


def test_other_owner_and_wrong_hash_cannot_create_file(setup):
    _, service, plan_id = setup
    preview = service.documents.preview(plan_id, 1, "p")
    other = TaskDocuments(service.store, "other")
    with pytest.raises(PersonalError):
        other.get(preview["id"])
    with pytest.raises(PersonalError):
        other.confirm(preview["id"], preview["payload_hash"])
    with pytest.raises(PersonalError):
        service.documents.confirm(preview["id"], "0" * 64)
    assert not service.documents.directory.exists()


def test_never_overwrites_existing_or_user_edited_file(setup):
    _, service, plan_id = setup
    tool = service.documents
    preview = tool.preview(plan_id, 1, "p")
    tool.directory.mkdir()
    path = Path(preview["path"])
    path.write_text("用户文件", encoding="utf-8")
    with pytest.raises(PersonalError):
        tool.confirm(preview["id"], preview["payload_hash"])
    assert path.read_text(encoding="utf-8") == "用户文件"
    assert tool.get(preview["id"])["status"] == "PREVIEW"
    path.unlink()
    tool.confirm(preview["id"], preview["payload_hash"])
    path.write_text("编辑后的工作单", encoding="utf-8")
    with pytest.raises(PersonalError):
        tool.confirm(preview["id"], preview["payload_hash"])
    path.unlink()
    with pytest.raises(PersonalError):
        tool.confirm(preview["id"], preview["payload_hash"])
    assert not path.exists()


def test_file_published_before_database_commit_recovers(setup, monkeypatch):
    _, service, plan_id = setup
    tool = service.documents
    preview = tool.preview(plan_id, 1, "p")
    original = os.link

    def interrupted(source, target):
        """模拟最终文件完整发布之后、状态落库之前进程操作中断。"""
        original(source, target)
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "link", interrupted)
    with pytest.raises(OSError):
        tool.confirm(preview["id"], preview["payload_hash"])
    assert tool.get(preview["id"])["status"] == "PREVIEW"
    assert Path(preview["path"]).exists()
    monkeypatch.setattr(os, "link", original)
    recovered = TaskDocuments(service.store, service.owner)
    assert recovered.confirm(preview["id"], preview["payload_hash"])["verified"]
    assert len(list(tool.directory.iterdir())) == 1


def test_publish_failure_has_no_final_file(setup, monkeypatch):
    _, service, plan_id = setup
    preview = service.documents.preview(plan_id, 1, "p")

    def fail(*args):
        """磁盘或链接操作失败不能留下部分最终文档。"""
        raise OSError("disk failure")

    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(Exception) as error:
        service.document_action("confirm", preview["id"], preview["payload_hash"])
    assert error.value.code == "DOCUMENT_IO_ERROR"
    assert not list(service.documents.directory.iterdir())
    assert service.documents.get(preview["id"])["status"] == "PREVIEW"


def test_authenticated_api_requires_explicit_confirmation(setup):
    settings, service, plan_id = setup
    token = "disposable-document-test-token"
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(settings, token=token, service=service)) as client:
        url = f"/v1/plans/{plan_id}/documents"
        assert (
            client.post(url, json={"version": 1, "request_id": "p"}).status_code == 401
        )
        preview = client.post(
            url, headers=headers, json={"version": 1, "request_id": "p"}
        ).json()
        confirm = f"/v1/documents/{preview['id']}/confirm"
        for value in (False, "true", 1):
            assert (
                client.post(
                    confirm,
                    headers=headers,
                    json={"confirmed": value, "payload_hash": preview["payload_hash"]},
                ).status_code
                == 422
            )
        assert not service.documents.directory.exists()
        assert (
            client.post(
                url,
                headers=headers,
                json={"version": 1, "request_id": "q", "path": "../x"},
            ).status_code
            == 422
        )
        result = client.post(
            confirm,
            headers=headers,
            json={"confirmed": True, "payload_hash": preview["payload_hash"]},
        )
        assert result.status_code == 200 and result.json()["verified"]
        assert (
            client.get(url + "?version=1", headers=headers).json()[0]["status"]
            == "CREATED"
        )


def test_schema4_upgrade_preserves_plan(setup):
    _, service, plan_id = setup
    with service.store.transaction() as db:
        db.execute("DROP TABLE task_documents")
        db.execute("UPDATE schema_meta SET version=4")
    assert service.plans.get(plan_id).body == "实现接口；完成条件：响应符合约定。"
    with service.store.connect() as db:
        assert db.execute("SELECT version FROM schema_meta").fetchone()[0] == 5
