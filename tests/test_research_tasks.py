"""验证研究人格、有限协作、隔离上下文与成果确认边界。"""

from dataclasses import replace
from pathlib import Path
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from magi.deployment import backup, install
from magi.harness.config import load_config
from magi.personal.artifacts import ResearchArtifacts
from magi.personal.store import PersonalStore
from magi.research.budget import ResearchBudget
from magi.research.models import (
    FindingHandling,
    ReportPayload,
    ReviewCheck,
    ReviewFindingInput,
    ReviewPayload,
    RevisionPayload,
)
from magi.research.runner import BudgetExhausted, PersonaRunner, ResearchOutputError
from magi.web.app import create_app
from magi.web.schemas import ResearchExecution, SourceArtifact, TurnInput
from magi.web.service import WebService
from magi.web.tasks import AgentTasks
from tests.test_harness_runtime import ScriptedModel, calls
from tests.test_web_mvp import env as env


def turn(
    message="research-1", chat="research-melchior", text="读取 note.txt 并形成报告"
):
    return TurnInput(
        chat_id=chat,
        message_id=message,
        reply_message_id="reply-" + message,
        user_text=text,
    )


def checks(status="pass"):
    return [
        ReviewCheck(name=name, status=status, reason="已检查")
        for name in (
            "requirements",
            "evidence",
            "completed_work",
            "unknowns",
            "permissions",
        )
    ]


@pytest.mark.parametrize("agent_id", ["melchior", "balthasar", "casper"])
def test_each_persona_uses_same_handwritten_tool_loop(env, agent_id):
    settings, _, _, _ = env
    (settings.docs_root / "note.txt").write_text("EVIDENCE-731", encoding="utf-8")
    answer = {
        "title": "调查结果",
        "body": "实际读取到 EVIDENCE-731。[evidence:call-0]",
        "completed_items": ["读取授权资料"],
        "unknowns": [],
    }
    model = ScriptedModel(
        responses=[
            calls(("magi_read_file", {"path": "note.txt"})),
            AIMessage(content=__import__("json").dumps(answer, ensure_ascii=False)),
        ]
    )
    runner = PersonaRunner(settings, model_factory=lambda _settings: model)
    report, sources, _, model_calls, tool_calls = runner.generate(
        "run-" + agent_id,
        agent_id,
        {"request": "读取资料", "permissions": {"web_enabled": False}},
        (3, 2),
    )
    assert "EVIDENCE-731" in report.body
    assert sources[0]["tool"] == "magi_read_file"
    assert (model_calls, tool_calls) == (2, 1)
    assert model.tool_bindings and "magi_read_file" in model.tool_bindings[0]


def test_shared_budget_splits_review_stage_without_resetting_total():
    budget = ResearchBudget(collaborative=True)
    assert budget.allowance("review", participants=2) == (3, 4)
    budget.consume("review", 3, 4)
    assert budget.allowance("review", participants=2) == (3, 4)
    budget.consume("review", 3, 4)
    assert budget.allowance("review", participants=2) == (0, 0)


def test_expired_shared_deadline_stops_before_model_request(env):
    settings, _, _, _ = env
    model = ScriptedModel(responses=[])
    runner = PersonaRunner(settings, model_factory=lambda _settings: model)
    with pytest.raises(BudgetExhausted, match="期限"):
        runner.generate(
            "expired",
            "melchior",
            {"request": "不应调用模型"},
            (2, 1),
            deadline=monotonic() - 1,
        )
    assert model.requests == []


