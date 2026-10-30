"""固定两例真实模型验收：是否选择预览工具，及普通聊天不产生文档。"""

import argparse
import json
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage

from magi.harness.config import load_config
from magi.harness.runtime import open_personal_session
from magi.web.schemas import TurnInput
from magi.web.service import WebService


def main():
    """每例独立临时库、不重试、不确认，保留所有成功与失败事实。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = load_config(Path("magi.qianwen.toml"))
    cases = [
        (
            "prepare",
            "请为已保存的“后端接口练习”计划准备任务工作单预览，版本使用当前已保存版本。现在不要创建文件。",
        ),
        ("ordinary", "你好，简单打个招呼即可，不查询资料、不调用工具。"),
    ]
    report = {
        "mode": "real model; independent temporary databases; no retries or confirmations",
        "model": base.model,
        "cases": [],
    }
    with tempfile.TemporaryDirectory(prefix="magi-document-calling-") as temporary:
        for name, text in cases:
            root = Path(temporary) / name
            settings = replace(
                base,
                business_db_path=root / "business.db",
                db_path=root / "checkpoint.db",
                events_path=root / "events.jsonl",
            )
            service = WebService(settings, "cloud-validation")
            service.plans.create(
                title="后端接口练习",
                body="完成一个查询接口，记录验证结果。",
                request_id="seed",
                source_epoch=0,
            )
            item = {"case": name, "input": text}
            started = time.monotonic()
            try:
                result = service.turn(
                    TurnInput(
                        chat_id=name,
                        message_id="u1",
                        reply_message_id="a1",
                        user_text=text,
                    ),
                    "chat",
                )
                item.update(
                    status=result["status"],
                    answer=result["text"],
                    documents=result["documents"],
                )
            except Exception as exc:
                # 只保留错误类型与安全错误码，不输出可能含网络细节的异常正文。
                item.update(
                    status="FAILED",
                    error_type=type(exc).__name__,
                    error_code=getattr(exc, "code", None),
                )
            item["seconds"] = round(time.monotonic() - started, 3)
            with open_personal_session(settings, service.logical_id(name)) as session:
                messages = session.messages()
            ai = [m for m in messages if isinstance(m, AIMessage)]
            item["model_calls_recorded"] = len(ai)
            item["tool_calls"] = [
                {"name": c["name"], "args": c["args"]} for m in ai for c in m.tool_calls
            ]
            item["tool_results"] = [
                {"name": m.name, "content": m.content}
                for m in messages
                if isinstance(m, ToolMessage)
            ]
            item["known_usage"] = [m.usage_metadata for m in ai if m.usage_metadata]
            with service.store.connect() as db:
                item["preview_count"] = db.execute(
                    "SELECT COUNT(*) FROM task_documents"
                ).fetchone()[0]
                item["context_epoch"] = db.execute(
                    "SELECT context_epoch FROM app_state"
                ).fetchone()[0]
            item["created_file_count"] = len(
                list(service.documents.directory.glob("*.md"))
            )
            item["branch_matches"] = (
                item["status"] == "COMPLETED"
                and item["preview_count"] == (1 if name == "prepare" else 0)
                and item["created_file_count"] == 0
            )
            report["cases"].append(item)
            # 每例完成立即保留证据，中断也不丢失已经执行的尝试。
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    print(
        json.dumps(
            {
                "cases": len(report["cases"]),
                "matched": sum(r["branch_matches"] for r in report["cases"]),
            }
        )
    )


if __name__ == "__main__":
    main()
