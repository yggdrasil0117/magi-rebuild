"""真实千问与Exa MCP单轮验收；全新合成数据，逐步留证，不重试生成。"""

import argparse
import json
from dataclasses import replace
from pathlib import Path

from magi.harness.config import load_config
from magi.web.schemas import TurnInput
from magi.web.service import WebService


def main():
    """目录和报告由命令行指定；恢复模式只读取同库，不调用模型或工具。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    if args.output.exists() or (args.data_dir.exists() and not args.restore):
        raise ValueError("拒绝覆盖旧尝试")
    docs = args.data_dir / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    settings = replace(
        load_config(Path("magi.qianwen.toml")),
        docs_root=docs,
        business_db_path=args.data_dir / "business.db",
        db_path=args.data_dir / "checkpoint.db",
        events_path=args.data_dir / "events.jsonl",
        embedding_enabled=False,
        num_predict=1400,
    )
    service = WebService(settings, "web-validation")
    report = {"mode": "real Qianwen and Exa MCP; no retries; synthetic request"}
    if args.restore:
        service.recover()
        report["history"] = service.history("webcheck")
    else:
        try:
            report["turn"] = service.turn(
                TurnInput(
                    chat_id="webcheck",
                    message_id="u1",
                    reply_message_id="a1",
                    web_enabled=True,
                    user_text="请联网查Python 3.12官方文档里asyncio.TaskGroup如何处理子任务异常。先搜索，然后读取相关官方网页，简要说明并给出来源链接；只讨论3.12版本，失败就说明，不写入计划或记忆。",
                ),
                "chat",
            )
        except Exception as exc:
            report["error_type"] = type(exc).__name__
        report["history"] = service.history("webcheck")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": report.get("turn", {}).get("status"),
                "history": len(report["history"]),
            }
        )
    )


if __name__ == "__main__":
    main()
