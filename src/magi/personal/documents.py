"""确认后执行的本机任务工作单工具；不调用模型、不改变计划或记忆。"""

import hashlib
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from magi.personal.export import plan_markdown
from magi.personal.models import ErrorCode, PersonalError
from magi.personal.plans import PlanService


def checksum(content):
    """以实际UTF-8字节计算摘要，预览、确认和文件恢复使用相同算法。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def worksheet(plan):
    """保留精确版本全文，并添加可填写区域，不推测自由文本中的任务结构。"""
    warning = (
        "背景已变化，请先复核本计划。" if plan.needs_review else "请核对当前条件。"
    )
    return (
        "# MAGI 任务工作单\n\n"
        + warning
        + " 文档创建不代表计划任务已执行。\n\n"
        + "## 开始前检查\n\n- [ ] 核对目标、约束与所需材料\n"
        + "- [ ] 核对下方计划中的假设与待确认事项\n\n"
        + "## 执行依据（所选版本原文）\n\n"
        + plan_markdown(plan)
        + "\n## 执行记录（请按原计划逐项填写）\n\n"
        + "| 计划步骤 | 实际行动与结果 | 完成证据 | 状态 |\n"
        + "| --- | --- | --- | --- |\n| 待填写 | 待填写 | 待填写 | 未开始 |\n\n"
        + "## 完成检查\n\n- [ ] 逐项核对原计划的完成条件\n"
        + "- [ ] 记录尚未完成或需要调整的事项\n\n"
        + "## 反馈与下次修订\n\n实际遇到的变化：\n\n"
        + "保留的步骤：\n\n需要调整的步骤及原因：\n"
    )


class TaskDocuments:
    def __init__(self, store, owner):
        """固定输出根目录；调用方沿用WebService串行门和应用进程锁。"""
        self.store, self.owner = store, owner
        self.directory = store.path.parent / "task-documents"

    def _get(self, db, document_id):
        """记录只对创建它的owner可见，路径不能由请求提供。"""
        row = db.execute(
            "SELECT * FROM task_documents WHERE id=? AND owner=?",
            (document_id, self.owner),
        ).fetchone()
        if row is None:
            raise PersonalError(ErrorCode.NOT_FOUND, "任务文档不存在")
        return dict(row)

    def _view(self, row):
        """返回精确预览及实际输出位置，不把PREVIEW表述成创建成功。"""
        return {**row, "path": str(self.directory / (row["id"] + ".md"))}

    def get(self, document_id):
        """刷新或重启后按文档编号恢复原预览，不重新生成。"""
        with self.store.connect() as db:
            return self._view(self._get(db, document_id))

    def for_turn(self, turn_id):
        """从真实关联记录恢复工具产物摘要，不依赖模型是否声称准备成功。"""
        with self.store.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,plan_id,version,status FROM task_documents WHERE owner=? AND source_turn_id=? ORDER BY rowid",
                    (self.owner, turn_id),
                )
            ]

    def preview(self, plan_id, version, request_id, *, source_turn_id=None):
        """同请求绑定同版本；预览仅写业务记录，不创建输出目录或文件。"""
        with self.store.transaction() as db:
            if source_turn_id is not None:
                turn = db.execute(
                    "SELECT id FROM web_turns WHERE id=? AND owner=? AND mode='chat' AND status='RUNNING'",
                    (source_turn_id, self.owner),
                ).fetchone()
                if turn is None:
                    raise PersonalError(ErrorCode.NOT_FOUND, "没有可关联的当前聊天轮次")
            old = db.execute(
                "SELECT * FROM task_documents WHERE owner=? AND request_id=?",
                (self.owner, request_id),
            ).fetchone()
            if old:
                if (
                    old["plan_id"] != plan_id
                    or old["version"] != version
                    or old["source_turn_id"] != source_turn_id
                ):
                    raise PersonalError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "请求已绑定其他版本"
                    )
                return self._view(dict(old))
            plan = PlanService(self.store).get(plan_id, version)
            content = worksheet(plan)
            document_id = uuid4().hex
            db.execute(
                "INSERT INTO task_documents(id,owner,request_id,plan_id,version,content,payload_hash,status,source_turn_id) VALUES (?,?,?,?,?,?,?, 'PREVIEW',?)",
                (
                    document_id,
                    self.owner,
                    request_id,
                    plan_id,
                    version,
                    content,
                    checksum(content),
                    source_turn_id,
                ),
            )
            return self._view(self._get(db, document_id))

    def confirm(self, document_id, payload_hash):
        """核对确认摘要，原子发布完整文件；重试核对原文件后恢复提交状态。"""
        with self.store.transaction() as db:
            row = self._get(db, document_id)
            if row["payload_hash"] != payload_hash:
                raise PersonalError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "确认内容与预览不一致"
                )
            directory = self.directory
            if directory.is_symlink() or directory.resolve() != directory:
                raise PersonalError(ErrorCode.INVALID_INPUT, "输出目录不能是链接")
            directory.mkdir(exist_ok=True)
            path = directory / (row["id"] + ".md")
            content = row["content"].encode("utf-8")
            if path.is_symlink():
                raise PersonalError(ErrorCode.INVALID_INPUT, "输出文件不能是链接")
            if path.exists():
                if not path.is_file() or path.read_bytes() != content:
                    raise PersonalError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "文件已存在且内容不同，不覆盖"
                    )
            elif row["status"] == "CREATED":
                raise PersonalError(
                    ErrorCode.NOT_FOUND, "已创建的文件已移走，不自动重建"
                )
            else:
                # 临时文件写完并刷盘后发布，避免中断留下半份最终文件；link拒绝覆盖。
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(
                        dir=directory, delete=False
                    ) as handle:
                        temporary = Path(handle.name)
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.link(temporary, path)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            db.execute(
                "UPDATE task_documents SET status='CREATED' WHERE id=?", (document_id,)
            )
            return {**self._view({**row, "status": "CREATED"}), "verified": True}
