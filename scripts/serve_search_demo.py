"""独立浏览器搜索验收：脚本模型使用真实Agent图和资料搜索工具。"""

import argparse
import json
import os
from pathlib import Path

import uvicorn
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

import magi.harness.runtime as runtime
from magi.harness.config import HarnessConfig
from magi.web.app import create_app
from magi.web.service import WebService


class DemoSearchModel(BaseChatModel):
    """固定调用关键词检索，仅用于页面验收，不测试语言理解能力。"""

    @property
    def _llm_type(self):
        """标明脚本替身，不冒充真实模型。"""
        return "offline-search-demo"

    def bind_tools(self, tools, **kwargs):
        """使用真实框架的工具注册，固定替身不根据描述自主选工具。"""
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """收到真实工具结果后才引用其中的原文、路径和行号。"""
        if messages[-1].type != "tool":
            result = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "magi_search_files",
                        "args": {"query": "计划导出", "path": "."},
                        "id": "demo-search",
                    }
                ],
            )
        else:
            found = json.loads(messages[-1].content)
            hit = found["matches"][0]
            result = AIMessage(
                content=f"根据授权资料 {hit['path']} 第{hit['line']}行：{hit['quote']}\n本次检索未修改计划、记忆或文件。"
            )
        return ChatResult(generations=[ChatGeneration(message=result)])


def main():
    """只创建独立合成资料与测试库，不读取真实个人资料或调用云端模型。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8881)
    args = parser.parse_args()
    docs = args.data_dir / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "guide.md").write_text(
        "# 演示资料\n计划导出只生成文件，不代表任务已完成。\n", encoding="utf-8"
    )
    settings = HarnessConfig(
        docs_root=args.data_dir / "docs",
        db_path=args.data_dir / "checkpoint.db",
        events_path=args.data_dir / "events.jsonl",
        business_db_path=args.data_dir / "business.db",
    )
    runtime.build_model = lambda settings: DemoSearchModel()
    service = WebService(settings, "search-demo")
    uvicorn.run(
        create_app(settings, service=service, token=os.environ["MAGI_API_TOKEN"]),
        host="127.0.0.1",
        port=args.port,
    )


if __name__ == "__main__":
    main()
