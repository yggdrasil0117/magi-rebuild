"""摘要覆盖边界和本地向量索引的版本隔离。"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from magi.harness.summary import ConversationSummary
from magi.personal import recall as module
from magi.personal.context import ContextService
from magi.personal.memory import MemoryService
from magi.personal.store import PersonalStore


def test_summary_resumes_and_preserves_original_history(tmp_path):
    calls = []

    def invoke(messages):
        calls.append(messages)
        return AIMessage(content="旧任务仍未完成")

    history = [
        message
        for index in range(5)
        for message in (HumanMessage(content=f"问题{index}"), AIMessage(content="答"))
    ]
    args = (
        tmp_path / "summary.db",
        "thread",
        SimpleNamespace(invoke=invoke),
        SimpleNamespace(num_ctx=8192),
        SimpleNamespace(emit=lambda *a, **k: None),
    )
    compressed = ConversationSummary(*args).compact(history)
    assert len(history) == 10
    assert compressed[1:] == history[4:]
    assert ConversationSummary(*args).compact(history) == compressed
    assert len(calls) == 1
    longer = [*history, HumanMessage(content="再继续")]

    def fail(_messages):
        raise TimeoutError

    broken = ConversationSummary(
        args[0], args[1], SimpleNamespace(invoke=fail), args[3], args[4]
    )
    assert broken.compact(longer) == longer
    assert ConversationSummary(*args).compact(history) == compressed
    assert len(calls) == 1


def test_qdrant_old_version_is_excluded_after_update_and_delete(tmp_path, monkeypatch):
    pytest.importorskip("qdrant_client")
    store = PersonalStore(tmp_path / "business.db")
    service = MemoryService(store)
    receipt = service.add(key="study", scope="global", value="10小时", request_id="add")

    class Provider:
        def embed(self, texts):
            return [[1.0] + [0.0] * 1023 for _ in texts]

    monkeypatch.setattr(module, "SiliconFlowEmbeddingProvider", Provider)
    settings = SimpleNamespace(business_db_path=store.path, embedding_min_score=0.45)

    def lookup():
        return module.recall(ContextService(store), "chat", "备考时间", settings)

    assert lookup()["items"][0]["version"] == 1
    service.update(
        receipt.object_id, expected_version=1, value="4小时", request_id="update"
    )
    with store.connect() as db:
        assert db.execute("SELECT version FROM memory_index_pending").fetchone()[0] == 2

    class Offline:
        def embed(self, texts):
            raise TimeoutError

    monkeypatch.setattr(module, "SiliconFlowEmbeddingProvider", Offline)
    assert lookup() == {"status": "UNAVAILABLE", "items": []}
    with store.connect() as db:
        assert db.execute("SELECT version FROM memory_index_pending").fetchone()[0] == 2
    monkeypatch.setattr(module, "SiliconFlowEmbeddingProvider", Provider)
    assert [item["value"] for item in lookup()["items"]] == ["4小时"]
    service.delete(receipt.object_id, expected_version=2, request_id="delete")
    assert lookup() == {"status": "OK", "items": []}
    with store.connect() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM memory_index_pending").fetchone()[0] == 0
        )
