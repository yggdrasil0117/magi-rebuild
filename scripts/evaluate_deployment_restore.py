"""隔离部署、真实 HTTP 启动、数据恢复与真实千问续聊验收。"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from secrets import token_urlsafe
from unittest.mock import patch

import httpx
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from magi.deployment import backup, install
from magi.harness.config import load_config
from magi.harness.runtime import open_session
from magi.web.issues import GitHubIssues, IssueActions
from magi.web.schemas import IssueDecision, TurnInput
from magi.web.service import WebService
from magi.web.tasks import AgentTasks

REPO = "sandbox-owner/restore-fixture"
OWNER = "acceptance043"


class SeedModel(FakeMessagesListChatModel):
    """生成可核对的旧会话，不在准备数据时调用云端。"""

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        if messages[0].text.startswith("用中文压缩旧对话"):
            reply = AIMessage(
                content="历史：准备 Python 3.12 面试，协程取消实验尚未验证。"
            )
        elif messages[-1].text.startswith("准备 Issue"):
            reply = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "magi_prepare_issue",
                        "id": "seed-unknown"
                        if messages[-1].text.endswith("未知")
                        else "seed-verified",
                        "args": {
                            "title": messages[-1].text,
                            "body": "等待验证协程取消现象。",
                        },
                    }
                ],
            )
        else:
            reply = AIMessage(content="已保留准备面试及待验证事项。")
        return ChatResult(generations=[ChatGeneration(message=reply)])


def turn_input(number, text, *, issue=False, recall=False):
    return TurnInput(
        chat_id="study",
        message_id=f"u{number}",
        reply_message_id=f"a{number}",
        parent_reply_message_id=f"a{number - 1}" if number > 1 else None,
        user_text=text,
        issue_enabled=issue,
        memory_recall=recall,
    )


def seed(settings):
    """准备业务数据与四份数据库，外部写入只使用 MockTransport。"""
    service = WebService(settings, OWNER)
    service.memories.add(
        key="备考时间",
        scope="global",
        value="每周只能安排4小时备考",
        request_id="memory",
    )
    plan = service.plans.create(
        title="协程复习",
        body="验证 Python 3.12 的协程取消行为。",
        request_id="plan",
        source_epoch=service.memories.current_epoch(),
    )
    doc = service.document_action("preview", plan.object_id, 1, "worksheet")
    service.document_action("confirm", doc["id"], doc["payload_hash"])

    legacy = SeedModel(responses=[])
    with open_session(settings, "legacy-fixture", model=legacy) as session:
        session.ask("保留旧 checkpoint")

    def simulate(request):
        payload = json.loads(request.content)
        if payload["title"].endswith("未知"):
            raise httpx.ReadTimeout("simulated", request=request)
        return httpx.Response(
            201,
            json=payload
            | {
                "number": 21,
                "repository_url": f"https://api.github.com/repos/{REPO}",
            },
        )

    model = SeedModel(responses=[])
    with (
        httpx.Client(transport=httpx.MockTransport(simulate)) as github,
        patch("magi.harness.runtime.build_model", return_value=model),
        patch.dict(os.environ, {"MAGI_GITHUB_ISSUES_TOKEN": "isolated-mock-value"}),
    ):
        service.issues = IssueActions(
            service.store, OWNER, repo=REPO, github=GitHubIssues(github)
        )
        for number in range(1, 6):
            text = (
                "准备 Python 3.12 面试，协程取消实验尚未验证。"
                if number == 1
                else "继续原任务。"
            )
            service.turn(turn_input(number, text + "x" * 1800), "chat")
        actions = []
        for number, suffix in [(6, "已核对"), (7, "未知")]:
            draft = service.turn(
                turn_input(number, "准备 Issue " + suffix, issue=True), "chat"
            )["issues"][0]
            actions.append(
                service.issues.decide(
                    draft["id"],
                    IssueDecision(
                        decision="approve", payload_hash=draft["payload_hash"]
                    ),
                )
            )
        queue = AgentTasks(service)
        task = queue.submit(
            TurnInput(
                chat_id="queue-fixture",
                message_id="u1",
                reply_message_id="a1",
                user_text="登记一条已完成后台任务的历史。",
            ),
            "chat",
        )
        assert queue.run_one()
        assert queue.get(task["id"])["status"] == "COMPLETED"
    assert [action["status"] for action in actions] == ["VERIFIED", "UNKNOWN"]
    assert settings.db_path.with_suffix(".summaries.db").is_file()
    return doc, actions, task["id"]


@contextmanager
def live(config, *, log):
    """从独立目录启动实际模块入口，只对本机暴露随机端口。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = token_urlsafe(32)
    environment = {
        **os.environ,
        "MAGI_API_TOKEN": token,
        "MAGI_OWNER": OWNER,
        "MAGI_GITHUB_ISSUES_REPO": REPO,
    }
    environment.pop("MAGI_GITHUB_ISSUES_TOKEN", None)
    with (
        log.open("wb") as server_log,
        subprocess.Popen(
            [
                sys.executable,
                "-X",
                "utf8",
                "-m",
                "magi.web",
                "--config",
                str(config),
                "--port",
                str(port),
            ],
            cwd=config.parent,
            env=environment,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        ) as process,
        httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            trust_env=False,
            timeout=240,
            headers={"Authorization": "Bearer " + token},
        ) as client,
    ):
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    response = client.get("/health", timeout=1)
                    if response.status_code == 200:
                        yield client, response.json(), process.pid
                        return
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass
                time.sleep(0.2)
            raise RuntimeError("隔离服务未就绪")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)


