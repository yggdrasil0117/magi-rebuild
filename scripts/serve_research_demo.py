"""用可控模型替身启动隔离研究页面，仅用于浏览器与恢复验收。"""

import argparse
from dataclasses import replace
from pathlib import Path

import uvicorn

from magi.harness.config import load_config
from magi.research.models import (
    FindingHandling,
    ReportPayload,
    ReviewCheck,
    ReviewFindingInput,
    ReviewPayload,
    RevisionPayload,
)
from magi.web.app import create_app
from magi.web.service import WebService


def checks():
    return [
        ReviewCheck(name=name, status="pass", reason="可控验收已检查")
        for name in (
            "requirements",
            "evidence",
            "completed_work",
            "unknowns",
            "permissions",
        )
    ]


class DemoRunner:
    """保留真实状态机和数据库，只替换外部模型，便于稳定检查页面分支。"""

    def generate(self, run_id, agent_id, context, allowance, **kwargs):
        return (
            ReportPayload(
                title="本机记忆方案比较",
                body=(
                    "# 建议\n\n当前先保留 SQLite 作为事实源，向量索引继续允许重建。"
                    "\n\n## 权限边界\n\n本报告没有安装依赖或修改项目代码。"
                ),
                completed_items=["比较三类方案", "记录现有能力"],
                unknowns=["长期数据量与维护成本尚未实测"],
            ),
            [],
            [],
            1,
            0,
        )

    def review(self, run_id, agent_id, context, allowance, *, stage, **kwargs):
        if stage == "review" and agent_id == "balthasar":
            result = ReviewPayload(
                verdict="REVISE",
                checks=checks(),
                findings=[
                    ReviewFindingInput(
                        target="维护负担",
                        issue_type="UNKNOWN_NOT_DISCLOSED",
                        blocking=True,
                        reason="需要更醒目地说明尚未实测",
                        evidence_ids=[],
                        suggestion="在结论中保留未知项",
                    )
                ],
            )
        else:
            result = ReviewPayload(verdict="PASS", checks=checks(), findings=[])
        return result, [], [], 1, 0

    def revise(self, run_id, agent_id, context, allowance, **kwargs):
        finding_id = context["findings"][0]["finding_id"]
        return (
            RevisionPayload(
                report=ReportPayload(
                    title="本机记忆方案比较（评审后）",
                    body=(
                        "# 建议\n\n当前先保留 SQLite 作为事实源，向量索引允许重建。"
                        "\n\n## 未核实\n\n长期数据量、恢复耗时和维护成本尚未实测，"
                        "因此不把混合方案表述为已验证的最优方案。"
                    ),
                    completed_items=["比较三类方案", "根据评审缩小结论"],
                    unknowns=["长期数据量、恢复耗时和维护成本"],
                ),
                handling=[
                    FindingHandling(
                        finding_id=finding_id,
                        action="accepted",
                        reason="将维护负担明确列为未知",
                        location="未核实",
                    )
                ],
            ),
            [],
            [],
            1,
            0,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("magi.qianwen.toml"))
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    data = args.data_dir.resolve()
    data.mkdir(parents=True, exist_ok=True)
    docs = data / "docs"
    docs.mkdir(exist_ok=True)
    settings = replace(
        load_config(args.config),
        docs_root=docs,
        business_db_path=data / "business.sqlite3",
        db_path=data / "checkpoint.sqlite3",
        events_path=data / "events.jsonl",
        embedding_enabled=False,
    )
    service = WebService(settings, "browser-research")
    service.research.runner = DemoRunner()
    uvicorn.run(
        create_app(settings, token=args.token, service=service),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
