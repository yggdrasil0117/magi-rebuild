"""固定离线RAG基线，所有尝试留存，不调用生成模型、不重试。"""

import argparse
import json
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

from magi.council.sources import selected_excerpts
from magi.retrieval import MagiLocalRetrievalProvider, RetrievalRequest


def main():
    """固定资料与问题分别记录程序边界、召回表现及未执行的语义复核。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("拒绝覆盖已有评测，复测须使用新路径")
    report = dict(
        mode="offline; no retries; synthetic documents",
        attempts=[],
        model_calls=0,
        tokens=0,
        semantic_review="未执行真实模型引用支持与三人格内容复核",
    )
    with tempfile.TemporaryDirectory(prefix="magi-rag-") as temporary:
        root = Path(temporary)
        docs = root / "docs"
        docs.mkdir()
        guide = docs / "guide.txt"
        guide.write_text("计划导出只生成文件，不代表执行完成。", encoding="utf-8")
        provider = MagiLocalRetrievalProvider(docs, root / "index.db")
        for name, question in [
            ("原词", "计划导出"),
            ("同义改写", "输出清单算办妥了吗"),
            ("资料无答案", "量子引力"),
        ]:
            begin = time.perf_counter()
            result = provider.retrieve(RetrievalRequest(question))
            report["attempts"].append(
                dict(
                    case=name,
                    question=question,
                    result=result,
                    seconds=time.perf_counter() - begin,
                )
            )
        old = report["attempts"][0]["result"]["matches"][0]
        guide.write_text("计划导出：新版仍需检查实际执行结果。", encoding="utf-8")
        new = provider.retrieve(RetrievalRequest("计划导出"))["matches"][0]
        report["attempts"].append(
            dict(
                case="同文档新旧版本",
                passed=old["document_id"] == new["document_id"]
                and old["version_id"] != new["version_id"],
            )
        )
        guide.unlink()
        report["attempts"].append(
            dict(
                case="文件删除", result=provider.retrieve(RetrievalRequest("计划导出"))
            )
        )
        report["attempts"].append(
            dict(
                case="检索失败",
                result=MagiLocalRetrievalProvider(root / "missing").retrieve(
                    RetrievalRequest("计划导出")
                ),
            )
        )
        try:
            selected_excerpts(
                ["forged"], dict(evidence_ids=[], retrieval={"matches": []})
            )
            rejected = False
        except ValueError:
            rejected = True
        report["attempts"].append(dict(case="伪造引用", rejected=rejected))
        with closing(sqlite3.connect(root / "index.db")) as db:
            versions = db.execute("SELECT count(*) FROM versions").fetchone()[0]
        report["attempts"].append(
            dict(
                case="索引重开",
                versions=versions,
                result=MagiLocalRetrievalProvider(docs, root / "index.db").retrieve(
                    RetrievalRequest("计划导出")
                ),
                note="仅索引重开，进程重启另见浏览器验收",
            )
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"attempts": len(report["attempts"]), "model_calls": 0}))


if __name__ == "__main__":
    main()
