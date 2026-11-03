"""读取联网验收独立库供浏览器核对；凭据从环境提供，不预置用户业务数据。"""

import argparse
import os
from dataclasses import replace
from pathlib import Path

import uvicorn

from magi.harness.config import load_config
from magi.web.app import create_app


def main():
    """data-dir与evaluate_web_research一致；GET不会生成或重新搜索。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8894)
    args = parser.parse_args()
    root = args.data_dir.resolve()
    if not (root / "business.db").is_file():
        raise ValueError("先运行联网验收生成独立数据库")
    settings = replace(
        load_config(Path("magi.qianwen.toml")),
        docs_root=root / "docs",
        business_db_path=root / "business.db",
        db_path=root / "checkpoint.db",
        events_path=root / "events.jsonl",
        embedding_enabled=False,
    )
    app = create_app(
        settings, token=os.environ.get("MAGI_API_TOKEN", ""), owner="web-validation"
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
