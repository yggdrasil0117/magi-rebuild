"""Real Edge + cloud + isolated API process validation, with bounded calls.

Requires playwright (development only), an installed Edge browser and an
environment API key. Never writes API keys, browser state or request headers.
"""

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.sync_api import sync_playwright

from magi.harness.config import load_config


def write_config(path, settings, data):
    sections = {
        "model": {
            "provider": settings.provider,
            "name": settings.model,
            "base_url": settings.base_url,
            "api_key_env": settings.api_key_env,
            "timeout_seconds": settings.timeout_seconds,
            "num_ctx": settings.num_ctx,
            "num_predict": settings.num_predict,
        },
        "tools": {"root": settings.docs_root.as_posix()},
        "storage": {
            "checkpoint_db": (data / "checkpoint.db").as_posix(),
            "event_log": (data / "events.jsonl").as_posix(),
            "business_db": (data / "business.db").as_posix(),
        },
        "limits": {
            "model_calls": settings.max_model_calls,
            "tool_calls": settings.max_tool_calls,
        },
    }
    path.write_text(
        "\n\n".join(
            "["
            + name
            + "]\n"
            + "\n".join(
                key + " = " + json.dumps(value, ensure_ascii=False)
                for key, value in values.items()
            )
            for name, values in sections.items()
        ),
        encoding="utf-8",
    )


