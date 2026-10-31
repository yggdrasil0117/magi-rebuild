"""成熟框架真实审批与MAGI业务边界的独立兼容性验收。"""

from dataclasses import replace
from pathlib import Path

import pytest
from langgraph.types import Command

from magi.harness.approval_trial import (
    TOOL_NAME,
    ApprovalError,
    open_document_approval_trial,
)
from magi.personal.models import PersonalError
from scripts.probe_approval import ApprovalModel
from tests.test_harness_runtime import ScriptedModel, calls
from tests.test_task_documents import setup as setup


@pytest.fixture
def prepared(setup):
    """独立业务库与checkpoint，保留生产文件服务，模型完全离线。"""
    settings, service, plan_id = setup
    settings = replace(settings, num_ctx=32768)
    saved = service.documents.preview(plan_id, 1, "trial-preview")
    return settings, service, saved


def model_for(saved):
    """脚本只提出冻结文档动作，用户批准仍需独立调用decide。"""
    return ApprovalModel(document_id=saved["id"], payload_hash=saved["payload_hash"])


def test_trial_never_falls_back_to_default_model(prepared):
    settings, service, saved = prepared
    with pytest.raises(ValueError, match="显式提供模型"):
        with open_document_approval_trial(
            settings, service.owner, saved["id"], model=None
        ):
            pytest.fail("试验不能隐式使用默认模型")
    assert not settings.db_path.exists()


def test_pauses_before_execution_and_approve_executes_once(prepared):
    settings, service, saved = prepared
    with open_document_approval_trial(
        settings, service.owner, saved["id"], model=model_for(saved)
    ) as trial:
        pending = trial.start()
        assert pending["phase"] == "PENDING" and pending["document_status"] == "PREVIEW"
        assert not Path(saved["path"]).exists()
        assert trial.start() == pending
        assert (
            sum(e["type"] == "model_response" for e in trial.session.events.records)
            == 1
        )
        result = trial.decide(pending["interrupt_id"], "approve", saved["payload_hash"])
        assert result["phase"] == "COMPLETED" and result["document_status"] == "CREATED"
        assert Path(saved["path"]).read_bytes() == saved["content"].encode()
        with pytest.raises(ApprovalError, match="没有待处理"):
            trial.decide(pending["interrupt_id"], "approve", saved["payload_hash"])
        assert len(list(service.documents.directory.iterdir())) == 1
        assert service.memories.current_epoch() == 0


def test_reject_completes_graph_without_creating_file(prepared):
    settings, service, saved = prepared
    with open_document_approval_trial(
        settings, service.owner, saved["id"], model=model_for(saved)
    ) as trial:
        pending = trial.start()
        result = trial.decide(pending["interrupt_id"], "reject", saved["payload_hash"])
        assert result["phase"] == "COMPLETED" and result["document_status"] == "PREVIEW"
        assert not service.documents.directory.exists()


@pytest.mark.parametrize("choice", ["edit", "respond", "true", True, None, [], {}])
def test_adapter_rejects_unsupported_decisions_before_resume(prepared, choice):
    settings, service, saved = prepared
    with open_document_approval_trial(
        settings, service.owner, saved["id"], model=model_for(saved)
    ) as trial:
        pending = trial.start()
        with pytest.raises(ApprovalError) as error:
            trial.decide(pending["interrupt_id"], choice, saved["payload_hash"])
        assert error.value.code == "INVALID_DECISION"
        assert trial.status() == pending
        assert not service.documents.directory.exists()


@pytest.mark.parametrize("wrong", ["interrupt", "hash"])
def test_stale_or_changed_approval_does_not_consume_pending_state(prepared, wrong):
    settings, service, saved = prepared
    with open_document_approval_trial(
        settings, service.owner, saved["id"], model=model_for(saved)
    ) as trial:
        pending = trial.start()
        with pytest.raises(ApprovalError) as error:
            trial.decide(
                "wrong" if wrong == "interrupt" else pending["interrupt_id"],
                "approve",
                "0" * 64 if wrong == "hash" else saved["payload_hash"],
            )
        assert error.value.code == (
            "STALE_INTERRUPT" if wrong == "interrupt" else "CONTENT_CONFLICT"
        )
        assert trial.status() == pending
        assert not service.documents.directory.exists()


