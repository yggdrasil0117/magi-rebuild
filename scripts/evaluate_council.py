"""Bounded real-model evaluation; all attempts retained, no automatic retries."""

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from magi.council.service import Council
from magi.council.sources import source_options
from magi.harness.config import load_config

CASES = [
    {
        "id": "research_job",
        "background": "本周论文实验和求职准备共可用六小时，要保留休息。",
        "create": "权衡论文实验和求职准备，给出本周可验证的简短行动计划，包含休息。",
        "budget": 360,
        "old_body": "论文实验180分钟，求职准备120分钟，休息60分钟。",
        "feedback": "实验卡住了，本周只剩两小时。对照旧计划缩减任务，说明保留和放弃的事项。",
        "new_budget": 120,
    },
    {
        "id": "backend_agent",
        "background": "本周有四小时学习时间，想同时学习后端开发和Agent，目标是能解释并修改自己的项目。",
        "create": "比较两种学习投入，给出含休息的本周练习计划及完成条件。",
        "budget": 240,
        "old_body": "后端接口练习120分钟，Agent调用链练习90分钟，休息30分钟。",
        "feedback": "接口练习已完成，但Agent工具调用没看懂，只剩90分钟，请修订原计划并解释理由。",
        "new_budget": 90,
    },
    {
        "id": "weekend",
        "background": "周末有三小时，要采购生活用品、整理房间，也希望充分休息。",
        "create": "比较必要性与负担，给出简短安排，每步可判断完成，包含休息。",
        "budget": 180,
        "old_body": "采购60分钟，整理房间90分钟，休息30分钟。",
        "feedback": "突然下雨而且感觉疲惫，只剩60分钟，采购可以配送。请对照原计划缩减任务并保留休息。",
        "new_budget": 60,
    },
]

# 固定虚构输入，不查询实时价格。金额场景由人工核对，不能当作已有金额校验器。
COMPATIBILITY_CASES = [
    {
        "id": "travel_cost",
        "background": "以下均为测试中已给定的报价，不需要查询实时价格；不要求按分钟安排。",
        "create": "出差费用上限1800元：往返车票600元，住宿800元，餐饮200元。比较费用与必要性，给出行动及完成条件，不编造报价，不需要估计分钟。",
        "budget": None,
        "old_body": "往返车票600元，住宿800元，餐饮200元，合计1600元；预留200元。",
        "feedback": "出差费用上限降到1500元；车票600元不变，住宿新报价900元，餐饮200元。现有报价超预算，请说明缺口和待确认选项，不虚构已拿到折扣，不需要分钟安排。",
        "new_budget": None,
    },
    {
        "id": "purchase_choice",
        "background": "测试给定产品属性，不做实时商品推荐；没有时间预算。",
        "create": "团队选采购方案：A价格1200元且已确认兼容，B价格900元但兼容性未知。上限1300元，稳定性优先。给出比较、行动及完成条件，不需要估时。",
        "budget": None,
        "old_body": "优先A，1200元，在1300元以内；下单前确认售后。B兼容性未知，不当作已验证。",
        "feedback": "采购上限改为1000元，A价格仍为1200元，B仍为900元且尚未确认兼容。修订原建议，不能把兼容性推测当事实，不需要估时。",
        "new_budget": None,
    },
    {
        "id": "software_debug",
        "background": "这是模拟开发任务，不访问仓库、不实际执行代码。没有时间预算。",
        "create": "接口返回500，只有错误状态码，尚无日志和复现步骤。提出排查行动及完成条件，区分假设与事实，不猜测已确定根因，不需要估计分钟。",
        "budget": None,
        "old_body": "先取得最小复现与脱敏日志；核对请求参数和异常位置，再按证据提出修改。根因未确认。",
        "feedback": "已有稳定复现，脱敏日志显示数据库连接超时；但数据库宕机还是网络问题尚未确定。修订排查顺序，不重复要求从零收集复现，也不宣称已修复。不需要估时。",
        "new_budget": None,
    },
    CASES[1],  # 保留显式时间预算及预算缩减的对照场景。
]


