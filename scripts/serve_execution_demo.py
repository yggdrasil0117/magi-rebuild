"""执行跟踪完整页面验收服务：真实HTTP/SQLite，明确脚本模型与checkpoint。"""

import argparse
import json
import os
from pathlib import Path

import uvicorn
from serve_plan_demo import DemoMemoryExtractor, demo_session

from magi.council.service import Council, Opinion
from magi.harness.config import HarnessConfig
from magi.web.app import create_app
from magi.web.service import WebService


def response(system, payload, schema, *args):
    """替身读取真实冻结执行记录，验证集成，不冒充模型理解质量。"""
    data = json.loads(payload)
    snapshot = data if schema is Opinion else data["snapshot"]
    execution = (snapshot.get("old_plan") or {}).get("execution", {})
    if schema is Opinion:
        return json.dumps(
            dict(
                view="执行记录状态：" + execution.get("status", "未知"),
                recommendation="针对实际阻塞缩减下一步",
                concerns=[],
                unknowns=[],
                evidence_refs=[],
            ),
            ensure_ascii=False,
        )
    return json.dumps(
        dict(
            agreement=["依据用户记录调整"],
            disagreements=[],
            unknowns=[],
            candidate=dict(
                title="执行反馈后的修订安排",
                steps=[
                    dict(
                        action="先复现接口阻塞并记录结果",
                        done_when="记录复现结果或无法复现的证据缺口",
                    )
                ],
                assumptions=[],
                source_ids=["source:0"],
                unknowns=[],
                revision_reason="依据此版本用户自报执行记录修订，尚未自动执行",
            ),
        ),
        ensure_ascii=False,
    )


def main():
    """独立资料目录不含用户文档；复用数据目录做真实进程重启，不重复种子计划。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8885)
    args = parser.parse_args()
    docs = args.data_dir / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    settings = HarnessConfig(
        docs_root=docs,
        db_path=args.data_dir / "checkpoint.db",
        business_db_path=args.data_dir / "business.db",
        events_path=args.data_dir / "events.jsonl",
    )
    service = WebService(
        settings,
        "execution-demo",
        council=Council(settings, response),
        session_factory=demo_session,
        memory_extractor=DemoMemoryExtractor(),
    )
    if not service.plans.list_current():
        service.plans.create(
            title="接口与Agent学习安排",
            body="核对接口返回，记录调用链与验证结果。",
            request_id="execution-seed",
            source_epoch=0,
        )
    uvicorn.run(
        create_app(settings, service=service, token=os.environ["MAGI_API_TOKEN"]),
        host="127.0.0.1",
        port=args.port,
    )


if __name__ == "__main__":
    main()
