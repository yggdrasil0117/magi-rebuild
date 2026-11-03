"""用网页默认运行时验证核心改造后第035轮功能仍可用。"""

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from magi.personal import recall
from magi.web.service import WebError, WebService
from tests.test_harness_runtime import ScriptedModel
from tests.test_web_mvp import env as env
from tests.test_web_mvp import request


def test_native_chat_compacts_model_input_and_preserves_old_turns(env, monkeypatch):
    settings, _, _, _ = env
    settings = replace(settings, num_ctx=8192)

    class SummaryModel(ScriptedModel):
        def _generate(self, messages, *args, **kwargs):
            if messages[0].text.startswith("用中文压缩旧对话"):
                reply = "旧任务尚待核验"
            else:
                reply = "续接"
            self.responses.append(AIMessage(content=reply))
            return super()._generate(messages, *args, **kwargs)

    model = SummaryModel(responses=[])
    monkeypatch.setattr("magi.harness.runtime.build_model", lambda _: model)
    for number in range(1, 5):
        service = WebService(settings, "owner")
        req = request(str(number)).model_copy(
            update={"user_text": f"ROUND-{number} " + "x" * 800}
        )
        assert service.turn(req, "chat")["status"] == "COMPLETED"
    final = [message.text for message in model.requests[-1]]
    assert any("旧任务尚待核验" in text for text in final)
    assert not any(text.startswith("ROUND-1") for text in final)
    assert any(text.startswith("ROUND-4") for text in final)
    assert service.history("study")[0]["user_text"].startswith("ROUND-1")


def test_native_chat_recall_updates_after_reopen(env, monkeypatch):
    pytest.importorskip("qdrant_client")
    settings, _, _, _ = env
    model = ScriptedModel(responses=[AIMessage(content="回复")] * 2)

    class Provider:
        def embed(self, texts):
            return [[1.0] + [0.0] * 1023 for _ in texts]

    monkeypatch.setattr(recall, "SiliconFlowEmbeddingProvider", Provider)
    monkeypatch.setattr("magi.harness.runtime.build_model", lambda _: model)
    service = WebService(settings, "owner")
    receipt = service.memories.add(
        key="备考", value="每周10小时", scope="global", request_id="add"
    )
    service.turn(request(memory_recall=True), "chat")
    service.memories.update(
        receipt.object_id, value="每周4小时", expected_version=1, request_id="update"
    )
    reopened = WebService(settings, "owner")
    turn = reopened.turn(request("2", memory_recall=True), "chat")
    assert [row["version"] for row in turn["result"]["memory_recall"]["items"]] == [2]
    assert (
        reopened.history("study")[-1]["memory_recall"]["items"][0]["value"]
        == "每周4小时"
    )
    assert "每周4小时" in model.requests[-1][0].text
    assert "每周10小时" not in model.requests[-1][0].text


def test_current_turn_over_budget_skips_summary(env, monkeypatch):
    settings, _, _, _ = env
    model = ScriptedModel(responses=[])
    monkeypatch.setattr("magi.harness.runtime.build_model", lambda _: model)
    service = WebService(settings, "owner")
    req = request().model_copy(update={"user_text": "字" * 8000})
    with pytest.raises(WebError) as error:
        service.turn(req, "chat")
    assert error.value.code == "CURRENT_TURN_TOO_LARGE"
    assert "当前消息" in service.history("study")[0]["text"]
    assert not model.requests


def test_web_summary_resumes_after_second_batch_timeout(tmp_path):
    import json
    import sqlite3
    from contextlib import closing
    from types import SimpleNamespace

    from langchain_core.messages import HumanMessage

    from magi.harness.summary import ConversationSummary

    history = [
        message
        for number in range(5)
        for message in (
            HumanMessage(content=f"{number}:" + "x" * 650),
            AIMessage(content="答"),
        )
    ]
    path = tmp_path / "summaries.db"
    calls = []

    def invoke(messages):
        calls.append(json.loads(messages[-1].text))
        if len(calls) == 2:
            raise TimeoutError
        return AIMessage(content="前序任务未完成")

    def reopen(run):
        return ConversationSummary(
            path,
            "thread",
            SimpleNamespace(invoke=invoke),
            SimpleNamespace(num_ctx=2048),
            SimpleNamespace(run_id=run, emit=lambda *a, **k: None),
        )

    assert (
        reopen("first").compact(history, keep_turns=1, max_batches=3)[1:] == history[2:]
    )
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT covered FROM summaries").fetchone() == (2,)
    assert (
        reopen("second").compact(history, keep_turns=1, max_batches=3)[1:]
        == history[8:]
    )
    assert calls[2] == calls[1]
