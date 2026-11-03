"""联网工具预算、失败保存和Web入口集成；离线传输替身不冒充真实联网。"""

import json
from contextlib import contextmanager
from copy import deepcopy

import pytest
from mcp.types import CallToolResult, TextContent

from magi.web.service import WebError, WebService, digest
from magi.web_research import WebResearch, normalize, public_url
from tests.test_web_mvp import env as env
from tests.test_web_mvp import request


def response(text, error=False):
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=error)


@pytest.mark.parametrize(
    "url",
    [
        "file:///a",
        "http://127.0.0.1/x",
        "http://10.0.0.1",
        "http://localhost",
        "http://x.local",
        "https://user:secret@example.com",
        "https://example.com:1234",
        "javascript:alert(1)",
    ],
)
def test_reject_nonpublic_url(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_normalize_bounds_and_failures():
    text = "\n".join(
        f"Title: page{i}\nURL: https://example.com/{i}\n" + "x" * 3000 for i in range(5)
    )
    data = normalize(response(text), "search", "question")
    assert len(data["sources"]) == 3
    assert all(len(s["excerpt"]) == 2000 and s["truncated"] for s in data["sources"])
    assert normalize(response(""), "search", "q")["status"] == "NO_RESULTS"
    assert (
        normalize(response("unexpected format"), "search", "q")["status"] == "UNPARSED"
    )
    assert (
        normalize(response("secret upstream message", True), "search", "q")[
            "error_code"
        ]
        == "REMOTE_TOOL_ERROR"
    )
    assert (
        normalize(response("Access denied"), "fetch", "https://example.com")["status"]
        == "UNPARSED"
    )
    fetched = normalize(
        response("# Page\nURL: https://example.com/page\n" + "a" * 5000),
        "fetch",
        "https://example.com",
    )
    assert fetched["sources"][0]["url"] == "https://example.com/page"
    assert fetched["sources"][0]["truncated"]


def test_budget_and_failure_saved_before_return():
    saves, calls = [], []

    async def transport(name, args):
        calls.append((name, args))
        raise ExceptionGroup("sdk", [TimeoutError("sensitive detail")])

    research = WebResearch(lambda records: saves.append(deepcopy(records)), transport)
    for _ in range(3):
        result = json.loads(research.call("search", "Python", "official docs"))
        assert result["error_code"] == "TIMEOUT"
    assert len(calls) == 3 and saves[0][0]["status"] == "RUNNING"
    assert saves[-1][-1]["status"] == "FAILED"
    assert "sensitive" not in json.dumps(saves)
    assert "CALL_BUDGET_EXCEEDED" in research.call("search", "extra", "docs")
    assert len(calls) == 3


@pytest.mark.parametrize("fail_answer", [False, True])
def test_web_sources_persist_when_model_succeeds_or_fails(
    env, monkeypatch, fail_answer
):
    """真实业务SQLite，模拟一次工具调用；模型随后失败仍保留来源，重启读取不联网。"""
    settings, _, _, _ = env
    import magi.web.service as module
    import magi.web_research as research_module

    calls = []

    async def transport(name, args):
        calls.append(name)
        return response(
            "Title: Python\nURL: https://docs.python.org/3/\nOfficial documentation"
        )

    monkeypatch.setattr(
        research_module, "WebResearch", lambda persist: WebResearch(persist, transport)
    )

    @contextmanager
    def factory(settings, logical, extra_tools=()):
        class Session:
            def ask(self, text):
                tool = next(t for t in extra_tools if t.name == "magi_web_search")
                tool.invoke(dict(query="Python", objective="official docs"))
                if fail_answer:
                    raise RuntimeError("model failure")
                return "已找到Python官方文档"

        yield Session()

    monkeypatch.setattr(module, "open_personal_session", factory)
    service = WebService(settings, "owner", session_factory=factory)
    req = request(web_enabled=True)
    if fail_answer:
        with pytest.raises(WebError):
            service.turn(req, "chat")
    else:
        assert service.turn(req, "chat")["status"] == "COMPLETED"
    restored = WebService(settings, "owner", session_factory=factory)
    stored = restored.history("study")[0]
    assert (
        stored["web_research"][0]["sources"][0]["url"] == "https://docs.python.org/3/"
    )
    assert restored.turn(req, "chat")["id"] == stored["id"]
    assert len(calls) == 1 and not restored.plans.list_current()


def test_opt_in_and_legacy_digest(env):
    _, service, _, _ = env
    req = request()
    turn = service.turn(req, "chat")
    data = req.model_dump(exclude={"web_enabled"})
    assert turn["digest"] == digest([data, "chat"])
    with pytest.raises(WebError, match="日常聊天"):
        service.turn(request("2", web_enabled=True), "council")
