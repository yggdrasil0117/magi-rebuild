"""固定三例真实模型资料检索验收；无重试、不使用用户真实资料。"""

import argparse
import hashlib
import json
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage

from magi.harness.config import load_config
from magi.harness.runtime import open_personal_session
from magi.web.schemas import TurnInput
from magi.web.service import WebService


def seed_docs(root, *, partial=False):
    """写入公开合成资料，固定行号；partial另放超限文件验证覆盖不足提示。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "guide.md").write_text(
        "# 演示资料\n计划导出只生成文件，不代表任务已完成。\n计划的历史版本保留不变。\n",
        encoding="utf-8",
    )
    if partial:
        (root / "large.md").write_bytes(b"x" * 32_769)


def main():
    """每例独立数据库，保留所有回答、工具事实与失败，不自动生成记忆或文件。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = load_config(Path("magi.qianwen.toml"))
    cases = [
        (
            "found",
            "请用资料搜索工具检索关键词“计划导出”，说明导出成功是否代表任务完成，并给出来源文件路径和行号。",
        ),
        (
            "missing",
            "请用资料搜索工具精确检索“不存在标签QZX731”，没有命中就说明本次没找到，不要编造来源。",
        ),
        (
            "partial",
            "请用资料搜索工具检索“计划导出”，说明返回片段讲了什么，给出路径和行号，并如实报告搜索覆盖范围。",
        ),
    ]
    report = {
        "model": base.model,
        "mode": "real model, synthetic docs, separate temporary databases, no retries",
        "cases": [],
    }
    with tempfile.TemporaryDirectory(prefix="magi-search-") as temporary:
        for name, text in cases:
            root = Path(temporary) / name
            seed_docs(root / "docs", partial=name == "partial")
            settings = replace(
                base,
                docs_root=root / "docs",
                business_db_path=root / "business.db",
                db_path=root / "checkpoint.db",
                events_path=root / "events.jsonl",
            )
            service = WebService(settings, "search-validation")
            item = {"case": name, "input": text}
            started = time.monotonic()
            try:
                turn = service.turn(
                    TurnInput(
                        chat_id=name,
                        message_id="u1",
                        reply_message_id="a1",
                        user_text=text,
                    ),
                    "chat",
                )
                item.update(status=turn["status"], answer=turn["text"])
            except Exception as exc:
                item.update(
                    status="FAILED",
                    error_type=type(exc).__name__,
                    error_code=getattr(exc, "code", None),
                )
            item["seconds"] = round(time.monotonic() - started, 3)
            with open_personal_session(settings, service.logical_id(name)) as session:
                messages = session.messages()
            ai = [m for m in messages if isinstance(m, AIMessage)]
            item["model_calls_recorded"] = len(ai)
            item["tool_calls"] = [
                {"name": c["name"], "args": c["args"]} for m in ai for c in m.tool_calls
            ]
            item["tool_results"] = [
                {"name": m.name, "content": m.content}
                for m in messages
                if isinstance(m, ToolMessage)
            ]
            item["known_usage"] = [m.usage_metadata for m in ai if m.usage_metadata]
            searches = [
                json.loads(r["content"])
                for r in item["tool_results"]
                if r["name"] == "magi_search_files"
            ]
            item["branch_matches"] = (
                item["status"] == "COMPLETED"
                and bool(searches)
                and any(
                    (not r["matches"] if name == "missing" else bool(r["matches"]))
                    and r["complete"] == (name != "partial")
                    for r in searches
                )
            )
            with service.store.connect() as db:
                item["business_counts"] = {
                    table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("memories", "plans", "task_documents")
                }
                item["context_epoch"] = db.execute(
                    "SELECT context_epoch FROM app_state"
                ).fetchone()[0]
            report["cases"].append(item)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    report["source_sha256"] = {
        p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
        for p in (
            "src/magi/agent/search.py",
            "src/magi/harness/runtime.py",
            "scripts/evaluate_search.py",
        )
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "cases": len(report["cases"]),
                "matched": sum(c["branch_matches"] for c in report["cases"]),
            }
        )
    )


if __name__ == "__main__":
    main()
