"""真实千问执行反馈闭环，独立合成计划与用户自报记录，不执行现实任务。"""

import argparse
import json
from dataclasses import replace
from pathlib import Path

from magi.harness.config import load_config
from magi.web.schemas import Confirmation, TurnInput
from magi.web.service import WebService


def main():
    """单轮最多4次真实生成；失败保留，不重试；确认仅保存合成测试计划。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.data_dir.exists():
        raise ValueError("输出和目录必须是新路径")
    docs = args.data_dir / "docs"
    docs.mkdir(parents=True)
    settings = replace(
        load_config(Path("magi.qianwen.toml")),
        docs_root=docs,
        db_path=args.data_dir / "checkpoint.db",
        business_db_path=args.data_dir / "business.db",
        events_path=args.data_dir / "events.jsonl",
        embedding_enabled=False,
    )
    service = WebService(settings, "execution-validation")
    plan = service.plans.create(
        title="登录接口核对计划",
        body="复现登录接口问题，核对现有日志，记录证据或缺口。",
        request_id="seed",
        source_epoch=0,
    )
    service.executions.record(
        plan.object_id,
        1,
        request_id="progress",
        expected_revision=0,
        status="IN_PROGRESS",
        note="用户自报：已整理现有错误日志。",
        confirmed=True,
    )
    service.executions.record(
        plan.object_id,
        1,
        request_id="blocked",
        expected_revision=1,
        status="BLOCKED",
        note="用户自报：缺少测试账号，无法复现登录；今天只剩30分钟。",
        confirmed=True,
    )
    report = dict(
        mode="real Qianwen and business/checkpoint SQLite; synthetic user reports; no retries; no real task execution",
        plan_id=plan.object_id,
        initial_execution=service.executions.view(plan.object_id, 1),
    )

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    save()
    try:
        turn = service.turn(
            TurnInput(
                chat_id="execution",
                message_id="u1",
                reply_message_id="a1",
                user_text="请结合这份计划的执行记录修订：我现在没有测试账号，只剩30分钟。给出现有条件下能做的核对，不要求必须定位根因，无法验证就记录缺口。",
                target_plan_id=plan.object_id,
                expected_version=1,
                time_budget_minutes=30,
            ),
            "council",
        )
        report["turn"] = turn
        save()
        if turn["proposal_id"]:
            proposal = service.proposal(turn["proposal_id"], "execution", "a1")
            receipt = service.confirm(
                proposal["id"],
                Confirmation(
                    chat_id="execution",
                    reply_message_id="a1",
                    payload_hash=proposal["payload_hash"],
                ),
            )
            report["saved_revision"] = receipt
            service.executions.record(
                plan.object_id,
                1,
                request_id="completed",
                expected_revision=2,
                status="COMPLETED",
                note="用户自报：旧版核对结束，缺口已记录，不代表接口已修复。",
                confirmed=True,
            )
            restored = WebService(settings, "execution-validation")
            report["restored"] = dict(
                old_execution=restored.executions.view(plan.object_id, 1),
                new_execution=restored.executions.view(plan.object_id, 2),
                frozen_execution=restored.get_turn(turn["id"])["result"]["snapshot"][
                    "old_plan"
                ]["execution"],
                new_body=restored.plans.get(plan.object_id, 2).body,
                old_body=restored.plans.get(plan.object_id, 1).body,
            )
    except Exception as exc:
        report["failure"] = dict(
            error_type=type(exc).__name__, code=getattr(exc, "code", None)
        )
    save()
    print(
        json.dumps(
            {
                "status": report.get("turn", {}).get("status"),
                "saved_version": report.get("saved_revision", {}).get("result_version"),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