class CollaborationRunner:
    def __init__(self):
        self.review_rounds = []
        self.contexts = []

    def generate(self, run_id, agent_id, context, allowance, **kwargs):
        self.contexts.append(context)
        return (
            ReportPayload(
                title="v1 报告",
                body="初稿",
                completed_items=["读取需求"],
                unknowns=["维护成本"],
            ),
            [],
            [],
            1,
            0,
        )

    def review(self, run_id, agent_id, context, allowance, *, stage, **kwargs):
        self.review_rounds.append((stage, agent_id, context["artifact"]["version"]))
        if stage == "review" and agent_id == "balthasar":
            result = ReviewPayload(
                verdict="REVISE",
                checks=checks(),
                findings=[
                    ReviewFindingInput(
                        target="维护成本",
                        issue_type="UNKNOWN_NOT_DISCLOSED",
                        blocking=True,
                        reason="没有给出恢复负担",
                        evidence_ids=[],
                        suggestion="保留为未知并说明影响",
                    )
                ],
            )
        else:
            result = ReviewPayload(verdict="PASS", checks=checks(), findings=[])
        return result, [], [], 1, 0

    def revise(self, run_id, agent_id, context, allowance, **kwargs):
        finding = context["findings"][0]["finding_id"]
        return (
            RevisionPayload(
                report=ReportPayload(
                    title="v2 报告",
                    body="修改稿明确保留维护成本未知。",
                    completed_items=["读取需求"],
                    unknowns=["维护成本尚无实测"],
                ),
                handling=[
                    FindingHandling(
                        finding_id=finding,
                        action="accepted",
                        reason="如实缩小结论",
                        location="未核实事项",
                    )
                ],
            ),
            [],
            [],
            1,
            0,
        )


def test_collaboration_revises_once_and_second_review_always_stops(env):
    settings, _, _, _ = env
    service = WebService(settings, "owner")
    runner = CollaborationRunner()
    service.research.runner = runner
    queue = AgentTasks(service)
    execution = ResearchExecution(
        lead_agent_id="melchior",
        reviewer_ids=["balthasar", "casper"],
        entry_mode="persona",
    )
    task = queue.submit(turn(), "research", execution)
    assert queue.run_one()
    result = queue.get(task["id"])
    assert (result["status"], result["review_status"]) == ("COMPLETED", "PASSED")
    artifact = service.research.artifacts.get(result["artifact_id"])
    assert artifact["version"] == 2 and artifact["title"] == "v2 报告"
    assert (
        service.research.artifacts.get(result["artifact_id"], 1)["review_status"]
        == "UNRESOLVED"
    )
    assert runner.review_rounds == [
        ("review", "balthasar", 1),
        ("review", "casper", 1),
        ("verify", "balthasar", 2),
        ("verify", "casper", 2),
    ]
    with service.store.connect() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM agent_runs WHERE task_id=? AND stage='revise'",
                (task["id"],),
            ).fetchone()[0]
            == 1
        )


def test_private_research_context_does_not_leak_to_another_chat(env):
    settings, _, _, _ = env
    service = WebService(settings, "owner")
    runner = CollaborationRunner()
    service.research.runner = runner
    queue = AgentTasks(service)
    first = queue.submit(
        turn("private", "research-melchior", "PRIVATE-MARKER-991"),
        "research",
        ResearchExecution(lead_agent_id="melchior", entry_mode="persona"),
    )
    assert queue.run_one() and queue.get(first["id"])["status"] == "COMPLETED"
    second = queue.submit(
        turn("other", "research-casper", "另一个任务"),
        "research",
        ResearchExecution(lead_agent_id="casper", entry_mode="persona"),
    )
    assert queue.run_one() and queue.get(second["id"])["status"] == "COMPLETED"
    assert "PRIVATE-MARKER-991" not in repr(runner.contexts[-1])


def test_explicit_message_share_is_validated_and_included(env):
    settings, _, _, _ = env
    service = WebService(settings, "owner")
    with service.store.transaction() as db:
        db.execute(
            """INSERT INTO web_turns
               (id,owner,chat,message,reply,digest,epoch,mode,user_text,status,text,
                synced,created_at)
               VALUES ('shared-turn','owner','private-chat','share-1','share-reply',
                       'digest',0,'chat','PRIVATE-SHARED-551','COMPLETED',
                       '已完成回答',1,'2026-11-03T00:00:00+00:00')"""
        )
    runner = CollaborationRunner()
    service.research.runner = runner
    queue = AgentTasks(service)
    task = queue.submit(
        turn("shared-task", "shared-room", "使用我明确分享的消息"),
        "research",
        ResearchExecution(
            lead_agent_id="melchior",
            entry_mode="room",
            shared_message_ids=["share-1"],
        ),
    )
    queue.run_one()
    assert queue.get(task["id"])["status"] == "COMPLETED"
    assert runner.contexts[-1]["explicitly_shared_messages"][0]["user_text"] == (
        "PRIVATE-SHARED-551"
    )


