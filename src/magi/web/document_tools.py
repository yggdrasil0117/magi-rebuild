"""当前Web聊天轮次获得的预览工具，不暴露文件确认或创建能力。"""

import hashlib
import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field


class DocumentArguments(BaseModel):
    """模型只能指定计划与精确版本，身份、路径和确认不属于模型参数。"""

    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(min_length=1, max_length=120, strict=True)
    version: int = Field(ge=1, strict=True)


def build_document_tool(documents, turn_id):
    """服务端闭包绑定owner与轮次；同轮同版本使用固定请求编号去重。"""

    def magi_prepare_task_document(plan_id: str, version: int) -> str:
        """只准备可恢复的工作单预览，返回简短真实元数据，不创建最终文件。"""
        signature = hashlib.sha256(json.dumps([plan_id, version]).encode()).hexdigest()
        result = documents.preview(
            plan_id,
            version,
            "tool:" + turn_id + ":" + signature,
            source_turn_id=turn_id,
        )
        return json.dumps(
            {
                "document_id": result["id"],
                "plan_id": plan_id,
                "version": version,
                "status": result["status"],
                "file_created_by_this_call": False,
                "next_step": "预览已准备。请用户点击回复下方查看按钮，核对后在页面确认创建；本工具没有创建文件。",
            },
            ensure_ascii=False,
        )

    return StructuredTool.from_function(
        magi_prepare_task_document,
        description=(
            "仅当用户要求准备任务工作单时，针对已保存计划的精确版本生成待确认预览。"
            "不知道编号或版本时先用计划查询工具；多个匹配计划时先澄清。"
            "只存预览，不创建文件，不执行计划，不接受确认、路径或自定义正文。"
        ),
        args_schema=DocumentArguments,
    )
