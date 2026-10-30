"""仅用于离线网页验收：真实业务数据库与 HTTP，模拟模型和 checkpoint。

必须指定独立数据目录和 MAGI_API_TOKEN；不读取云端密钥，不启动本地模型。
"""

import argparse
import json
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import uvicorn

from magi.council.service import Council, Opinion
from magi.harness.config import HarnessConfig
from magi.web.app import create_app
from magi.web.service import WebService


def demo_response(system, payload, schema, *args):
    """返回固定模拟结果，测试确认和版本链路，不作为模型质量证据。"""
    if schema is Opinion:
        return json.dumps(
            dict(
                view="模拟意见",
                recommendation="按反馈缩小范围",
                concerns=[],
                unknowns=[],
                evidence_refs=[],
            )
        )
    return json.dumps(
        dict(
            agreement=["依据最新反馈调整"],
            disagreements=[],
            unknowns=[],
            candidate=dict(
                title="学习安排·反馈修订",
                steps=[dict(action="定位接口问题", done_when="记录复现或待验证缺口")],
                assumptions=[],
                source_ids=["source:0"],
                unknowns=[],
                revision_reason="保留定位，暂缓扩展练习",
            ),
        ),
        ensure_ascii=False,
    )


@contextmanager
def demo_session(*args):
    """checkpoint 仅为桩；业务回合、候选和计划版本仍由真实 SQLite 保存。"""
    session = MagicMock()
    session.agent.get_state.return_value.next = ()
    session.messages.return_value = []
    session.ask.return_value = "这是离线演示回复，不调用真实模型。"
    yield session


def main():
    """启动本机独立测试服务；数据目录可复用以验证进程重启后的版本保留。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8876)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.data_dir.mkdir(parents=True, exist_ok=True)
    settings = HarnessConfig(
        docs_root=root / "docs",
        db_path=args.data_dir / "checkpoint.db",
        events_path=args.data_dir / "events.jsonl",
        business_db_path=args.data_dir / "business.db",
    )
    service = WebService(
        settings,
        "offline-demo",
        council=Council(settings, demo_response),
        session_factory=demo_session,
    )
    if not service.plans.list_current():
        plan = service.plans.create(
            title="学习与项目练习",
            body="阅读调用链\n完成接口练习\n保留休息",
            request_id="demo-create",
            source_epoch=0,
        )
        service.plans.update(
            plan.object_id,
            title="学习与项目练习",
            body="阅读调用链\n定位工具调用卡点\n保留休息",
            expected_version=1,
            request_id="demo-update",
            source_epoch=0,
        )
        service.plans.create(
            title="采购方案",
            body="比较已知报价\n确认兼容后再决定",
            request_id="demo-purchase",
            source_epoch=0,
        )
    app = create_app(
        settings, service=service, token=os.environ.get("MAGI_API_TOKEN", "")
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
