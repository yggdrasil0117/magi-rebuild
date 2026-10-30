"""固定小批记忆候选评测：只使用真实提取模型，主回答为桩，不自动确认。"""

import hashlib
import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from magi.harness.config import load_config
from magi.web.schemas import TurnInput
from magi.web.service import WebService

CASES = [
    ("preference", "以后解释代码时，我更喜欢先讲原理，再给代码。", "add", None),
    (
        "background",
        "我目前是计算机专业的研究生，这个背景可以作为之后讨论科研问题的参考。",
        "add",
        None,
    ),
    (
        "goal",
        "接下来一年，我打算持续学习后端与Agent开发，目标是能独立维护自己的项目。",
        "add",
        None,
    ),
    ("temporary", "今天只剩90分钟，请安排一下今天的练习。", "none", None),
    (
        "quoted_document",
        "下面是一段文档示例，不是我的偏好：用户喜欢先看代码，不喜欢解释。",
        "none",
        None,
    ),
    (
        "uncertain",
        "如果我以后改做前端，可能会喜欢先看界面，现在还没决定。",
        "none",
        None,
    ),
    (
        "duplicate",
        "解释代码时，我喜欢先讲原理，再给代码。",
        "none",
        "先讲原理，再给代码。",
    ),
    (
        "update",
        "我调整一下长期回答偏好：以后先给具体例子，再解释原理。",
        "update",
        "先讲原理，再给代码。",
    ),
]


@contextmanager
def fake_session(*args):
    session = MagicMock()
    session.ask.return_value = "主回答为桩，不参与记忆抽取。"
    yield session


def main():
    """八个独立案例、最多八次模型调用，所有结果保留；候选绝不自动生效。"""
    root = Path(__file__).resolve().parents[1]
    settings = load_config(root / "magi.qianwen.toml")
    if not os.environ.get(settings.api_key_env, "").strip():
        raise SystemExit("缺少云端密钥，尚未调用模型")
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": settings.model,
        "scope": "real memory extraction only; fake primary answer; no confirmation",
        "max_calls": len(CASES),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "case_set_sha256": hashlib.sha256(
            json.dumps(CASES, ensure_ascii=False).encode()
        ).hexdigest(),
        "source_files_sha256": {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                root / "src/magi/personal/extraction.py",
                root / "src/magi/personal/suggestions.py",
                root / "src/magi/web/service.py",
            ]
        },
        "runs": [],
    }
    output_dir = root / ".magi/evaluations"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / (
        "memory-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    )
    with tempfile.TemporaryDirectory() as directory:
        for index, (name, question, expected, old) in enumerate(CASES):
            data = Path(directory) / str(index)
            data.mkdir()
            config = replace(
                settings,
                db_path=data / "checkpoint.db",
                business_db_path=data / "business.db",
                events_path=data / "events.jsonl",
            )
            service = WebService(config, "evaluation", session_factory=fake_session)
            if old:
                service.memories.add(
                    key="回答偏好", value=old, scope="global", request_id="seed"
                )
            before = [m.id for m in service.memories.list_active()]
            turn = service.turn(
                TurnInput(
                    chat_id="eval",
                    message_id="u1",
                    reply_message_id="a1",
                    user_text=question,
                ),
                "chat",
            )
            result = service.propose_memory(turn["id"])
            candidates = service.suggestions.pending("eval")
            actual = (
                ("update" if candidates[0]["target_id"] else "add")
                if candidates
                else "none"
            )
            valid_no_candidate = result["status"] in {"NO_SUGGESTION", "DUPLICATE"}
            report["runs"].append(
                dict(
                    case=name,
                    question=question,
                    expected=expected,
                    actual=actual,
                    result=result,
                    candidates=candidates,
                    decision_check=(
                        actual == expected and (bool(candidates) or valid_no_candidate)
                    ),
                    confirmed_memory_unchanged=before
                    == [m.id for m in service.memories.list_active()],
                )
            )
            output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"{name}: {result['status']} ({actual})", flush=True)
    report["summary"] = {
        "attempted": len(report["runs"]),
        "decision_checks_passed": sum(r["decision_check"] for r in report["runs"]),
        "confirmed_memory_unchanged": all(
            r["confirmed_memory_unchanged"] for r in report["runs"]
        ),
        "semantic_quality": "manual review required; no general accuracy claim",
    }
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    print(output)
    return 0 if report["summary"]["decision_checks_passed"] == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