def build_snapshot(case, phase, repeat):
    """把一个固定案例转换成 Council 输入；修订使用预设旧计划，独立于创建结果。"""
    creating = phase == "create"
    return {
        "run_id": f"eval-{case['id']}-{phase}-{repeat}",
        "logical_session_id": "eval-" + case["id"],
        "epoch": 0,
        "question": case["create"] if creating else case["feedback"],
        "time_budget_minutes": case["budget"] if creating else case["new_budget"],
        "memories": [],
        "history": [{"role": "human", "text": case["background"]}],
        "evidence_ids": ["user:current"],
        "old_plan": None
        if creating
        else {"title": "原计划", "version": 1, "body": case["old_body"]},
    }


def assess_result(snapshot, result):
    """独立核对工作流、输出结构、引用和预算；返回检查明细，不评判建议是否合理。"""
    personas = result.get("personas", [])
    candidate = result.get("candidate")
    completed = result.get("status") == "COMPLETED"
    checks = {
        "call_limit": type(result.get("calls")) is int and 0 <= result["calls"] <= 4,
        "three_roles": [p.get("role") for p in personas]
        == ["MELCHIOR", "BALTHASAR", "CASPER"],
        "candidate_requires_completion": bool(candidate) == completed,
        "all_roles_complete_before_candidate": not candidate
        or all(p.get("status") == "COMPLETED" and p.get("opinion") for p in personas),
        "known_evidence_only": all(
            set(p.get("opinion", {}).get("evidence_refs", []))
            <= set(snapshot["evidence_ids"])
            for p in personas
        ),
    }
    if candidate:
        options = {o["id"]: o for o in source_options(snapshot)}
        selected = candidate.get("source_ids", [])
        checks["selected_sources_resolved"] = not selected or (
            len(selected) == len(set(selected))
            and all(key in options for key in selected)
            and candidate.get("source_excerpts")
            == [
                {k: options[key][k] for k in ("evidence_ref", "quote")}
                for key in selected
            ]
        )
        steps = candidate.get("steps", [])
        checks["executable_steps"] = bool(steps) and all(
            isinstance(s.get("action"), str)
            and bool(s["action"].strip())
            and isinstance(s.get("done_when"), str)
            and bool(s["done_when"].strip())
            for s in steps
        )
        checks["valid_optional_labels"] = all(
            s.get("kind") is None
            or (isinstance(s["kind"], str) and 0 < len(s["kind"].strip()) <= 60)
            for s in candidate.get("steps", [])
        )
        sources = {"user:current": snapshot.get("question", "")}
        sources.update(
            {"memory:" + m["key"]: m["value"] for m in snapshot.get("memories", [])}
        )
        checks["source_excerpts_match"] = all(
            item.get("evidence_ref") in snapshot["evidence_ids"]
            and item.get("evidence_ref") in sources
            and bool(item.get("quote"))
            and item["quote"] in sources[item["evidence_ref"]]
            for item in candidate.get("source_excerpts", [])
        )
        minutes = [s.get("minutes") for s in candidate.get("steps", [])]
        budget = snapshot.get("time_budget_minutes")
        checks["positive_step_minutes"] = bool(minutes) and all(
            (m is None and budget is None) or (type(m) is int and 0 < m <= 10080)
            for m in minutes
        )
        if budget is not None:
            checks["within_time_budget"] = (
                checks["positive_step_minutes"] and sum(minutes) <= budget
            )
    return {"passed": all(checks.values()), "checks": checks}