class Server:
    def __init__(self, root, config, token, port, log):
        self.root, self.config, self.token, self.port, self.log = (
            root,
            config,
            token,
            port,
            log,
        )
        self.process = None

    def start(self):
        env = dict(os.environ, MAGI_CONFIG=str(self.config), MAGI_API_TOKEN=self.token)
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "magi.web.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=self.root,
            env=env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        deadline = time.monotonic() + 30
        with httpx.Client(trust_env=False, timeout=1) as client:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError("isolated API exited during startup")
                try:
                    if client.get(f"http://127.0.0.1:{self.port}/health").is_success:
                        return
                except httpx.RequestError:
                    pass
                time.sleep(0.2)
        raise TimeoutError("isolated API startup timeout")

    def stop(self):
        if self.process is None or self.process.poll() is not None:
            return
        if os.name == "nt":
            # Only the process tree created above, including the venv launcher.
            subprocess.run(
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                capture_output=True,
                check=True,
                timeout=15,
            )
        else:
            self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def main():
    root = Path(__file__).resolve().parents[1]
    settings = load_config(root / "magi.qianwen.toml")
    if not os.getenv(settings.api_key_env):
        raise ValueError("set " + settings.api_key_env + " in the current process")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = root / ".magi" / "web-validation" / stamp
    output_dir.mkdir(parents=True)
    output = output_dir / "report.json"
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": settings.provider,
        "model": settings.model,
        "scope": "real Edge, loopback HTTP, isolated DB, actual process restart",
        "steps": {},
        "attempts": [],
        "http": [],
        "passed": False,
    }

    def record(name, value):
        report["steps"][name] = value
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(name + ": passed", flush=True)

    token = secrets.token_hex(32)
    with (
        tempfile.TemporaryDirectory(prefix="magi-web-validation-") as directory,
        (output_dir / "server.log").open("w", encoding="utf-8") as log,
    ):
        data = Path(directory)
        config = data / "config.toml"
        write_config(config, settings, data)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = Server(root, config, token, port, log)
        base = f"http://127.0.0.1:{port}"
        try:
            server.start()
            with (
                httpx.Client(
                    base_url=base,
                    headers={"Authorization": "Bearer " + token},
                    trust_env=False,
                ) as client,
                sync_playwright() as playwright,
            ):
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                try:
                    page = browser.new_page(viewport={"width": 1280, "height": 1000})
                    page.on(
                        "response",
                        lambda response: report["http"].append(
                            {
                                "path": urlsplit(response.url).path,
                                "status": response.status,
                            }
                        ),
                    )
                    page.goto(base)
                    page.locator("#chat").fill("cloud-round")
                    page.locator("#token").fill("wrong-token")
                    page.locator("#load").click()
                    page.wait_for_function(
                        "document.querySelector('#status').textContent.includes('UNAUTHORIZED')"
                    )
                    record(
                        "unauthorized_visible",
                        {"code": "UNAUTHORIZED", "no_model_call": True},
                    )
                    page.locator("#token").fill(token)
                    page.locator("#send").click()
                    page.wait_for_function(
                        "document.querySelector('#status').textContent.includes('INVALID_INPUT')"
                    )
                    assert client.get("/v1/chats/cloud-round/turns").json() == []
                    record(
                        "invalid_input_visible",
                        {"code": "INVALID_INPUT", "no_turn_created": True},
                    )
                    page.locator("#reset").click()

                    def send(text, endpoint):
                        page.locator("#text").fill(text)
                        with page.expect_response(
                            lambda response: (
                                urlsplit(response.url).path == endpoint
                                and response.request.method == "POST"
                            ),
                            timeout=270000,
                        ) as pending:
                            page.locator("#send").click()
                        response = pending.value
                        turn = response.json()
                        report["attempts"].append(
                            {
                                "endpoint": endpoint,
                                "http_status": response.status,
                                "response": turn,
                            }
                        )
                        output.write_text(
                            json.dumps(report, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        assert response.ok, "chat HTTP status " + str(response.status)
                        assert turn["status"] == "COMPLETED", (
                            "model turn: " + turn["status"]
                        )
                        page.wait_for_function(
                            "document.querySelector('#send').disabled===false"
                        )
                        return turn

                    daily = send(
                        "本周论文实验和求职准备共可用六小时，要保留休息。请简短回应，不要制定计划。",
                        "/v1/chat",
                    )
                    assert daily["mode"] == "chat" and daily["result"] is None
                    record("daily", daily)
                    page.locator("#mode").select_option("deliberations")
                    page.locator("#budget").fill("360")
                    first = send(
                        "根据刚才背景比较论文和求职的投入，给本周可验证的简短计划，包含休息。",
                        "/v1/deliberations",
                    )
                    assert first["proposal_id"] and first["result"]["calls"] <= 4
                    assert client.get("/v1/plans").json() == []
                    record("create", first)

                    def confirm():
                        with page.expect_response(
                            lambda response: (
                                response.request.method == "POST"
                                and urlsplit(response.url).path.endswith("/confirm")
                            ),
                            timeout=15000,
                        ) as pending:
                            page.locator("article").last.locator("button").click()
                        assert pending.value.ok
                        receipt = pending.value.json()
                        assert receipt["committed"]
                        page.wait_for_function(
                            "document.querySelector('#receipt').textContent.includes('operation_id')"
                        )
                        return receipt

                    saved = confirm()
                    assert saved["result_version"] == 1 and not saved["duplicate"]
                    duplicate = confirm()
                    assert (
                        duplicate["duplicate"]
                        and duplicate["operation_id"] == saved["operation_id"]
                    )
                    assert len(client.get("/v1/plans").json()) == 1
                    record("saved", saved)
                    record("duplicate", duplicate)
                    page.screenshot(path=str(output_dir / "v1.png"), full_page=True)
                    plan_id = saved["object_id"]
                    v1 = client.get("/v1/plans/" + plan_id).json()

                    page.reload()
                    page.locator("#token").fill(token)
                    page.locator("#chat").fill("cloud-round")
                    page.locator("#load").click()
                    page.wait_for_function(
                        "document.querySelectorAll('article').length===2"
                    )
                    record("refresh_history", {"turn_count": 2})
                    server.stop()
                    server.start()
                    page.locator("#planId").fill(plan_id)
                    page.locator("#planVersion").fill("1")
                    with page.expect_response(
                        lambda response: (
                            urlsplit(response.url).path == "/v1/plans/" + plan_id
                        )
                    ):
                        page.locator("#getPlan").click()
                    page.wait_for_function(
                        "document.querySelector('#receipt').textContent.includes('source_epoch')"
                    )
                    restored = json.loads(page.locator("#receipt").inner_text())
                    assert restored == v1
                    record("after_process_restart", restored)

                    page.locator("#mode").select_option("deliberations")
                    page.locator("#budget").fill("120")
                    page.locator("#target").fill(plan_id)
                    page.locator("#version").fill("1")
                    feedback = send(
                        "实验卡住了，本周只剩两小时。对照原计划缩减任务，保留休息，解释保留和放弃什么。",
                        "/v1/deliberations",
                    )
                    record("feedback", feedback)
                    updated = confirm()
                    assert (
                        updated["object_id"] == plan_id
                        and updated["result_version"] == 2
                    )
                    record("updated", updated)
                    page.screenshot(path=str(output_dir / "v2.png"), full_page=True)
                    for version in (1, 2):
                        page.locator("#planVersion").fill(str(version))
                        with page.expect_response(
                            lambda response: (
                                urlsplit(response.url).path == "/v1/plans/" + plan_id
                            )
                        ) as pending:
                            page.locator("#getPlan").click()
                        plan = pending.value.json()
                        assert plan["version"] == version
                        if version == 1:
                            assert plan["body"] == v1["body"]
                        else:
                            assert plan["body"] != v1["body"]
                        record("v" + str(version), plan)
                    page.locator("#mode").select_option("chat")
                    for field in ("budget", "target", "version"):
                        page.locator("#" + field).fill("")
                    followup = send(
                        "刚才修订后的第一步是什么？请简短回答。", "/v1/chat"
                    )
                    record("followup", followup)
                    page.screenshot(
                        path=str(output_dir / "followup.png"), full_page=True
                    )
                    report["passed"] = True
                finally:
                    browser.close()
        except Exception as exc:
            report["error_type"] = type(exc).__name__
            print("validation failed: " + type(exc).__name__, flush=True)
        finally:
            server.stop()
            output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(output, flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
