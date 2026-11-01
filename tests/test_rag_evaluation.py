"""固定云端验收器的离线控制测试，不请求网络或读取凭据。"""

import importlib.util
import json
from pathlib import Path

from magi.harness.config import HarnessConfig


def test_authentication_failure_stops_remaining_cases(tmp_path, monkeypatch):
    """共享鉴权失败后保留首例及未执行清单，禁止继续调用其他案例。"""
    spec = importlib.util.spec_from_file_location(
        "rag_evaluation", Path("scripts/evaluate_rag_cloud.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    class FakeCouncil:
        def __init__(self, settings):
            pass

        def run(self, snapshot):
            calls.append(snapshot)
            return dict(
                status="FAILED",
                candidate=None,
                calls=3,
                personas=[dict(error_code="AUTHENTICATION_FAILED") for _ in range(3)],
            )

    monkeypatch.setattr(module, "Council", FakeCouncil)
    settings = HarnessConfig(
        docs_root=tmp_path,
        db_path=tmp_path / "checkpoint.db",
        events_path=tmp_path / "events.jsonl",
    )
    output = tmp_path / "report.json"
    module.evaluate(settings, output)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert len(calls) == 1 and report["summary"]["attempted"] == 1
    assert report["stopped_reason"] == "AUTHENTICATION_FAILED"
    assert len(report["not_run_cases"]) == 3
    assert report["attempts"][0]["result"]["status"] == "FAILED"
