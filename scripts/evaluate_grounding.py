"""固定同快照比较目标/证据提示改动；真实生成，无重试，不保存正式计划。"""

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

from magi.council.service import Council
from magi.harness.config import load_config

BASELINE_REF = "69ad835"


def cases():
    """两个历史失败输入加一个新场景；快照复用已有原文，隔离检索随机性。"""
    historical = json.loads(
        Path("docs/validation/030-chunking-first.json").read_text(encoding="utf-8")
    )["generation_snapshot"]
    missing = json.loads(
        Path("docs/validation/028-rag-cloud-retest.json").read_text(encoding="utf-8")
    )["attempts"][1]["snapshot"]
    holdout = dict(
        question="我只想整理购买显示器前要向商家询问的问题，暂不决定型号、不下单。请给两步准备计划。",
        time_budget_minutes=None,
        memories=[],
        history=[],
        old_plan=None,
        evidence_ids=["user:current", "document:synthetic-monitor"],
        retrieval=dict(
            status="FOUND",
            matches=[
                dict(
                    evidence_ref="document:synthetic-monitor",
                    quote="采购流程示例：比较接口和保修条款；确认报价后可下单。",
                    path="synthetic-monitor.txt",
                    version_id="synthetic-version",
                    document_id="synthetic-document",
                    chunk_id="synthetic-monitor",
                    line=1,
                    offset=0,
                )
            ],
            complete=True,
            notice="离线合成快照，不表示授权购买",
        ),
    )
    return [
        ("historical_scope", historical),
        ("historical_missing", missing),
        ("new_purchase_scope", holdout),
    ]


def baseline_council(directory):
    """从固定旧提交提取改动前service，避免脚本提交后HEAD变成新版而失去对照。"""
    source = subprocess.check_output(
        ["git", "show", f"{BASELINE_REF}:src/magi/council/service.py"]
    )
    path = directory / "baseline_service.py"
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("magi_grounding_baseline", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.Council, hashlib.sha256(source).hexdigest()


def main():
    """每个案例前后各最多4次生成，共24次；全部尝试逐项落盘，无自动内容判分。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.data_dir.exists():
        raise ValueError("输出和数据目录必须为新路径")
    args.data_dir.mkdir(parents=True)
    baseline, source_hash = baseline_council(args.data_dir)
    settings = load_config(Path("magi.qianwen.toml"))
    dataset = cases()
    report = dict(
        mode="fixed frozen snapshots; real cloud; baseline then updated; no retries",
        max_calls=24,
        baseline_service_sha256=source_hash,
        dataset_sha256=hashlib.sha256(
            json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        attempts=[],
        human_review="待人工复核，不把结构完成等同内容正确",
    )
    report["source_sha256"] = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [
            Path(__file__),
            Path("src/magi/council/grounding.py"),
            Path("src/magi/council/service.py"),
        ]
    }
    for label, implementation in [("baseline", baseline), ("updated", Council)]:
        for name, snapshot in dataset:
            started = time.monotonic()
            try:
                result = implementation(settings).run(deepcopy(snapshot))
            except Exception as exc:
                result = dict(status="FAILED", error_type=type(exc).__name__)
            report["attempts"].append(
                dict(
                    variant=label,
                    case=name,
                    snapshot=snapshot,
                    result=result,
                    seconds=time.monotonic() - started,
                )
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(label + " " + name + ": " + result["status"], flush=True)
    report["summary"] = dict(
        completed=sum(a["result"]["status"] == "COMPLETED" for a in report["attempts"]),
        attempted=len(report["attempts"]),
        calls=sum(a["result"].get("calls", 0) for a in report["attempts"]),
    )
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
