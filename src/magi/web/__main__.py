import argparse
import os
from pathlib import Path

import uvicorn

from magi.harness.config import load_config
from magi.web.app import create_app


def main():
    """指定独立配置与本机端口，应用仍以单执行器运行。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.getenv("MAGI_CONFIG", "magi.qianwen.toml")),
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口应介于 1 和 65535")
    app = create_app(load_config(args.config))
    uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