def exercise(args, root, report):
    """验证运行中拒绝备份，停服后复制，再从新实例读取并续聊。"""
    template = replace(load_config(args.config), num_ctx=16384, embedding_enabled=False)
    source_config = install(root / "source", template)
    source = load_config(source_config)
    document, actions, task_id = seed(source)
    archive = root / "snapshot.zip"
    with live(source_config, log=root / "source.log") as (client, health, pid):
        report["source"] = {"health": health, "pid": pid}
        assert client.get("/v1/chats/study/turns").status_code == 200
        try:
            backup(source, archive)
        except RuntimeError:
            report["backup_denied_while_live"] = not archive.exists()
        else:
            raise AssertionError("未拒绝运行中备份")
    manifest = backup(source, archive)
    report["archive_files"] = sorted(manifest["files"])
    restored_config = install(root / "restored", template, archive)
    restored = load_config(restored_config)
    restored_doc = (
        restored.business_db_path.parent
        / "task-documents"
        / Path(document["path"]).name
    )
    report["worksheet_equal"] = (
        restored_doc.read_bytes() == Path(document["path"]).read_bytes()
    )
    report[
        "rebuildable_index_initially_absent"
    ] = not restored.business_db_path.with_suffix(".memory-index").exists()

    with live(restored_config, log=root / "restored.log") as (client, health, pid):
        report["restored"] = {"health": health, "pid": pid}
        report["history_before_continue"] = len(
            client.get("/v1/chats/study/turns").raise_for_status().json()
        )
        report["issue_statuses"] = [
            client.get(f"/v1/issues/{action['id']}").raise_for_status().json()["status"]
            for action in actions
        ]
        report["task_status"] = (
            client.get(f"/v1/tasks/{task_id}").raise_for_status().json()["status"]
        )
        response = client.post(
            "/v1/chat",
            json=turn_input(
                8,
                f"继续准备面试。请回看刚才的版本、每周可用时长和未验证事项，"
                f"再只读查动作 {actions[0]['id']} 的核对结果。不创建任何 Issue。",
                issue=True,
                recall=True,
            ).model_dump(),
        )
        report["continuation_http"] = response.status_code
        if response.status_code == 200:
            data = response.json()
            report["continuation"] = {
                "status": data["status"],
                "text": data["text"],
                "recall": (data.get("result") or {}).get("memory_recall"),
            }
        else:
            report["continuation_error"] = response.json().get("code")
    if restored.events_path.exists():
        report["tools"] = [
            event["name"]
            for line in restored.events_path.read_text(encoding="utf8").splitlines()
            if (event := json.loads(line))["type"] == "tool_started"
        ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("magi.qianwen.toml"))
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.data.resolve()
    if root.exists() or args.output.exists():
        parser.error("请为验收使用新目录与报告文件")
    root.mkdir(parents=True)
    report = {}
    try:
        exercise(args, root, report)
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["error_code"] = getattr(exc, "code", None)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8"
    )


if __name__ == "__main__":
    main()
