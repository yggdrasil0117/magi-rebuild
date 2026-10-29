"""Real Ollama + real HTTP restart demonstration; isolated temporary databases."""

import json
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from magi.harness.config import load_config
from magi.web.app import create_app


def main():
    root = Path(__file__).resolve().parents[1]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    output = root / ".magi" / f"demo-evidence-{stamp}.json"
    output.parent.mkdir(exist_ok=True)
    token = "isolated-demo-token-not-a-production-secret"
    headers = {"Authorization": "Bearer " + token}
    evidence = {}
    with tempfile.TemporaryDirectory(prefix="magi-mvp-") as directory:
        data = Path(directory)
        settings = replace(
            load_config(root / "magi.harness.toml"),
            db_path=data / "checkpoint.db",
            business_db_path=data / "personal.db",
            events_path=data / "events.jsonl",
        )

        def post(client, endpoint, number, text, **kwargs):
            response = client.post(
                endpoint,
                headers=headers,
                json={
                    "chat_id": "demo",
                    "message_id": "u" + number,
                    "reply_message_id": "a" + number,
                    "parent_reply_message_id": "a" + str(int(number) - 1)
                    if int(number) > 1
                    else None,
                    "user_text": text,
                    **kwargs,
                },
            )
            response.raise_for_status()
            return response.json()

        def confirm(client, turn):
            p = client.get(
                "/v1/proposals/" + turn["proposal_id"],
                headers=headers,
                params={"chat_id": "demo", "reply_message_id": turn["reply"]},
            ).json()
            response = client.post(
                "/v1/proposals/" + p["id"] + "/confirm",
                headers=headers,
                json={
                    "chat_id": "demo",
                    "reply_message_id": turn["reply"],
                    "payload_hash": p["payload_hash"],
                },
            )
            response.raise_for_status()
            return response.json()

        try:
            with TestClient(create_app(settings, token=token)) as client:
                evidence["daily"] = post(
                    client,
                    "/v1/chat",
                    "1",
                    "这周论文实验和求职准备共可用六小时，需要保留休息时间。请简短记住本次背景，不要制定计划。",
                )
                print("daily:", evidence["daily"]["status"], flush=True)
                evidence["council"] = post(
                    client,
                    "/v1/deliberations",
                    "2",
                    "根据刚才六小时背景，比较取舍。总预算360分钟，仅安排实验180分钟、求职120分钟、休息60分钟三个步骤，各给完成条件。",
                    time_budget_minutes=360,
                )
                print("council:", evidence["council"]["status"], flush=True)
                if not evidence["council"]["proposal_id"]:
                    raise RuntimeError(
                        "real council did not produce a complete proposal"
                    )
                evidence["saved"] = confirm(client, evidence["council"])
                evidence["duplicate"] = confirm(client, evidence["council"])
                assert evidence["saved"]["committed"]
                assert evidence["duplicate"]["duplicate"]
                assert (
                    evidence["saved"]["operation_id"]
                    == evidence["duplicate"]["operation_id"]
                )
            with TestClient(create_app(settings, token=token)) as client:
                plan_id = evidence["saved"]["object_id"]
                evidence["after_restart"] = client.get(
                    "/v1/plans/" + plan_id, headers=headers
                ).json()
                assert evidence["after_restart"]["version"] == 1
                evidence["feedback"] = post(
                    client,
                    "/v1/deliberations",
                    "3",
                    "执行反馈：实验卡住了，只剩120分钟。对照旧计划，缩减为实验诊断30分钟、简历60分钟、休息30分钟三个步骤，各给完成条件，解释保留与放弃的理由。",
                    target_plan_id=plan_id,
                    expected_version=1,
                    time_budget_minutes=120,
                )
                print("feedback:", evidence["feedback"]["status"], flush=True)
                if not evidence["feedback"]["proposal_id"]:
                    raise RuntimeError("feedback did not produce a complete proposal")
                evidence["updated"] = confirm(client, evidence["feedback"])
                evidence["v1"] = client.get(
                    "/v1/plans/" + plan_id + "?version=1", headers=headers
                ).json()
                evidence["v2"] = client.get(
                    "/v1/plans/" + plan_id + "?version=2", headers=headers
                ).json()
                assert evidence["updated"]["result_version"] == 2
                assert evidence["v1"]["body"] == evidence["after_restart"]["body"]
                assert evidence["v2"]["version"] == 2
                evidence["followup"] = post(
                    client, "/v1/chat", "4", "刚才修订计划的第一步是什么？简短回答。"
                )
            evidence["passed"] = True
        except Exception as exc:
            evidence["passed"] = False
            evidence["error"] = type(exc).__name__ + ": " + str(exc)
            raise
        finally:
            output.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print("evidence:", output, flush=True)


if __name__ == "__main__":
    main()
