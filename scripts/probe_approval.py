"""独立成熟审批验收入口：每次命令是一个进程，固定脚本模型，无云端调用。"""

import argparse
import json
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from magi.harness.approval_trial import (
    TOOL_NAME,
    ApprovalError,
    open_document_approval_trial,
)
from magi.harness.config import HarnessConfig
from magi.personal.documents import TaskDocuments
from magi.personal.models import PersonalError
from magi.personal.plans import PlanService
from magi.personal.store import PersonalStore


class ApprovalModel(BaseChatModel):
    """固定提出一次创建请求，工具返回后结束，不模拟真实语言理解。"""

    document_id: str
    payload_hash: str

    @property
    def _llm_type(self):
        """标明离线审批试验模型。"""
        return "offline-approval-trial"

    def bind_tools(self, tools, **kwargs):
        """真实工具注册由框架完成，脚本仅返回固定调用。"""
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """不根据用户文字批准操作，审批输入来自独立宿主命令。"""
        if messages[-1].type == "tool":
            response = AIMessage(content="审批流程已处理；文件创建事实请核对业务记录。")
        else:
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": TOOL_NAME,
                        "args": {
                            "document_id": self.document_id,
                            "payload_hash": self.payload_hash,
                        },
                        "id": "approval-tool-call",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=response)])


def trial_settings(root):
    """构造独立试验库路径，不引用生产配置、密钥或真实用户数据库。"""
    root.mkdir(parents=True, exist_ok=True)
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    return HarnessConfig(
        docs_root=docs,
        db_path=root / "approval-checkpoint.db",
        business_db_path=root / "approval-business.db",
        events_path=root / "approval-events.jsonl",
        num_ctx=32768,
    )


def main():
    """init只准备预览；start运行至暂停；approve/reject需提供实际中断ID和摘要。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["init", "start", "status", "approve", "reject"]
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--document-id")
    parser.add_argument("--interrupt-id")
    parser.add_argument("--payload-hash")
    args = parser.parse_args()
    settings = trial_settings(args.data_dir)
    store = PersonalStore(settings.business_db_path)
    documents = TaskDocuments(store, "approval-demo")
    if args.action == "init":
        receipt = PlanService(store).create(
            title="审批演示",
            body="完成接口练习并记录验证。",
            request_id="seed-plan",
            source_epoch=0,
        )
        preview = documents.preview(receipt.object_id, 1, "seed-preview")
        print(json.dumps({k: preview[k] for k in ["id", "payload_hash", "status"]}))
        return
    if not args.document_id:
        parser.error("此操作需要--document-id")
    saved = documents.get(args.document_id)
    model = ApprovalModel(document_id=saved["id"], payload_hash=saved["payload_hash"])
    with open_document_approval_trial(
        settings, "approval-demo", saved["id"], model=model
    ) as trial:
        if args.action in {"approve", "reject"}:
            if not args.interrupt_id or not args.payload_hash:
                parser.error("恢复审批需要--interrupt-id和--payload-hash")
            result = trial.decide(args.interrupt_id, args.action, args.payload_hash)
        else:
            result = trial.start() if args.action == "start" else trial.status()
        print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (ApprovalError, PersonalError) as exc:
        print(json.dumps({"error_code": exc.code, "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
