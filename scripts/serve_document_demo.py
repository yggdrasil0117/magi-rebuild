"""独立浏览器验收：脚本模型 + 真实Agent图、工具、SQLite及文件系统。"""

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


class DemoDocumentModel(BaseChatModel):
    """固定执行列计划→准备预览→回复，不评估自然语言理解或调用真实模型。"""

    @property
    def _llm_type(self):
        """标明验收替身身份，不冒充云端模型。"""
        return "offline-document-demo"

    def bind_tools(self, tools, **kwargs):
        """保留真实工具注册路径；此固定替身不根据描述自行选择工具。"""
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """只根据本轮最近真实工具结果推进固定演示流程。"""
        last = messages[-1]
        if last.type != "tool":
            result = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "magi_list_plans",
                        "args": {},
                        "id": "demo-list",
                    }
                ],
            )
        elif last.name == "magi_list_plans":
            plan = json.loads(last.content)[0]
            result = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "magi_prepare_task_document",
                        "args": {"plan_id": plan["id"], "version": plan["version"]},
                        "id": "demo-prepare",
                    }
                ],
            )
        else:
            result = AIMessage(
                content="工作单预览已准备，尚未创建文件。请点击下方按钮查看，再由你确认。"
            )
        return ChatResult(generations=[ChatGeneration(message=result)])


def main():
    """仅启动独立临时数据服务，模拟模型不会访问云端或启动Ollama。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8880)
    args = parser.parse_args()
    settings = HarnessConfig(
        docs_root=Path("docs").resolve(),
        db_path=args.data_dir / "checkpoint.db",
        events_path=args.data_dir / "events.jsonl",
        business_db_path=args.data_dir / "business.db",
    )
    runtime.build_model = lambda settings: DemoDocumentModel()
    service = WebService(settings, "document-demo")
    if not service.plans.list_current():
        service.plans.create(
            title="后端接口练习",
            body="完成一个查询接口，记录验证结果。",
            request_id="demo-seed",
            source_epoch=0,
        )
    uvicorn.run(
        create_app(settings, service=service, token=os.environ["MAGI_API_TOKEN"]),
        host="127.0.0.1",
        port=args.port,
    )


if __name__ == "__main__":
    main()
