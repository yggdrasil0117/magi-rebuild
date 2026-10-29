"""Small real capability probe using an environment key and isolated data."""

import json
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from magi.council.service import Council, Opinion
from magi.harness.config import load_config
from magi.harness.runtime import open_personal_session
from magi.personal.plans import PlanService
from magi.personal.store import PersonalStore


def main():
    root = Path(__file__).resolve().parents[1]
    settings = load_config(root / "magi.qianwen.toml")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / ".magi" / f"qianwen-probe-{stamp}.json"
    output.parent.mkdir(exist_ok=True)
    report = {
        "model": settings.model,
        "provider": settings.provider,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        snapshot = {
            "question": "总时间30分钟，给出最简可行性意见。",
            "time_budget_minutes": 30,
            "evidence_ids": ["user:current"],
            "memories": [],
            "history": [],
            "old_plan": None,
        }
        council = Council(settings)
        diagnostics = {}
        opinion = council._generate(
            "只生成简短可行性意见，各字段一句，严格遵循JSON schema。",
            json.dumps(snapshot, ensure_ascii=False),
            Opinion,
            768,
            60,
            diagnostics,
        )
        report["structured"] = {
            "passed": True,
            "opinion": opinion.model_dump(),
            "diagnostics": diagnostics,
        }
        print("structured JSON schema: passed", flush=True)
        with tempfile.TemporaryDirectory(prefix="magi-qianwen-probe-") as directory:
            data = Path(directory)
            isolated = replace(
                settings,
                db_path=data / "checkpoint.db",
                business_db_path=data / "business.db",
                events_path=data / "events.jsonl",
            )
            receipt = PlanService(PersonalStore(isolated.business_db_path)).create(
                title="千问接口测试计划",
                body="这是隔离测试数据，不是用户真实计划。",
                request_id="qianwen-probe",
                source_epoch=0,
            )
            with open_personal_session(isolated, "qianwen-probe") as session:
                text = session.ask(
                    f"请必须调用 magi_get_plan 查询计划 {receipt.object_id}，然后只回答该计划标题。"
                )
            events = [
                json.loads(line)
                for line in isolated.events_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            calls = [
                e
                for e in events
                if e.get("type") == "tool_started" or e.get("event") == "tool_started"
            ]
            report["harness"] = {
                "text": text,
                "events": events,
                "tool_called": bool(calls),
            }
            if not calls:
                raise RuntimeError("no observed tool dispatch")
            print("read-only harness tool: passed", flush=True)
        report["passed"] = True
    except Exception as exc:
        report.update(
            passed=False,
            error_type=type(exc).__name__,
            status_code=getattr(exc, "status_code", None),
        )
        print("probe failed:", type(exc).__name__, flush=True)
    finally:
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(output, flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