def test_capacity_prunes_old_optional_history_and_records_reason(env):
    settings, _, _, _ = env
    first_service = WebService(settings, "owner")
    first_service.research.runner = CollaborationRunner()
    first_queue = AgentTasks(first_service)
    first = first_queue.submit(
        turn("long-old", "capacity-chat", "OLD-OPTIONAL-" + "x" * 1800),
        "research",
        ResearchExecution(lead_agent_id="melchior", entry_mode="persona"),
    )
    first_queue.run_one()
    assert first_queue.get(first["id"])["status"] == "COMPLETED"

    limited_service = WebService(replace(settings, num_ctx=5600), "owner")
    runner = CollaborationRunner()
    limited_service.research.runner = runner
    queue = AgentTasks(limited_service)
    current = queue.submit(
        turn("capacity-current", "capacity-chat", "当前短任务"),
        "research",
        ResearchExecution(lead_agent_id="melchior", entry_mode="persona"),
    )
    queue.run_one()
    result = queue.get(current["id"])
    assert result["status"] == "COMPLETED", result
    selected = runner.contexts[-1]
    assert selected["same_chat_history"] == []
    assert selected["selection"]["omitted_history"] >= 1
    assert "容量预算" in selected["selection"]["omission_reason"]


def test_artifact_approval_is_version_bound_and_idempotent(env, tmp_path):
    settings, _, _, _ = env
    service = WebService(settings, "owner")
    service.research.runner = CollaborationRunner()
    queue = AgentTasks(service)
    task = queue.submit(
        turn(),
        "research",
        ResearchExecution(lead_agent_id="melchior", entry_mode="persona"),
    )
    queue.run_one()
    artifact = service.research.artifacts.get(queue.get(task["id"])["artifact_id"])
    receipt = service.research.artifacts.decide(
        artifact["id"], "approve-1", 1, artifact["payload_hash"], "approve"
    )
    replay = service.research.artifacts.decide(
        artifact["id"], "approve-1", 1, artifact["payload_hash"], "approve"
    )
    assert receipt["path"] == replay["path"]
    assert Path(receipt["path"]).read_text(encoding="utf-8") == artifact["body"]
    archive = tmp_path / "research-backup.zip"
    manifest = backup(settings, archive)
    artifact_name = "research-artifacts/" + Path(receipt["path"]).name
    assert artifact_name in manifest["files"]
    restored = load_config(install(tmp_path / "restored-research", settings, archive))
    restored_artifact = ResearchArtifacts(
        PersonalStore(restored.business_db_path), "owner"
    ).get(artifact["id"], 1)
    restored_path = restored.business_db_path.parent / artifact_name
    assert restored_artifact["payload_hash"] == artifact["payload_hash"]
    assert restored_path.read_text(encoding="utf-8") == artifact["body"]
    with pytest.raises(Exception, match="摘要|版本"):
        service.research.artifacts.decide(
            artifact["id"], "approve-old", 1, "0" * 64, "approve"
        )


def test_follow_up_review_binds_exact_artifact_version(env):
    settings, _, _, _ = env
    service = WebService(settings, "owner")
    service.research.runner = CollaborationRunner()
    queue = AgentTasks(service)
    initial = queue.submit(
        turn(),
        "research",
        ResearchExecution(lead_agent_id="melchior", entry_mode="persona"),
    )
    queue.run_one()
    artifact = service.research.artifacts.get(queue.get(initial["id"])["artifact_id"])
    follow = ResearchExecution(
        lead_agent_id="melchior",
        reviewer_ids=["balthasar"],
        entry_mode="persona",
        source_artifact=SourceArtifact(
            artifact_id=artifact["id"],
            version=artifact["version"],
            payload_hash=artifact["payload_hash"],
        ),
    )
    with pytest.raises(Exception, match="摘要|版本"):
        queue.submit(
            turn("review-bad"),
            "research",
            follow.model_copy(
                update={
                    "source_artifact": SourceArtifact(
                        artifact_id=artifact["id"], version=1, payload_hash="0" * 64
                    )
                }
            ),
        )
    task = queue.submit(turn("review-2"), "research", follow)
    assert task["artifact_id"] == artifact["id"]


