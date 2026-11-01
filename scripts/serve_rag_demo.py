"""独立RAG离线页面验收服务；真实资料/数据库/HTTP，模型与会话明确使用桩。"""

import argparse
import json
import os
from pathlib import Path

import uvicorn
from serve_plan_demo import DemoMemoryExtractor, demo_session

from magi.council.service import Council, Opinion
from magi.council.sources import source_options
from magi.harness.config import HarnessConfig
from magi.web.app import create_app
from magi.web.service import WebService


def response(system, payload, schema, *args):
    """角色选择本轮资料编号；综合只选目录，原文由业务程序补入。"""
    data = json.loads(payload)
    snapshot = data if schema is Opinion else data["snapshot"]
    refs = [m["evidence_ref"] for m in snapshot["retrieval"]["matches"]]
    if schema is Opinion:
        return json.dumps(
            dict(
                view="导出与执行完成分开核对。",
                recommendation="按资料检查实际产物。",
                concerns=[],
                unknowns=[],
                evidence_refs=refs[:1],
            ),
            ensure_ascii=False,
        )
    options = [m["id"] for m in source_options(snapshot) if m["evidence_ref"] in refs]
    return json.dumps(
        dict(
            agreement=["检查实际执行结果"],
            disagreements=[],
            unknowns=[],
            candidate=dict(
                title="计划导出核对",
                steps=[dict(action="导出后核对执行结果", done_when="记录实际完成情况")],
                assumptions=[],
                source_ids=options[:1],
                unknowns=[],
                revision_reason="资料说明导出不代表执行完成",
            ),
        ),
        ensure_ascii=False,
    )


def main():
    """复用数据目录便于真实进程重启；已有资料不覆盖。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8883)
    args = parser.parse_args()
    docs = args.data_dir / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    guide = docs / "guide.md"
    if not guide.exists():
        guide.write_text(
            "# 合成资料\n计划导出只生成文件，不代表执行完成。\n", encoding="utf-8"
        )
    settings = HarnessConfig(
        docs_root=docs,
        db_path=args.data_dir / "checkpoint.db",
        events_path=args.data_dir / "events.jsonl",
        business_db_path=args.data_dir / "business.db",
    )
    service = WebService(
        settings,
        "rag-demo",
        council=Council(settings, response),
        session_factory=demo_session,
        memory_extractor=DemoMemoryExtractor(),
    )
    uvicorn.run(
        create_app(settings, service=service, token=os.environ["MAGI_API_TOKEN"]),
        host="127.0.0.1",
        port=args.port,
    )


if __name__ == "__main__":
    main()
