"""使用隔离数据库验证低容量续聊，凭据由调用进程提供。"""

import argparse
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from magi.harness.config import load_config
from magi.web.schemas import TurnInput
from magi.web.service import WebError, WebService


def main():
    """首次生成四轮，恢复模式在同一个库上继续第五轮。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.output.exists() or args.data.exists() != args.resume:
        parser.error("拒绝覆盖验收记录，恢复时必须使用已存在的数据目录")
    docs = args.data / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    settings = replace(
        load_config(Path("magi.qianwen.toml")),
        num_ctx=8192,
        docs_root=docs,
        db_path=args.data / "checkpoint.db",
        events_path=args.data / "events.jsonl",
        business_db_path=args.data / "business.db",
        embedding_enabled=False,
    )
    service = WebService(settings, "acceptance039")
    service.recover()
    questions = [
        "任务是准备 Python 面试。只用 Python 3.12，本周仅有4小时。协程取消实验尚未验证。请简短确认。",
        "复习先围绕基础，稍后再决定安排。请回复收到。",
        "先保留未决实验，不要给出任务计划。请回复收到。",
        "请只复述已知版本、时间限制、未验证的事项。资料不足的项请标明。",
        "重启后继续。请简短复述之前的版本、时长和未验证事项。",
    ]
    report = {"num_ctx": settings.num_ctx, "resumed": args.resume, "turns": []}
    if args.resume:
        report["restored_before_send"] = len(service.history("study"))
    for number in [5] if args.resume else range(1, 5):
        try:
            turn = service.turn(
                TurnInput(
                    chat_id="study",
                    message_id=f"u{number}",
                    reply_message_id=f"a{number}",
                    parent_reply_message_id=f"a{number - 1}" if number > 1 else None,
                    user_text=questions[number - 1]
                    + "\n以下只是容量测试填充，请忽略：\n"
                    + "x" * 760,
                ),
                "chat",
            )
            report["turns"].append({"status": turn["status"], "text": turn["text"]})
        except WebError as exc:
            report["turns"].append({"status": "FAILED", "code": exc.code})
            break
    path = settings.db_path.with_suffix(".summaries.db")
    if path.exists():
        with closing(sqlite3.connect(path)) as db:
            report["summaries"] = [
                {"covered": covered, "body": body}
                for covered, body in db.execute("SELECT covered,body FROM summaries")
            ]
    if settings.events_path.exists():
        report["summary_events"] = [
            event
            for line in settings.events_path.read_text(encoding="utf8").splitlines()
            if (event := json.loads(line))["type"].startswith("summary_")
        ]
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
    )


if __name__ == "__main__":
    main()