def test_initial_pass_stops_without_revision(env):
    settings, _, _, _ = env
    service = WebService(settings, "owner")
    runner = CollaborationRunner()
    service.research.runner = runner
    queue = AgentTasks(service)
    task = queue.submit(
        turn(),
        "research",
        ResearchExecution(
            lead_agent_id="melchior",
            reviewer_ids=["casper"],
            entry_mode="persona",
        ),
    )
    queue.run_one()
    result = queue.get(task["id"])
    assert (result["status"], result["review_status"]) == ("COMPLETED", "PASSED")
    assert service.research.artifacts.get(result["artifact_id"])["version"] == 1
    assert runner.review_rounds == [("review", "casper", 1)]


class VerifyFailureRunner(CollaborationRunner):
    def review(self, run_id, agent_id, context, allowance, *, stage, **kwargs):
        if stage == "verify":
            self.review_rounds.append((stage, agent_id, context["artifact"]["version"]))
            return (
                ReviewPayload(
                    verdict="REVISE",
                    checks=checks(),
                    findings=[
                        ReviewFindingInput(
                            target="修改稿",
                            issue_type="UNSUPPORTED_CLAIM",
                            blocking=True,
                            reason="修改仍未解决",
                            evidence_ids=[],
                            suggestion="后续由用户发起新任务",
                        )
                    ],
                ),
                [],
                [],
                1,
                0,
            )
        return super().review(
            run_id, agent_id, context, allowance, stage=stage, **kwargs
        )


def test_second_review_failure_returns_partial_without_another_revision(env):
    settings, _, _, _ = env
    service = WebService(settings, "owner")
    service.research.runner = VerifyFailureRunner()
    queue = AgentTasks(service)
    task = queue.submit(
        turn(),
        "research",
        ResearchExecution(
            lead_agent_id="melchior",
            reviewer_ids=["balthasar"],
            entry_mode="persona",
        ),
    )
    queue.run_one()
    result = queue.get(task["id"])
    assert (result["status"], result["review_status"], result["stop_reason"]) == (
        "PARTIAL",
        "UNRESOLVED",
        "REVISION_LIMIT",
    )
    with service.store.connect() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM agent_runs WHERE task_id=? AND stage='revise'",
                (task["id"],),
            ).fetchone()[0]
            == 1
        )


class BrokenReviewRunner(CollaborationRunner):
    failure = ResearchOutputError("非法评审").with_usage(1, 0)

    def review(self, *args, **kwargs):
        raise self.failure


@pytest.mark.parametrize(
    "failure,reason",
    [
        (ResearchOutputError("非法评审").with_usage(1, 0), "INVALID_MODEL_OUTPUT"),
        (BudgetExhausted("预算耗尽"), "BUDGET_EXHAUSTED"),
    ],
)
def test_review_error_keeps_valid_draft_and_terminal_state(env, failure, reason):
    settings, _, _, _ = env
    service = WebService(settings, "owner")
    runner = CollaborationRunner()

    def fail(*args, **kwargs):
        raise failure

    runner.review = fail
    service.research.runner = runner
    queue = AgentTasks(service)
    task = queue.submit(
        turn(),
        "research",
        ResearchExecution(
            lead_agent_id="melchior",
            reviewer_ids=["balthasar"],
            entry_mode="persona",
        ),
    )
    queue.run_one()
    result = queue.get(task["id"])
    assert (result["status"], result["review_status"], result["stop_reason"]) == (
        "PARTIAL",
        "INCOMPLETE",
        reason,
    )
    assert service.research.artifacts.get(result["artifact_id"])["body"] == "初稿"


def test_required_context_over_limit_fails_before_model(env):
    settings, _, _, _ = env
    service = WebService(replace(settings, num_ctx=100), "owner")
    runner = CollaborationRunner()
    service.research.runner = runner
    queue = AgentTasks(service)
    task = queue.submit(
        turn(),
        "research",
        ResearchExecution(lead_agent_id="melchior", entry_mode="persona"),
    )
    queue.run_one()
    result = queue.get(task["id"])
    assert (result["status"], result["stop_reason"]) == ("FAILED", "CONTEXT_LIMIT")
    assert runner.contexts == []


