"""固定比较旧硬切与重叠分块；真实BGE-M3，关键词对照，保留所有尝试。"""

import argparse
import json
import time
from pathlib import Path

import magi.retrieval as retrieval
from magi.council.service import Council
from magi.embedding import SiliconFlowEmbeddingProvider
from magi.harness.config import load_config


def legacy_chunks(text):
    """复现上一轮硬切算法，只用于单进程对照，不修改历史数据库。"""
    for number, line in enumerate(text.splitlines(), 1):
        for offset in range(0, len(line), 160):
            quote = text.splitlines()[number - 1][offset : offset + 160]
            if quote.strip():
                yield number, offset, quote


def main():
    """每种分块独立缓存；真实向量不重试，生成只验证新版一次，不保存正式计划。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.data_dir.exists():
        raise ValueError("输出和目录必须为新路径")
    docs = args.data_dir / "docs"
    docs.mkdir(parents=True)
    (docs / "boundary.txt").write_text(
        "甲" * 156
        + "SQLiteRestart恢复已提交事务；重启不代表未确认计划已经保存。"
        + "乙" * 130,
        encoding="utf-8",
    )
    (docs / "repeat.txt").write_text(
        "\n".join(
            ["SQLite重复提示：先核对版本。"] * 7 + ["SQLite补充提示：先备份再迁移。"]
        ),
        encoding="utf-8",
    )
    report = dict(
        mode="synthetic corpus; real embeddings; no retries; legacy segmentation reproduced",
        attempts=[],
    )
    current = retrieval.chunks
    current_policy = retrieval.CHUNK_POLICY
    provider = SiliconFlowEmbeddingProvider()
    snapshot = None
    try:
        for label, chunker in [("legacy", legacy_chunks), ("overlap", current)]:
            retrieval.chunks = chunker
            retrieval.CHUNK_POLICY = (
                "line160-legacy-evaluation-v1" if label == "legacy" else current_policy
            )
            # 显式命名空间隔离对照运行；旧算法结果不当成旧提交的完整重跑。
            local = retrieval.MagiLocalRetrievalProvider(
                docs, args.data_dir / (label + ".db"), embedding=provider
            )
            keyword = retrieval.MagiLocalRetrievalProvider(
                docs, args.data_dir / (label + ".db")
            )
            for query in ["SQLiteRestart", "SQLite", "量子引力实验"]:
                started = time.monotonic()
                lexical = keyword.retrieve(retrieval.RetrievalRequest(query))
                semantic = local.retrieve(retrieval.RetrievalRequest(query))
                report["attempts"].append(
                    dict(
                        segmentation=label,
                        query=query,
                        keyword=lexical,
                        vector=semantic,
                        seconds=time.monotonic() - started,
                    )
                )
                if label == "overlap" and query == "SQLiteRestart":
                    snapshot = dict(
                        question="SQLiteRestart重启时恢复什么？是否表示未确认计划已保存？请依据本轮资料引用原文并给出核对步骤。",
                        time_budget_minutes=None,
                        memories=[],
                        history=[],
                        old_plan=None,
                        retrieval=semantic,
                        evidence_ids=["user:current"]
                        + [m["evidence_ref"] for m in semantic["matches"]],
                    )
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(label + " " + query + ": " + semantic["status"], flush=True)
    finally:
        retrieval.chunks = current
        retrieval.CHUNK_POLICY = current_policy
    report["embedding_calls"] = provider.calls
    report["embedding_known_tokens"] = provider.tokens
    if snapshot:
        report["generation_snapshot"] = snapshot
        report["generation"] = Council(load_config(Path("magi.qianwen.toml"))).run(
            snapshot
        )
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "generation=" + report.get("generation", {}).get("status", "NOT_RUN"),
        flush=True,
    )


if __name__ == "__main__":
    main()
