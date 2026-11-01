"""硅基流动向量固定对照与真实WebService保存验收；合成资料，无重试。"""

import argparse
import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

from magi.embedding import SiliconFlowEmbeddingProvider
from magi.harness.config import load_config
from magi.retrieval import MagiLocalRetrievalProvider, RetrievalRequest
from magi.web.schemas import Confirmation, TurnInput
from magi.web.service import WebService

CASES = [
    ("literal", "计划导出", "guide.md"),
    ("paraphrase", "输出清单算办妥了吗", "guide.md"),
    ("irrelevant", "量子引力的实验测量结果", None),
    ("english", "SQLite restart recovery", "database.md"),
]


def main():
    """output拒绝覆盖；data-dir独立保留数据库用于重启核验，不触及用户资料。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.data_dir.exists():
        raise ValueError("评测输出和数据目录必须为新路径")
    docs = args.data_dir / "docs"
    docs.mkdir(parents=True)
    (docs / "guide.md").write_text(
        "计划导出只生成文件，不代表执行完成。完成应逐项检查实际产物。", encoding="utf-8"
    )
    (docs / "database.md").write_text(
        "SQLite restart recovery restores committed transactions. Back up the database before migration.",
        encoding="utf-8",
    )
    (docs / "rest.md").write_text(
        "休息安排：每完成一段工作，可以起身活动并补充饮水。", encoding="utf-8"
    )
    base = load_config(Path("magi.qianwen.toml"))
    report = dict(
        mode="real SiliconFlow BGE-M3; fixed synthetic corpus; no retries",
        min_score=base.embedding_min_score,
        attempts=[],
    )
    report["source_sha256"] = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [
            Path(__file__),
            Path("src/magi/embedding.py"),
            Path("src/magi/retrieval.py"),
        ]
    }
    embedding = SiliconFlowEmbeddingProvider()
    keyword = MagiLocalRetrievalProvider(docs, args.data_dir / "retrieval.db")
    semantic = MagiLocalRetrievalProvider(
        docs,
        args.data_dir / "retrieval.db",
        embedding=embedding,
        min_score=base.embedding_min_score,
    )

    def save():
        report["embedding_calls"] = embedding.calls
        report["embedding_known_tokens"] = embedding.tokens
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    for name, query, expected in CASES:
        started = time.monotonic()
        baseline = keyword.retrieve(RetrievalRequest(query))
        result = semantic.retrieve(RetrievalRequest(query))
        report["attempts"].append(
            dict(
                case=name,
                query=query,
                expected_top=expected,
                keyword=baseline,
                vector=result,
                seconds=time.monotonic() - started,
            )
        )
        save()
        print(name + ": " + result["status"], flush=True)
    settings = replace(
        base,
        docs_root=docs,
        db_path=args.data_dir / "checkpoint.db",
        business_db_path=args.data_dir / "business.db",
        events_path=args.data_dir / "events.jsonl",
        embedding_enabled=True,
    )
    service = WebService(settings, "embedding-validation")
    started = time.monotonic()
    try:
        turn = service.turn(
            TurnInput(
                chat_id="embedding",
                message_id="u1",
                reply_message_id="a1",
                user_text="输出清单算办妥了吗？请根据资料制定两步核对计划，引用资料；不要把文件生成等同于执行完成。",
            ),
            "council",
        )
        report["web"] = dict(
            status=turn["status"],
            result=turn["result"],
            seconds=time.monotonic() - started,
        )
        save()
        if turn["proposal_id"]:
            proposal = service.proposal(turn["proposal_id"], "embedding", "a1")
            receipt = service.confirm(
                proposal["id"],
                Confirmation(
                    chat_id="embedding",
                    reply_message_id="a1",
                    payload_hash=proposal["payload_hash"],
                ),
            )
            saved = service.plans.get(receipt["object_id"], receipt["result_version"])
            (docs / "guide.md").write_text(
                "计划导出新版：核对文件可打开，并逐项核对执行情况。", encoding="utf-8"
            )
            new = semantic.retrieve(RetrievalRequest("计划导出"))
            restarted = WebService(settings, "embedding-validation")
            restored = restarted.get_turn(turn["id"])
            report["persistence"] = dict(
                saved_version=receipt["result_version"],
                old_body_unchanged=restarted.plans.get(receipt["object_id"], 1).body
                == saved.body,
                old_snapshot_unchanged=restored["result"]["snapshot"]
                == turn["result"]["snapshot"],
                new_version_result=new,
                plan_body=saved.body,
            )
    except Exception as exc:
        report["web_failure"] = dict(
            error_type=type(exc).__name__, code=getattr(exc, "code", None)
        )
    save()
    print(
        json.dumps(
            {
                "cases": len(report["attempts"]),
                "web_status": report.get("web", {}).get("status"),
                "embedding_calls": embedding.calls,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
