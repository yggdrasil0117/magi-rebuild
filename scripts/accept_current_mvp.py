"""当前产品的真实HTTP整体验收：合成资料、两轮千问、BGE-M3、保存与进程重启。

凭据仅由环境提供；自动确认仅适用于脚本新建的合成验收计划，不操作用户数据库。
"""

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def run(root, output):
    """root/output必须不存在；每步立即留证，任何失败停止，不重试生成或覆盖报告。"""
    if root.exists() or output.exists():
        raise ValueError("验收数据目录和报告必须是新路径")
    if not all(os.getenv(k) for k in ("DASHSCOPE_API_KEY", "SILICONFLOW_API_KEY")):
        raise ValueError("需要通过环境提供两种云端凭据")
    root.mkdir(parents=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    docs = root / "docs"
    docs.mkdir()
    guide = docs / "核对指南.md"
    guide.write_text(
        "登录接口核对：先查看现有错误日志，再使用测试账号复现。\n"
        "没有测试账号时，只整理现有日志和待验证问题，不声称已经定位根因。\n"
        "工作单导出不代表任务完成；最终记录核对结果或证据缺口。\n",
        encoding="utf-8",
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env["MAGI_API_TOKEN"] = secrets.token_urlsafe(32)
    report = {
        "scope": "real HTTP/Qianwen/BGE-M3/SQLite/process restart; synthetic data; no browser; no generation retries",
        "attempts": [],
        "checks": {},
        "pids": [],
        "source_sha256": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [Path(__file__), Path("scripts/serve_mvp_acceptance.py")]
        },
    }
    process = None

    def save():
        """保存已观察事实；不记录请求认证头、环境或异常正文。"""
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def start():
        """启动自己拥有的单worker子进程；就绪轮询仅查询health，不调用模型。"""
        child = subprocess.Popen(
            [
                sys.executable,
                "scripts/serve_mvp_acceptance.py",
                "--data-dir",
                str(root),
                "--docs-root",
                str(docs),
                "--port",
                str(port),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        report["pids"].append(child.pid)
        try:
            for _ in range(100):
                if child.poll() is not None:
                    raise RuntimeError("验收子进程提前退出")
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/health").status_code == 200:
                        return child
                except httpx.TransportError:
                    pass
                time.sleep(0.2)
            raise RuntimeError("验收服务未就绪")
        except BaseException:
            child.terminate()
            child.wait(timeout=15)
            raise

    def check(name, condition):
        """记录真实断言；失败保留此前全部事实并终止，避免跳过失败继续宣称通过。"""
        report["checks"][name] = bool(condition)
        save()
        if not condition:
            raise AssertionError(name)

    try:
        save()
        process = start()
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": "Bearer " + env["MAGI_API_TOKEN"]},
            timeout=300,
        ) as client:

            def call(method, path, body=None, expected=200):
                """一次HTTP请求对应一次留证；不重发失败的生成或写入。"""
                attempt = {"method": method, "path": path, "request": body}
                report["attempts"].append(attempt)
                save()
                response = client.request(method, path, json=body)
                attempt.update(status=response.status_code, response=response.json())
                save()
                if response.status_code != expected:
                    raise AssertionError(f"HTTP状态不符：{response.status_code}")
                return attempt["response"]

            def turn(number, text, **extra):
                """两轮审议分别使用新消息身份；返回的候选尚未保存。"""
                result = call(
                    "POST",
                    "/v1/deliberations",
                    dict(
                        chat_id="acceptance",
                        message_id=f"u{number}",
                        reply_message_id=f"a{number}",
                        parent_reply_message_id=f"a{number - 1}"
                        if number > 1
                        else None,
                        user_text=text,
                        **extra,
                    ),
                )
                check(
                    f"generation_{number}",
                    result["status"] == "COMPLETED" and result["proposal_id"],
                )
                return result

            def confirm(turn):
                """模拟用户确认合成候选；绑定候选摘要和回复身份，不直接写库。"""
                path = "/v1/proposals/" + turn["proposal_id"]
                proposal = call(
                    "GET",
                    path + "?chat_id=acceptance&reply_message_id=" + turn["reply"],
                )
                body = dict(
                    chat_id="acceptance",
                    reply_message_id=turn["reply"],
                    payload_hash=proposal["payload_hash"],
                )
                return call("POST", path + "/confirm", body), (path + "/confirm", body)

            first = turn(
                1,
                "登录接口核对：请根据核对指南安排查看错误日志、使用测试账号复现，并记录核对结果或缺口。只有60分钟。",
                time_budget_minutes=60,
            )
            check("candidate_is_not_plan", call("GET", "/v1/plans") == [])
            detail = call("GET", "/v1/turns/" + first["id"])
            retrieval = detail["result"]["snapshot"]["retrieval"]
            check("retrieved_documents", bool(retrieval["matches"]))
            check(
                "cloud_embedding_not_degraded",
                retrieval["embedding"]["enabled"]
                and not retrieval["embedding"]["degraded"]
                and retrieval["method"] == "dense cosine BAAI/bge-m3",
            )
            receipt, confirmation = confirm(first)
            plan_id = receipt["object_id"]
            path = "/v1/plans/" + plan_id
            old = call("GET", path + "?version=1")
            replay = call("POST", *confirmation)
            check("save_replay", replay["duplicate"] and replay["object_id"] == plan_id)
            progress = dict(
                request_id="blocked-1",
                version=1,
                expected_revision=0,
                status="BLOCKED",
                note="用户自报：已整理日志，但没有测试账号；剩余30分钟。",
                confirmed=True,
            )
            event = call("POST", path + "/execution", progress)
            # 请求已经成功后再发同一请求，模拟客户端没有拿到回执后的恢复。
            duplicate = call("POST", path + "/execution", progress)
            check(
                "execution_replay",
                duplicate["duplicate"] and duplicate["event_id"] == event["event_id"],
            )
            call(
                "POST",
                path + "/execution",
                progress | {"request_id": "stale"},
                expected=409,
            )
            second = turn(
                2,
                "请根据执行记录修订登录接口核对计划：没有测试账号，只剩30分钟。仅整理现有日志和待验证问题，不要求定位根因。",
                target_plan_id=plan_id,
                expected_version=1,
                time_budget_minutes=30,
            )
            frozen = second["result"]["snapshot"]["old_plan"]["execution"]
            check(
                "revision_received_progress",
                frozen["revision"] == 1 and frozen["status"] == "BLOCKED",
            )
            revised, _ = confirm(second)
            check("new_version", revised["result_version"] == 2)
            new = call("GET", path + "?version=2")
            # 资料后来改变，不应回写已保存的计划或当时的证据快照。
            guide.write_text(
                "新资料：本次验收后更新，不是旧计划的原始依据。", encoding="utf-8"
            )
            process.terminate()
            process.wait(timeout=15)
            process = start()
            check("old_plan_restored", call("GET", path + "?version=1") == old)
            check("new_plan_restored", call("GET", path + "?version=2") == new)
            old_execution = call("GET", path + "/execution?version=1")
            new_execution = call("GET", path + "/execution?version=2")
            check(
                "execution_restored",
                old_execution["revision"] == 1 and old_execution["status"] == "BLOCKED",
            )
            check(
                "new_execution_independent",
                new_execution["revision"] == 0
                and new_execution["status"] == "NOT_STARTED",
            )
            restored = call("GET", "/v1/turns/" + first["id"])
            check(
                "evidence_frozen",
                restored["result"]["snapshot"]["retrieval"] == retrieval,
            )
            check(
                "replay_after_restart",
                call("POST", path + "/execution", progress)["event_id"]
                == event["event_id"],
            )
            report["status"] = "PASSED"
    except Exception as exc:
        report["status"] = "FAILED"
        report["failure"] = {"type": type(exc).__name__}
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        save()
    print(
        json.dumps({"status": report["status"], "checks": report["checks"]}), flush=True
    )
    return report["status"] == "PASSED"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.exit(0 if run(args.data_dir.resolve(), args.output.resolve()) else 1)
