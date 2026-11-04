"""隔离数据运行六个真实研究任务；凭据只从环境读取，不写入报告。"""

import argparse
import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from magi.harness.config import load_config
from magi.web.schemas import ResearchExecution, TurnInput
from magi.web.service import WebService
from magi.web.tasks import AgentTasks


def prepare_documents(root: Path):
    """固定合成资料同时包含冲突、未知和已完成事项，便于人工内容复核。"""

    root.mkdir(parents=True, exist_ok=True)
    (root / "project-memory.md").write_text(
        """# 项目记忆现状
MAGI 已有 SQLite 已确认记忆、会话历史和可重建的向量索引。
本机单用户、单 worker。当前没有人格私有长期记忆，也没有多人权限模型。
要求比较 SQLite 全文检索、Qdrant 稠密向量和二者混合三种方案。
不要把候选记忆当成已确认事实；不能安装依赖或修改项目代码。
恢复要求：业务库是事实源，索引允许重建。
""",
        encoding="utf-8",
    )
    (root / "job-material.md").write_text(
        """# 岗位材料
目标岗位：后端与 Agent 工程。已经完成：Python API、SQLite 迁移、幂等回执和工具白名单测试。
尚未验证：多人并发和生产负载。材料中一处旧说明称项目仍无 API，这与当前记录冲突。
交付时应保留冲突并说明核对依据，不能虚构性能提升。
""",
        encoding="utf-8",
    )
    (root / "personal-notes.md").write_text(
        """# 个人笔记
用户偏好先完成最小闭环，再扩展视觉效果。每周可投入时间没有确认。
已经阅读模块044，不要再次安排“先阅读模块044”作为新工作。
笔记同时写过“优先本地模型”和“云端模型已经接入且故障时不要自动启动本地模型”，后者日期更新。
""",
        encoding="utf-8",
    )


def task_input(chat, message, text):
    return TurnInput(
        chat_id=chat,
        message_id=message,
        reply_message_id="reply-" + message,
        user_text=text,
    )


def run_case(queue, request, execution):
    task = queue.submit(request, "research", execution)
    if not queue.run_one():
        raise RuntimeError("固定评测任务未被执行器领取")
    return queue.get(task["id"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("magi.qianwen.toml"))
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = load_config(args.config)
    if not os.getenv(settings.api_key_env, "").strip():
        parser.error(f"缺少环境变量 {settings.api_key_env}；尚未调用模型")
    data = args.data_dir.resolve()
    if data.exists() and any(data.iterdir()):
        parser.error("data-dir 必须不存在或为空，避免覆盖已有评测")
    prepare_documents(data / "docs")
    settings = replace(
        settings,
        docs_root=data / "docs",
        business_db_path=data / "business.sqlite3",
        db_path=data / "checkpoint.sqlite3",
        events_path=data / "events.jsonl",
        embedding_enabled=False,
        num_predict=max(settings.num_predict, 1800),
    )
    service = WebService(settings, "research-evaluation")
    queue = AgentTasks(service)
    common = (
        "必须实际使用只读文件工具读取指定资料，再交付带证据 ID 的报告。"
        "区分已实现、未实现和未知；不修改文件，不安装依赖。"
    )
    cases = []
    for agent in ("melchior", "balthasar", "casper"):
        cases.append(
            (
                f"memory-{agent}",
                common
                + "读取 project-memory.md，比较三种适合本机单用户 MAGI 的记忆方案。",
                ResearchExecution(lead_agent_id=agent, entry_mode="persona"),
            )
        )
    cases.extend(
        [
            (
                "job-balthasar",
                common + "读取 job-material.md，整理一份可用于求职材料复核的报告。",
                ResearchExecution(lead_agent_id="balthasar", entry_mode="persona"),
            ),
            (
                "notes-casper",
                common + "读取 personal-notes.md，整理个人行动约束并保留冲突与未知。",
                ResearchExecution(lead_agent_id="casper", entry_mode="persona"),
            ),
            (
                "memory-collaboration",
                common
                + "读取 project-memory.md，比较三种方案；请另外两个人格独立评审，必要时只修改一次。",
                ResearchExecution(
                    lead_agent_id="melchior",
                    reviewer_ids=["balthasar", "casper"],
                    entry_mode="room",
                ),
            ),
        ]
    )
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model": settings.model,
        "provider": settings.provider,
        "attempts": [],
    }
    for index, (name, text, execution) in enumerate(cases, 1):
        task = run_case(queue, task_input(name, f"case-{index}", text), execution)
        artifact = (
            service.research.artifacts.get(task["artifact_id"])
            if task["artifact_id"]
            else None
        )
        with service.store.connect() as db:
            runs = [
                dict(row)
                for row in db.execute(
                    """SELECT id,agent_id,stage,review_round,status,model_calls,tool_calls
                       FROM agent_runs WHERE task_id=? ORDER BY created_at,id""",
                    (task["id"],),
                )
            ]
        report["attempts"].append(
            {
                "case": name,
                "task": task,
                "runs": runs,
                "artifact": artifact,
                "manual_review": {
                    "supported_claims": None,
                    "unsupported_claims": None,
                    "missed_requirements": None,
                    "repeated_completed_work": None,
                    "notes": "待人工逐条填写；程序完成不等于内容正确。",
                },
            }
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"完成 {len(report['attempts'])} 个研究任务；请人工复核输出中的报告与评审。")


if __name__ == "__main__":
    main()
