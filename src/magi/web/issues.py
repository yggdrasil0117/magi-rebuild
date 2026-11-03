"""GitHub Issue 草稿、人工批准和未知结果核对。"""

import os
import re
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from magi.web.service import WebError, digest


def now():
    return datetime.now(timezone.utc).isoformat()


class IssueDraft(BaseModel):
    """模型只能提供正文，目标与确认身份由服务端决定。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200, strict=True)
    body: str = Field(min_length=1, max_length=6000, strict=True)


class IssueReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str = Field(pattern=r"^[a-f0-9]{32}$", strict=True)


class GitHubIssues:
    """固定访问 GitHub REST，禁止重定向与自动重试。"""

    def __init__(self, client=None):
        self.client = client

    def request(self, method, repo, token, *, number=None, payload=None):
        """只用于创建 Issue 或按编号核对；不向调用方传播远端错误正文。"""
        base = f"https://api.github.com/repos/{repo}/issues"
        url = base if number is None else f"{base}/{number}"
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
        }
        if self.client:
            response = self.client.request(
                method, url, headers=headers, json=payload, follow_redirects=False
            )
        else:
            with httpx.Client(timeout=15, trust_env=False) as client:
                response = client.request(
                    method, url, headers=headers, json=payload, follow_redirects=False
                )
        if response.status_code != (201 if method == "POST" else 200):
            raise ValueError("GitHub 未返回预期状态")
        return response.json()


class IssueActions:
    def __init__(self, store, owner, *, repo=None, github=None):
        self.store = store
        self.owner = owner
        raw = (
            os.getenv("MAGI_GITHUB_ISSUES_REPO", "") if repo is None else repo
        ).strip()
        self.repo = (
            raw
            if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,38}/[a-zA-Z0-9_.-]{1,100}", raw)
            and raw.split("/")[1] not in {".", ".."}
            else None
        )
        self.github = github or GitHubIssues()

    def get(self, action_id, db=None):
        """读取当前用户的草稿或回执，链接由已核对的仓库和编号构造。"""
        if db is None:
            with self.store.connect() as connection:
                return self.get(action_id, connection)
        row = db.execute(
            "SELECT * FROM issue_actions WHERE id=? AND owner=?",
            (action_id, self.owner),
        ).fetchone()
        if row is None:
            raise WebError("NOT_FOUND", "未找到 Issue 草稿", 404)
        result = dict(row)
        result.pop("owner")
        result["outbound_body"] = self.payload(result)["body"]
        number = result["issue_number"]
        result["url"] = (
            f"https://github.com/{result['repo']}/issues/{number}" if number else None
        )
        return result

    def for_turn(self, turn_id):
        """只查询轮次关联的业务产物，模型回答失败后也可读取。"""
        with self.store.connect() as db:
            row = db.execute(
                "SELECT id FROM issue_actions WHERE turn_id=? AND owner=?",
                (turn_id, self.owner),
            ).fetchone()
            return [self.get(row["id"], db)] if row else []

    @staticmethod
    def payload(action):
        """发布标记与草稿身份绑定，后续可根据它核对不确定结果。"""
        return {
            "title": action["title"],
            "body": action["body"] + f"\n\n<!-- magi-action:{action['id']} -->",
        }

    def draft(self, turn_id, title, body):
        """准备一个精确预览。同轮重复相同参数复用原记录。"""
        if not self.repo:
            raise WebError("ISSUE_DISABLED", "服务未配置有效的 Issue 目标仓库", 422)
        args = IssueDraft(title=title, body=body)
        if (
            "magi-action:" in args.body.lower()
            or "\n" in args.title
            or "\r" in args.title
        ):
            raise WebError(
                "INVALID_ISSUE_DRAFT", "标题需为单行，正文不能含系统核对标记", 422
            )
        with self.store.transaction() as db:
            turn = db.execute(
                "SELECT epoch FROM web_turns WHERE id=? AND owner=? AND mode='chat' AND status='RUNNING'",
                (turn_id, self.owner),
            ).fetchone()
            if turn is None:
                raise WebError("TURN_NOT_RUNNING", "只能在本轮运行中准备草稿")
            old = db.execute(
                "SELECT id,repo,title,body FROM issue_actions WHERE turn_id=? AND owner=?",
                (turn_id, self.owner),
            ).fetchone()
            if old:
                if (old["repo"], old["title"], old["body"]) != (
                    self.repo,
                    args.title,
                    args.body,
                ):
                    raise WebError(
                        "ISSUE_DRAFT_CONFLICT", "同轮已有不同草稿，请新发消息"
                    )
                return self.get(old["id"], db)
            action_id = uuid4().hex
            signature = digest(
                [action_id, self.repo, args.title, args.body, turn["epoch"]]
            )
            db.execute(
                """INSERT INTO issue_actions
                   (id,owner,turn_id,repo,title,body,payload_hash,epoch,status,created_at)
                   VALUES (?,?,?,?,?,?,?,?,'DRAFT',?)""",
                (
                    action_id,
                    self.owner,
                    turn_id,
                    self.repo,
                    args.title,
                    args.body,
                    signature,
                    turn["epoch"],
                    now(),
                ),
            )
            return self.get(action_id, db)

    def token(self, action):
        """批准与核对仅能访问当前配置的同一仓库。"""
        token = os.getenv("MAGI_GITHUB_ISSUES_TOKEN", "").strip()
        if self.repo != action["repo"] or not token:
            raise WebError(
                "ISSUE_PROVIDER_UNAVAILABLE", "请核对服务的目标仓库和 Issue 凭据", 409
            )
        return token

    @staticmethod
    def checked_number(action, result, expected=None):
        """核对仓库、正文、标题和标记，排除 PR 与无关 Issue。"""
        if not isinstance(result, dict):
            raise ValueError("无有效回执")
        number = result.get("number")
        repo_url = result.get("repository_url")
        payload = IssueActions.payload(action)
        if (
            type(number) is not int
            or not 1 <= number <= 2147483647
            or (expected is not None and number != expected)
            or not isinstance(repo_url, str)
            or repo_url.lower()
            != f"https://api.github.com/repos/{action['repo']}".lower()
            or "pull_request" in result
            or result.get("title") != payload["title"]
            or result.get("body") != payload["body"]
        ):
            raise ValueError("远端回执与已批准内容不一致")
        return number

    def decide(self, action_id, command):
        """人工批准先持久化发送状态，出事务后最多发一次 POST。"""
        with self.store.transaction() as db:
            action = self.get(action_id, db)
            if action["payload_hash"] != command.payload_hash:
                raise WebError("PAYLOAD_MISMATCH", "请重新加载并核对草稿")
            status = action["status"]
            if command.decision == "reject":
                if status not in {"DRAFT", "REJECTED"}:
                    raise WebError(
                        "ISSUE_DECISION_CONFLICT", "已发送或已开始发送，不能撤销草稿"
                    )
                db.execute(
                    "UPDATE issue_actions SET status='REJECTED' WHERE id=?",
                    (action_id,),
                )
                return self.get(action_id, db)
            if status == "REJECTED":
                raise WebError("ISSUE_DECISION_CONFLICT", "草稿已拒绝")
            if status != "DRAFT":
                return action
            epoch = db.execute(
                "SELECT context_epoch FROM app_state WHERE singleton=1"
            ).fetchone()[0]
            if epoch != action["epoch"]:
                raise WebError("CONTEXT_CHANGED", "背景已变化，请重新生成并核对草稿")
            token = self.token(action)
            db.execute(
                "UPDATE issue_actions SET status='SENDING',approved_at=? WHERE id=?",
                (now(), action_id),
            )
        try:
            result = self.github.request(
                "POST", action["repo"], token, payload=self.payload(action)
            )
            number = self.checked_number(action, result)
        except Exception:
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE issue_actions SET status='UNKNOWN' WHERE id=? AND status='SENDING'",
                    (action_id,),
                )
                return self.get(action_id, db)
        return self.verify(action_id, number, "SENDING")

    def verify(self, action_id, number, prior):
        """持久化核对结果，迟到的核对请求不能改写已确认回执。"""
        with self.store.transaction() as db:
            db.execute(
                """UPDATE issue_actions SET status='VERIFIED',issue_number=?,verified_at=?
                   WHERE id=? AND owner=? AND status=?""",
                (number, now(), action_id, self.owner, prior),
            )
            action = self.get(action_id, db)
            if action["issue_number"] != number:
                raise WebError("ISSUE_RECEIPT_CONFLICT", "该草稿已有不同结果")
            return action

    def reconcile(self, action_id, number):
        """未知结果只读核对用户提供的编号，不再次创建。"""
        action = self.get(action_id)
        if action["status"] == "VERIFIED" and action["issue_number"] == number:
            return action
        if action["status"] != "UNKNOWN":
            raise WebError("ISSUE_RECONCILE_CONFLICT", "仅可核对结果未知的草稿")
        token = self.token(action)
        try:
            result = self.github.request("GET", action["repo"], token, number=number)
            self.checked_number(action, result, number)
        except Exception:
            raise WebError(
                "ISSUE_NOT_VERIFIED", "未核对到匹配的 Issue，状态仍未知", 409
            ) from None
        return self.verify(action_id, number, "UNKNOWN")

    def recover(self):
        """重启时不能假定旧发送未发生，保留待核对状态。"""
        with self.store.transaction() as db:
            db.execute(
                "UPDATE issue_actions SET status='UNKNOWN' WHERE owner=? AND status='SENDING'",
                (self.owner,),
            )

    def tools(self, turn_id):
        """模型入口只注册草稿和只读回执工具。"""

        def magi_prepare_issue(title: str, body: str) -> dict:
            """仅当用户明确要求准备 Issue 时创建待确认草稿。"""
            action = self.draft(turn_id, title, body)
            return {
                "action_id": action["id"],
                "repo": action["repo"],
                "status": action["status"],
                "next_step": "请用户在网页核对并批准。此工具未发出 GitHub 写入请求。",
            }

        def magi_get_issue_receipt(action_id: str) -> dict:
            """查看已有 Issue 回执；只有 VERIFIED 表示已核对。"""
            action = self.get(action_id)
            return {
                key: action[key]
                for key in ("id", "repo", "status", "issue_number", "url")
            }

        return [
            StructuredTool.from_function(magi_prepare_issue, args_schema=IssueDraft),
            StructuredTool.from_function(
                magi_get_issue_receipt, args_schema=IssueReceipt
            ),
        ]
