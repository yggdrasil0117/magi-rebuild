"""向量适配与缓存离线边界：不连接网络、不使用真实凭据。"""

import json
import sqlite3
from contextlib import closing

import httpx
import pytest

from magi.embedding import EmbeddingError, SiliconFlowEmbeddingProvider
from magi.retrieval import MagiLocalRetrievalProvider, RetrievalRequest


def vector(index):
    """合成1024维单位向量，仅用于证明缓存和校验机制。"""
    return [1.0 if i == index else 0.0 for i in range(1024)]


class FakeEmbedding:
    identity = "offline-1024-v1"

    def __init__(self):
        self.inputs = []
        self.fail = False

    def embed(self, texts):
        self.inputs.append(texts)
        if self.fail:
            raise EmbeddingError("TIMEOUT")
        return [vector(0) for text in texts]


def test_semantic_candidates_cache_versions_and_delete(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "guide.txt"
    source.write_text("计划导出不代表执行完成。", encoding="utf-8")
    provider = FakeEmbedding()
    db = tmp_path / "index.db"
    local = MagiLocalRetrievalProvider(docs, db, embedding=provider)
    first = local.retrieve(RetrievalRequest("输出清单算办妥吗"))
    assert first["status"] == "FOUND" and first["matches"][0]["keyword_score"] == 0
    assert first["embedding"]["embedded_chunks"] == 1
    second = local.retrieve(RetrievalRequest("输出清单算办妥吗"))
    assert second["embedding"]["cache_hits"] == 1 and len(provider.inputs) == 3
    source.write_text("新版资料与原版不同。", encoding="utf-8")
    changed = local.retrieve(RetrievalRequest("输出清单算办妥吗"))
    assert changed["matches"][0]["version_id"] != first["matches"][0]["version_id"]
    assert changed["embedding"]["embedded_chunks"] == 1
    source.unlink()
    assert (
        local.retrieve(RetrievalRequest("输出清单算办妥吗"))["status"] == "NO_RESULTS"
    )
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 2


def test_failure_explicitly_falls_back_and_preserves_versions(tmp_path):
    (tmp_path / "guide.txt").write_text("SQLite 数据库", encoding="utf-8")
    fake = FakeEmbedding()
    fake.fail = True
    db = tmp_path / "index.db"
    result = MagiLocalRetrievalProvider(tmp_path, db, embedding=fake).retrieve(
        RetrievalRequest("SQLite")
    )
    assert (
        result["embedding"]["degraded"]
        and result["embedding"]["error_code"] == "TIMEOUT"
    )
    assert result["matches"] and "keyword" in result["method"]
    assert "退回关键词" in result["notice"]
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 1


@pytest.mark.parametrize(
    "mode", ["401", "429", "timeout", "dimension", "nan", "indices", "zero", "reverse"]
)
def test_http_boundary(monkeypatch, mode):
    monkeypatch.setenv("SILICONFLOW_API_KEY", "synthetic.complete.key/value")

    def handle(request):
        assert request.headers["Authorization"] == "Bearer synthetic.complete.key/value"
        assert json.loads(request.content)["model"] == "BAAI/bge-m3"
        if mode == "timeout":
            raise httpx.ReadTimeout("private-data")
        if mode in {"401", "429"}:
            return httpx.Response(int(mode), json={"secret": "must not leak"})
        vectors = [vector(0), vector(1)]
        if mode == "dimension":
            vectors[0] = [1]
        if mode == "nan":
            return httpx.Response(
                200, content=b'{"data":[{"index":0,"embedding":[NaN]}]}'
            )
        if mode == "zero":
            vectors[0] = [0] * 1024
        rows = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
        if mode == "indices":
            rows[1]["index"] = 0
        if mode == "reverse":
            rows.reverse()
        return httpx.Response(200, json={"data": rows, "usage": {"total_tokens": 5}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = SiliconFlowEmbeddingProvider(client)
        if mode == "reverse":
            assert provider.embed(["a", "b"]) == [vector(0), vector(1)]
            assert provider.tokens == 5
        else:
            with pytest.raises(EmbeddingError) as error:
                provider.embed(["a", "b"])
            assert "private" not in str(error.value) and "secret" not in str(
                error.value
            )


def test_missing_key_no_network(monkeypatch):
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    with pytest.raises(EmbeddingError, match="MISSING_API_KEY"):
        SiliconFlowEmbeddingProvider().embed(["query"])


def test_vector_budget_and_cache_provider_identity(tmp_path):
    """向量请求最多前128块，换Provider不复用旧向量，查询裁剪必须标记。"""
    (tmp_path / "many.txt").write_text(
        "\n".join("片段" + str(i) for i in range(129)), encoding="utf-8"
    )
    fake = FakeEmbedding()
    db = tmp_path / "index.db"
    local = MagiLocalRetrievalProvider(tmp_path, db, embedding=fake)
    first = local.retrieve(RetrievalRequest("查询" * 1500))
    assert len(fake.inputs) == 5
    assert len(fake.inputs[0][0]) == 2000
    assert (
        first["embedding"]["pool_truncated"] and first["embedding"]["query_truncated"]
    )
    assert not first["complete"] and len(first["matches"]) == 5
    other = FakeEmbedding()
    other.identity = "different-provider-v1"
    second = MagiLocalRetrievalProvider(tmp_path, db, embedding=other).retrieve(
        RetrievalRequest("问题")
    )
    assert second["embedding"]["cache_hits"] == 0


def test_partial_embedding_failure_does_not_commit_partial_cache(tmp_path):
    """首批成功、次批失败时，缓存保存点回滚，当前版本记录仍可追溯。"""
    (tmp_path / "many.txt").write_text(
        "\n".join("SQLite" + str(i) for i in range(40)), encoding="utf-8"
    )

    class PartialFailure(FakeEmbedding):
        def embed(self, texts):
            if len(self.inputs) == 2:
                raise EmbeddingError("TIMEOUT")
            return super().embed(texts)

    db = tmp_path / "index.db"
    result = MagiLocalRetrievalProvider(
        tmp_path, db, embedding=PartialFailure()
    ).retrieve(RetrievalRequest("SQLite"))
    assert result["embedding"]["degraded"]
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT count(*) FROM versions").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE name='embeddings'"
            ).fetchone()[0]
            == 0
        )
