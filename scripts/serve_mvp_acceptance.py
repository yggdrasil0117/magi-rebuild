"""当前MVP真实验收服务：独立数据、真实配置，无预置计划或模型替身。"""

import argparse
import os
from dataclasses import replace
from pathlib import Path

import uvicorn

from magi.harness.config import load_config
from magi.web.app import create_app


def main():
    """只替换数据路径；凭据由环境传入，模型故障不回退或自动重试。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8883)
    parser.add_argument("--config", type=Path, default=Path("magi.qianwen.toml"))
    args = parser.parse_args()
    settings = load_config(args.config)
    if settings.provider != "qianwen":
        raise ValueError("本验收入口仅使用已配置的千问，不自动选择本地模型")
    if not os.getenv(settings.api_key_env):
        raise ValueError("请通过环境变量提供模型凭据，不写入验收配置")
    root = args.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    settings = replace(
        settings,
        business_db_path=root / "business.db",
        db_path=root / "checkpoint.db",
        events_path=root / "events.jsonl",
    )
    app = create_app(
        settings, token=os.environ.get("MAGI_API_TOKEN", ""), owner="mvp-acceptance"
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
