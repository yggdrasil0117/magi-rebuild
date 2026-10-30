"""终端业务边界：复用真实 API 与临时业务库，模型和 checkpoint 离线模拟。"""

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from magi.terminal import LOADING, Client, Terminal, safe_text
from magi.web.app import create_app
from tests import test_web_mvp as web_tests


@pytest.fixture
def env(tmp_path):
    """复用已有离线服务环境，不访问用户实际业务库。"""
    return web_tests.env.__wrapped__(tmp_path)


class Adapter:
    """将终端请求送进真实 FastAPI 路由，允许模拟响应丢失。"""

    def __init__(self, http):
        self.http = http
        self.lose_next = False
        self.requests = []

    def request(self, path, body=None):
        self.requests.append((path, body))
        response = self.http.request(
            "GET" if body is None else "POST",
            path,
            json=body,
            headers={"Authorization": "Bearer terminal-test-only-credential"},
        )
        if response.status_code >= 400:
            raise RuntimeError(response.json().get("message", "请求失败"))
        if self.lose_next:
            self.lose_next = False
            raise RuntimeError("模拟已处理但响应丢失")
        return response.json()


def test_terminal_shared_history_confirmation_retry_and_restart(env, capsys):
    """断线重试不重跑模型；取消不保存，确认响应丢失仍只产生一个计划版本。"""
    settings, service, session, calls = env
    with TestClient(
        create_app(settings, token="terminal-test-only-credential", service=service)
    ) as http:
        api = Adapter(http)
        answers = iter(["取消", "确认保存"])
        terminal = Terminal(api, "study", reader=lambda _: next(answers))
        terminal.history()
        terminal.command("日常问题")
        terminal.command("/council")
        api.lose_next = True
        with pytest.raises(RuntimeError, match="响应丢失"):
            terminal.command("本周六小时，论文与求职并行")
        pending = terminal.pending
        assert len(calls) == 4
        terminal.command("不能替换原请求")
        assert terminal.pending == pending
        terminal.command("/retry")
        assert terminal.pending is None and len(calls) == 4
        assert len(service.history("study")) == 2
        terminal.command("/save")
        assert not service.plans.list_current()
        # 读取候选成功后，只让确认 POST 的响应丢失。
        original = api.request

        def lose_confirm(path, body=None):
            if path.endswith("/confirm"):
                api.lose_next = True
            return original(path, body)

        api.request = lose_confirm
        with pytest.raises(RuntimeError, match="响应丢失"):
            terminal.command("/save")
        assert terminal.confirmation is not None
        assert len(service.plans.list_current()) == 1
        terminal.command("不允许新消息覆盖待确认保存")
        assert len(service.history("study")) == 2
        api.request = original
        terminal.command("/save")
        assert terminal.confirmation is None
        plan = service.plans.list_current()[0]
        assert plan.version == 1
        assert len(service.plans.list_versions(plan.id)) == 1
        # 新客户端从后端恢复；不读取旧客户端内存来推断保存。
        restarted = Terminal(api, "study")
        restarted.history()
        restarted.command("/save")
        assert restarted.parent == terminal.parent
        assert "已实际保存为 v1" in capsys.readouterr().out
        assert len(calls) == 4


def test_real_http_client_auth_and_terminal_turn(env):
    """临时本机 HTTP 服务验证真实 urllib 传输与认证，不调用真实模型。"""
    settings, service, _, _ = env
    app = create_app(settings, token="terminal-test-only-credential", service=service)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        worker = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            client = Client(
                f"http://127.0.0.1:{port}", "terminal-test-only-credential", timeout=5
            )
            terminal = Terminal(client, "study")
            terminal.history()
            terminal.command("终端真实 HTTP 连接")
            assert len(service.history("study")) == 1
            with pytest.raises(RuntimeError):
                Client(f"http://127.0.0.1:{port}", "wrong", timeout=5).request(
                    "/v1/plans"
                )
        finally:
            server.should_exit = True
            worker.join(5)
            assert not worker.is_alive()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com",
        "http://localhost@evil.com",
        "http://localhost/path",
        "http://localhost?token=x",
    ],
)
def test_reject_nonlocal_or_ambiguous_endpoint(url):
    with pytest.raises(ValueError):
        Client(url, "not-a-secret")


def test_terminal_plain_output_and_loading_library(capsys):
    assert len(LOADING) == len(set(LOADING)) == 64
    Terminal(None, "study").say("模型\x1b[2J\x07文本")
    assert "\x1b" not in capsys.readouterr().out
    assert safe_text("中文\n下一行") == "中文\n下一行"


def test_windows_powershell_launcher_enters_magi_module():
    """实际用 Windows PowerShell 启动，防止编码吞掉参数后误入 Python REPL。"""
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("仅在安装 Windows PowerShell 的环境验证")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "scripts/start_terminal.ps1"),
            "-Help",
        ],
        cwd=root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert b"--chat" in result.stdout and b"--no-color" in result.stdout
    assert b">>>" not in result.stdout
    # 两个含中文的入口都须在 PowerShell 5.1 中按 UTF-8 读取。
    for name in ("start_terminal.ps1", "start_magi.ps1"):
        assert (root / "scripts" / name).read_bytes().startswith(b"\xef\xbb\xbf")
