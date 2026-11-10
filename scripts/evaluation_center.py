"""MAGI Agent 测评中心：列出案例组、运行、查看记录、对照同一案例组。"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from xml.etree import ElementTree

REPO = Path(__file__).resolve().parents[1]
SUITES = {
    "research-offline": ("tests/test_research_tasks.py", "junit", 180),
    "research-cloud": ("scripts/evaluate_research_agents.py", "research", 2400),
    "memory-offline": ("scripts/evaluate_memory_recall.py", "memory", 180),
    "memory-cloud": ("scripts/evaluate_memory_recall.py", "memory", 240),
}
METRICS = (
    "hits",
    "false_positives",
    "misses",
    "micro_precision",
    "micro_recall",
    "correct_empty_cases",
    "boundary_violations",
    "failed_attempts",
    "sample_median_ms",
)


def read_evidence(path, kind):
    """把不同证据转成可比较的逐案结果，原始文件仍独立保留。"""
    if kind == "junit":
        cases = {}
        for node in ElementTree.parse(path).iter("testcase"):
            identity = f"{node.attrib['classname']}::{node.attrib['name']}"
            problem = next(
                (item for item in node if item.tag in {"failure", "error", "skipped"}),
                None,
            )
            cases[identity] = {
                "status": problem.tag.upper() if problem is not None else "PASSED",
                "seconds": float(node.attrib["time"]),
                "detail": problem.attrib.get("message")
                if problem is not None
                else None,
            }
        metrics = {
            f"cases.{key}": 0 for key in ("PASSED", "FAILURE", "ERROR", "SKIPPED")
        }
        metrics.update(
            {
                f"cases.{key}": count
                for key, count in Counter(
                    row["status"] for row in cases.values()
                ).items()
            }
        )
        return cases, metrics, None
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = {}
    for attempt in raw["attempts"]:
        if kind == "memory":
            target = set(attempt["expected"])
            forbidden = set(attempt["forbidden"])
            arms = {}
            for arm in ("default", "semantic"):
                result = attempt[arm]
                actual = {row["identity"] for row in result["items"]}
                arms[arm] = {
                    "status": result["status"],
                    "ms": result["ms"],
                    "matched": sorted(actual & target),
                    "missed": sorted(target - actual),
                    "unexpected": sorted(actual - target),
                    "forbidden": sorted(actual & forbidden),
                }
            cases[attempt["case"]] = arms
        else:
            task = attempt["task"]
            cases[attempt["case"]] = {
                "status": task["status"],
                "stop_reason": task["stop_reason"],
                "review_status": task["review_status"],
                "error_code": task["error_code"],
                "model_calls": sum(run["model_calls"] or 0 for run in attempt["runs"]),
                "tool_calls": sum(run["tool_calls"] or 0 for run in attempt["runs"]),
                "manual_review": "PENDING",
            }
    metrics = {
        f"{arm}.{key}": stats[key]
        for arm, stats in raw.get("summary", {}).items()
        for key in METRICS
    }
    if kind == "research":
        metrics.update(
            {
                "tasks.completed": sum(
                    row["status"] == "COMPLETED" for row in cases.values()
                ),
                "tasks.partial": sum(
                    row["status"] == "PARTIAL" for row in cases.values()
                ),
                "tasks.other": sum(
                    row["status"] not in {"COMPLETED", "PARTIAL"}
                    for row in cases.values()
                ),
                "model_calls": sum(row["model_calls"] for row in cases.values()),
                "tool_calls": sum(row["tool_calls"] for row in cases.values()),
            }
        )
    model = (
        {"name": raw["model"], "provider": raw["provider"]}
        if kind == "research"
        else None
    )
    return cases, metrics, model


def git_value(checkout, *arguments):
    """从明确指定的检出读取 Git 元数据，避免混用中心所在目录的版本。"""
    return subprocess.check_output(
        ["git", "-C", str(checkout), *arguments], text=True, encoding="utf-8"
    ).strip()


def save_manifest(directory, record):
    """原子更新运行状态。若评测中心被中断，原状态保留为 INCOMPLETE。"""
    pending = directory / "manifest.tmp"
    pending.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pending.replace(directory / "manifest.json")


def text_fingerprint(path):
    """统一文本换行再计算指纹，避免 Git 的 CRLF 转换破坏版本对照。"""
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def run_suite(root, suite, checkout, allow_dirty):
    """运行指定可信检出中的固定入口，每次分配独立目录和超时上限。"""
    source, kind, timeout = SUITES[suite]
    checkout = checkout.resolve()
    if Path(git_value(checkout, "rev-parse", "--show-toplevel")).resolve() != checkout:
        raise ValueError("checkout 必须是 Git 工作树根目录")
    commit = git_value(checkout, "rev-parse", "HEAD")
    dirty = bool(git_value(checkout, "status", "--porcelain"))
    if dirty and not allow_dirty:
        raise ValueError(
            "检出存在未提交变更；提交后再作版本对照，或显式传 --allow-dirty"
        )
    script = checkout / source
    suite_hash = text_fingerprint(script)
    directory = root / uuid4().hex
    directory.mkdir(parents=True)
    evidence = directory / ("evidence.xml" if kind == "junit" else "evidence.json")
    config = checkout / "magi.qianwen.toml"
    record = dict(
        schema=2,
        id=directory.name,
        suite=suite,
        commit=commit,
        dirty=dirty,
        suite_hash=suite_hash,
        started_at=datetime.now(timezone.utc).isoformat(),
        status="INCOMPLETE",
        evidence=evidence.name,
        cases={},
        metrics={},
        config_hash=text_fingerprint(config) if kind == "research" else None,
    )
    save_manifest(directory, record)
    if kind == "junit":
        args = ["-m", "pytest", "-q", source, f"--junitxml={evidence}"]
    else:
        args = [source, "--output", str(evidence)]
        if kind == "research":
            args += ["--config", str(config), "--data-dir", str(directory / "data")]
        elif suite.endswith("-cloud"):
            args += ["--cloud"]
    env = {**os.environ, "PYTHONPATH": str(checkout / "src")}
    started = perf_counter()
    try:
        with (directory / "process.log").open("wb") as log:
            result = subprocess.run(
                [sys.executable, "-X", "utf8", *args],
                cwd=checkout,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        record["exit_code"] = result.returncode
        record["status"] = "FINISHED" if result.returncode == 0 else "FAILED"
    except (subprocess.TimeoutExpired, OSError, KeyboardInterrupt) as exc:
        record["status"] = "FAILED"
        record["error"] = type(exc).__name__
    record["seconds"] = round(perf_counter() - started, 3)
    if evidence.is_file():
        try:
            record["cases"], record["metrics"], record["model"] = read_evidence(
                evidence, kind
            )
        except (ValueError, KeyError, TypeError, ElementTree.ParseError) as exc:
            record["status"] = "FAILED"
            record["error"] = f"证据读取失败：{type(exc).__name__}"
    if not record["cases"]:
        record["status"] = "FAILED"
        record.setdefault("error", "无逐案结果，请查看 process.log")
    if (
        git_value(checkout, "rev-parse", "HEAD") != commit
        or bool(git_value(checkout, "status", "--porcelain")) != dirty
    ):
        record["dirty"] = True
        record["checkout_changed"] = True
    save_manifest(directory, record)
    return record


def load_run(root, identity):
    """用完整运行 ID 查找记录，禁止路径片段作为 ID。"""
    if not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise ValueError("请提供 32 位运行 ID")
    return json.loads((root / identity / "manifest.json").read_text(encoding="utf-8"))


def compare(before, after):
    """只比较完整且定义一致的运行；按案例 ID 对齐，保留两侧原始指标。"""
    for key in ("schema", "suite", "suite_hash", "config_hash", "model"):
        if before.get(key) != after.get(key):
            raise ValueError(f"对照配置不同：{key}")
    if any(run["dirty"] or run["status"] == "INCOMPLETE" for run in (before, after)):
        raise ValueError("对照需要两个干净版本的已结束运行")
    if not before["cases"] or before["cases"].keys() != after["cases"].keys():
        raise ValueError("逐案证据不完整或案例集合不同")
    return {
        "suite": before["suite"],
        "before": {key: before[key] for key in ("id", "commit", "status")},
        "after": {key: after[key] for key in ("id", "commit", "status")},
        "metrics": {
            key: {
                "before": value,
                "after": after["metrics"][key],
                "delta": after["metrics"][key] - value
                if value is not None and after["metrics"][key] is not None
                else None,
            }
            for key, value in before["metrics"].items()
            if key in after["metrics"]
        },
        "cases": {
            key: {"before": case, "after": after["cases"][key]}
            for key, case in before["cases"].items()
        },
    }


def main():
    """统一 CLI 入口，JSON 输出供人工阅读或未来界面消费。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs-dir", type=Path, default=REPO / ".magi" / "agent-evaluations"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("suites")
    commands.add_parser("list")
    run = commands.add_parser("run")
    run.add_argument("suite", choices=SUITES)
    run.add_argument("--checkout", type=Path, default=REPO)
    run.add_argument("--allow-dirty", action="store_true")
    show = commands.add_parser("show")
    show.add_argument("id")
    diff = commands.add_parser("compare")
    diff.add_argument("before")
    diff.add_argument("after")
    args = parser.parse_args()
    root = args.runs_dir.resolve()
    exit_code = 0
    try:
        if args.command == "suites":
            value = {
                name: {"entry": spec[0], "timeout_seconds": spec[2]}
                for name, spec in SUITES.items()
            }
        elif args.command == "run":
            value = run_suite(root, args.suite, args.checkout, args.allow_dirty)
            exit_code = int(value["status"] != "FINISHED")
        elif args.command == "list":
            value = [
                {
                    key: row[key]
                    for key in (
                        "id",
                        "suite",
                        "commit",
                        "dirty",
                        "status",
                        "started_at",
                    )
                }
                for file in root.glob("*/manifest.json")
                if (row := load_run(root, file.parent.name))
            ]
            value.sort(key=lambda row: row["started_at"], reverse=True)
        elif args.command == "show":
            value = load_run(root, args.id)
        else:
            value = compare(load_run(root, args.before), load_run(root, args.after))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