def test_other_owner_cannot_open_approval(prepared):
    settings, service, saved = prepared
    with pytest.raises(PersonalError):
        with open_document_approval_trial(
            settings, "other-owner", saved["id"], model=model_for(saved)
        ):
            pytest.fail("不应打开其他owner的审批")
    assert not service.documents.directory.exists()


def test_reopen_restores_same_interrupt_without_model_call(prepared):
    settings, service, saved = prepared
    with open_document_approval_trial(
        settings, service.owner, saved["id"], model=model_for(saved)
    ) as trial:
        pending = trial.start()
    with open_document_approval_trial(
        settings, service.owner, saved["id"], model=model_for(saved)
    ) as reopened:
        assert reopened.status() == pending
        assert reopened.start() == pending
        assert reopened.session.events.records == []
        assert (
            reopened.decide(pending["interrupt_id"], "approve", saved["payload_hash"])[
                "document_status"
            ]
            == "CREATED"
        )


def test_existing_user_file_conflict_is_not_hidden_by_framework_approval(prepared):
    settings, service, saved = prepared
    with open_document_approval_trial(
        settings, service.owner, saved["id"], model=model_for(saved)
    ) as trial:
        pending = trial.start()
        service.documents.directory.mkdir()
        Path(saved["path"]).write_text("user edited", encoding="utf-8")
        with pytest.raises(PersonalError):
            trial.decide(pending["interrupt_id"], "approve", saved["payload_hash"])
        assert Path(saved["path"]).read_text(encoding="utf-8") == "user edited"
        assert trial.status()["phase"] == "BLOCKED"
        assert trial.status()["document_status"] == "PREVIEW"
        assert trial.start()["phase"] == "BLOCKED"


def test_mismatched_model_action_cannot_be_approved(prepared):
    settings, service, saved = prepared
    model = ScriptedModel(
        responses=[
            calls((TOOL_NAME, {"document_id": saved["id"], "payload_hash": "0" * 64}))
        ]
    )
    with open_document_approval_trial(
        settings, service.owner, saved["id"], model=model
    ) as trial:
        with pytest.raises(ApprovalError) as error:
            trial.start()
        assert error.value.code == "UNEXPECTED_ACTION"
        assert not service.documents.directory.exists()


def test_framework_disallows_edit_even_if_raw_resume_bypasses_adapter(prepared):
    settings, service, saved = prepared
    with open_document_approval_trial(
        settings, service.owner, saved["id"], model=model_for(saved)
    ) as trial:
        trial.start()
        with pytest.raises(ValueError):
            trial.session.agent.invoke(
                Command(
                    resume={
                        "decisions": [
                            {
                                "type": "edit",
                                "edited_action": {
                                    "name": TOOL_NAME,
                                    "args": {
                                        "document_id": saved["id"],
                                        "payload_hash": "0" * 64,
                                    },
                                },
                            }
                        ]
                    }
                ),
                trial.session.config,
            )
        assert not service.documents.directory.exists()


def test_tool_success_then_final_answer_failure_is_not_business_rollback(prepared):
    settings, service, saved = prepared
    model = ScriptedModel(
        responses=[
            calls(
                (
                    TOOL_NAME,
                    {"document_id": saved["id"], "payload_hash": saved["payload_hash"]},
                )
            )
        ]
    )
    with open_document_approval_trial(
        settings, service.owner, saved["id"], model=model
    ) as trial:
        pending = trial.start()
        with pytest.raises(AssertionError):
            trial.decide(pending["interrupt_id"], "approve", saved["payload_hash"])
        assert trial.status()["phase"] == "BLOCKED"
        assert trial.status()["document_status"] == "CREATED"
        assert Path(saved["path"]).read_bytes() == saved["content"].encode()