def summarize_report(report, seconds):
    """汇总全部尝试；部分失败与完成分开计数，缺失用量不当成零消耗。"""
    runs = report["runs"]
    statuses = Counter(r["result"]["status"] for r in runs)
    times = [r["result"]["seconds"] for r in runs if "seconds" in r["result"]]
    diagnostics = []
    failures = Counter()
    timeouts = Counter()
    for run in runs:
        result = run["result"]
        for persona in result.get("personas", []):
            diagnostics.append(persona.get("diagnostics", {}))
            if persona.get("error_code"):
                failures[persona["error_code"]] += 1
                if persona["error_code"] == "TIMEOUT":
                    d = persona.get("diagnostics", {})
                    timeouts[
                        f"{d.get('timeout_scope', 'unknown')}:{d.get('timeout_phase', 'unknown')}"
                    ] += 1
        if "synthesis_diagnostics" in result:
            diagnostics.append(result["synthesis_diagnostics"])
        if result.get("synthesis_error_code"):
            failures[result["synthesis_error_code"]] += 1
            if result["synthesis_error_code"] == "TIMEOUT":
                d = result.get("synthesis_diagnostics", {})
                timeouts[
                    f"{d.get('timeout_scope', 'unknown')}:{d.get('timeout_phase', 'unknown')}"
                ] += 1
    known_usage = [
        d
        for d in diagnostics
        if all(
            type(d.get(k)) is int
            for k in ("input_tokens", "output_tokens", "total_tokens")
        )
    ]
    return {
        "attempted": len(runs),
        "planned": report.get("planned_runs", report["repeats"] * len(CASES) * 2),
        "statuses": dict(statuses),
        "failure_codes": dict(failures),
        "timeouts_by_scope_and_phase": dict(timeouts),
        "boundary_checks_passed": sum(
            r.get("boundary_checks", {}).get("passed", False) for r in runs
        ),
        "model_invocations": sum(r["result"].get("calls", 0) for r in runs),
        "usage": {
            "calls_with_usage": len(known_usage),
            "calls_without_usage": max(
                0, sum(r["result"].get("calls", 0) for r in runs) - len(known_usage)
            ),
            "known_input_tokens": sum(d["input_tokens"] for d in known_usage),
            "known_output_tokens": sum(d["output_tokens"] for d in known_usage),
            "known_total_tokens": sum(d["total_tokens"] for d in known_usage),
        },
        "run_seconds": {
            "min": min(times),
            "median": round(statistics.median(times), 3),
            "max": max(times),
        }
        if times
        else None,
        "seconds": round(seconds, 3),
        "semantic_quality": "not automatically graded; review every candidate against the snapshot",
    }


def main():
    """选择固定案例集，逐轮留证；dry-run 不读取密钥或调用模型。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--label", choices=["baseline", "updated"], default="updated")
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--suite", choices=["original", "compatibility"], default="original"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show bounded invocation count without contacting a model",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "magi.qianwen.toml",
    )
    args = parser.parse_args()
    cases = CASES if args.suite == "original" else COMPATIBILITY_CASES
    planned_runs = args.repeats * len(cases) * 2
    root = Path(__file__).resolve().parents[1]
    settings = load_config(args.config)
    if args.model:
        settings = replace(settings, model=args.model)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "provider": settings.provider,
                    "model": settings.model,
                    "suite": args.suite,
                    "planned_runs": planned_runs,
                    "max_model_invocations": planned_runs * 4,
                },
                ensure_ascii=False,
            )
        )
        return 0
    # 缺少密钥时提前停止，避免把配置问题记录成整批模型质量失败。
    if (
        settings.provider == "qianwen"
        and not os.environ.get(settings.api_key_env, "").strip()
    ):
        parser.error(f"缺少环境变量 {settings.api_key_env}；尚未调用模型")
    output_dir = root / ".magi" / "evaluations"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = output_dir / f"council-{stamp}-{args.label}.json"
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": args.label,
        "model": settings.model,
        "provider": settings.provider,
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "scope": "council only, fixed independent create/revision cases, real model",
        "case_set_sha256": hashlib.sha256(
            json.dumps(cases, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "source_files_sha256": {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (root / "src/magi/council").glob("*.py")
        },
        "num_ctx": settings.num_ctx,
        "repeats": args.repeats,
        "suite": args.suite,
        "planned_runs": planned_runs,
        "runs": [],
    }
    started = time.monotonic()
    try:
        for repeat in range(args.repeats):
            for case in cases:
                for phase in ("create", "revision"):
                    snapshot = build_snapshot(case, phase, repeat)
                    try:
                        result = Council(settings).run(snapshot)
                    except Exception as exc:
                        result = {"status": "FAILED", "error": type(exc).__name__}
                    report["runs"].append(
                        {
                            "case": case["id"],
                            "phase": phase,
                            "repeat": repeat,
                            "snapshot": snapshot,
                            "result": result,
                            "boundary_checks": assess_result(snapshot, result),
                        }
                    )
                    output.write_text(
                        json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(
                        f"{repeat + 1}/{args.repeats} {case['id']} {phase}: {result['status']}",
                        flush=True,
                    )
    finally:
        statuses = Counter(r["result"]["status"] for r in report["runs"])
        report["summary"] = summarize_report(report, time.monotonic() - started)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
        print(output, flush=True)
    return (
        0
        if statuses.get("COMPLETED", 0) == report["summary"]["planned"]
        and report["summary"]["boundary_checks_passed"] == report["summary"]["planned"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