def test_running_research_is_interrupted_not_replayed_after_restart(env):
    settings, _, _, _ = env
    service = WebService(settings, "owner")
    queue = AgentTasks(service)
    task = queue.submit(
        turn(),
        "research",
        ResearchExecution(lead_agent_id="melchior", entry_mode="persona"),
    )
    claimed = queue.claim()
    assert claimed["id"] == task["id"]
    queue.recover()
    assert queue.get(task["id"])["status"] == "INTERRUPTED"


def test_file_publish_retry_reconciles_approved_content(env, monkeypatch):
    settings, _, _, _ = env
    service = WebService(settings, "owner")
    service.research.runner = CollaborationRunner()
    queue = AgentTasks(service)
    task = queue.submit(
        turn(),
        "research",
        ResearchExecution(lead_agent_id="melchior", entry_mode="persona"),
    )
    queue.run_one()
    artifact = service.research.artifacts.get(queue.get(task["id"])["artifact_id"])
    original = service.research.artifacts._publish

    def interrupt(*args):
        raise OSError("模拟批准落库后进程中断")

    monkeypatch.setattr(service.research.artifacts, "_publish", interrupt)
    with pytest.raises(OSError, match="中断"):
        service.research.artifacts.decide(
            artifact["id"], "lost-response", 1, artifact["payload_hash"], "approve"
        )
    assert (
        service.research.artifacts.get(artifact["id"])["decision"]["status"]
        == "APPROVED"
    )
    monkeypatch.setattr(service.research.artifacts, "_publish", original)
    receipt = service.research.artifacts.decide(
        artifact["id"], "lost-response", 1, artifact["payload_hash"], "approve"
    )
    assert receipt["status"] == "SAVED"
    assert Path(receipt["path"]).read_text(encoding="utf-8") == artifact["body"]


def test_research_http_from_submit_to_refresh_and_approval(env):
    settings, _, _, _ = env
    service = WebService(settings, "owner")
    service.research.runner = CollaborationRunner()
    token = "research-http-test-token-123456"
    headers = {"Authorization": "Bearer " + token}
    body = {
        "mode": "research",
        "turn": turn("http-1", "research-http").model_dump(mode="json"),
        "execution": {
            "lead_agent_id": "melchior",
            "reviewer_ids": [],
            "deliverable": "report",
            "entry_mode": "persona",
            "shared_message_ids": [],
        },
    }
    with TestClient(create_app(settings, token=token, service=service)) as client:
        assert client.post("/v1/tasks", json=body).status_code == 401
        accepted = client.post("/v1/tasks", json=body, headers=headers)
        assert accepted.status_code == 202
        task_id = accepted.json()["id"]
        deadline = monotonic() + 5
        while monotonic() < deadline:
            task = client.get(f"/v1/tasks/{task_id}", headers=headers).json()
            if task["status"] not in {"QUEUED", "RUNNING"}:
                break
            sleep(0.05)
        assert (task["status"], task["review_status"]) == (
            "COMPLETED",
            "NOT_REQUESTED",
        )
        artifact = client.get(
            f"/v1/artifacts/{task['artifact_id']}", headers=headers
        ).json()
        decision = {
            "request_id": "http-approve",
            "expected_version": artifact["version"],
            "payload_hash": artifact["payload_hash"],
            "decision": "approve",
        }
        saved = client.post(
            f"/v1/artifacts/{artifact['id']}/decision",
            json=decision,
            headers=headers,
        )
        assert saved.status_code == 200 and saved.json()["status"] == "SAVED"
        replay = client.post(
            f"/v1/artifacts/{artifact['id']}/decision",
            json=decision,
            headers=headers,
        )
        assert replay.json()["path"] == saved.json()["path"]
        refreshed = client.get(
            "/v1/chats/research-http/artifacts", headers=headers
        ).json()
        assert refreshed[0]["decision"]["status"] == "SAVED"
