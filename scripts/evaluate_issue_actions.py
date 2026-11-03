"""真实千问选择工具，GitHub 访问全部由隔离传输替身处理。"""

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path

import httpx

from magi.harness.config import load_config
from magi.web.issues import GitHubIssues, IssueActions
from magi.web.schemas import IssueDecision, TurnInput
from magi.web.service import WebService

REPO = "sandbox-owner/synthetic-issue-repo"


def main():
    """生成、模拟人工批准、查询回执各执行一次，保留观察结果。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.data.exists() or args.output.exists():
        parser.error("请为验收指定全新目录和报告")
    docs = args.data / "docs"
    docs.mkdir(parents=True)
    settings = replace(
        load_config(Path("magi.qianwen.toml")),
        docs_root=docs,
        db_path=args.data / "checkpoint.db",
        business_db_path=args.data / "business.db",
        events_path=args.data / "events.jsonl",
        embedding_enabled=False,
    )
    remote_calls = []

    def simulate(request):
        url = f"https://api.github.com/repos/{REPO}/issues"
        if request.method != "POST" or str(request.url) != url:
            raise AssertionError("Unexpected external action")
        remote_calls.append({"method": request.method, "url": url})
        return httpx.Response(
            201,
            json=json.loads(request.content)
            | {"repository_url": url.removesuffix("/issues"), "number": 17},
        )

    # 只给模拟传输使用，不读取或继承真实 GitHub 凭据。
    os.environ["MAGI_GITHUB_ISSUES_TOKEN"] = "isolated-mock-value"
    service = WebService(settings, "acceptance042")
    report = {"remote_calls": remote_calls}
    try:
        with httpx.Client(transport=httpx.MockTransport(simulate)) as github:
            service.issues = IssueActions(
                service.store, service.owner, repo=REPO, github=GitHubIssues(github)
            )
            first = service.turn(
                TurnInput(
                    chat_id="issue-check",
                    message_id="u1",
                    reply_message_id="a1",
                    issue_enabled=True,
                    user_text=(
                        "请准备一个 GitHub Issue 草稿，主题是核对后台任务的超时提示。"
                        "事实只有这次复现时页面仍显示执行中，原因未查明。"
                        "希望后续复现并记录发生时间。请先生成待审草稿。"
                    ),
                ),
                "chat",
            )
            report["draft_reply"] = first["text"]
            report["drafts"] = first["issues"]
            report["writes_before_approval"] = len(remote_calls)
            if first["issues"]:
                draft = first["issues"][0]
                report["approved"] = service.issues.decide(
                    draft["id"],
                    IssueDecision(
                        decision="approve", payload_hash=draft["payload_hash"]
                    ),
                )
                second = service.turn(
                    TurnInput(
                        chat_id="issue-check",
                        message_id="u2",
                        reply_message_id="a2",
                        parent_reply_message_id="a1",
                        issue_enabled=True,
                        user_text="我刚才在网页批准了。请核对前一轮草稿的实际回执，然后告诉我结果和编号。",
                    ),
                    "chat",
                )
                report["receipt_reply"] = second["text"]
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["error_code"] = getattr(exc, "code", None)
    if settings.events_path.exists():
        report["tools"] = [
            item["name"]
            for line in settings.events_path.read_text(encoding="utf8").splitlines()
            if (item := json.loads(line))["type"] == "tool_started"
        ]
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
    )


if __name__ == "__main__":
    main()
