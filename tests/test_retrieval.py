import pytest

from magi.council.sources import selected_excerpts, source_options
from magi.retrieval import MagiLocalRetrievalProvider, RetrievalRequest


def test_versions_and_frozen_quotes(tmp_path):
    """文件变化只影响新检索；旧快照引用仍还原旧原文。"""
    path = tmp_path / "guide.txt"
    path.write_text("SQLite 旧版本", encoding="utf-8")
    provider = MagiLocalRetrievalProvider(tmp_path)
    old = provider.retrieve(RetrievalRequest("SQLite"))
    path.write_text("SQLite 新版本", encoding="utf-8")
    new = provider.retrieve(RetrievalRequest("SQLite"))
    a, b = old["matches"][0], new["matches"][0]
    assert a["document_id"] == b["document_id"]
    assert a["version_id"] != b["version_id"]
    assert a["chunk_id"] != b["chunk_id"]
    snapshot = dict(retrieval=old, evidence_ids=[a["evidence_ref"]])
    assert (
        selected_excerpts([source_options(snapshot)[0]["id"]], snapshot)[0]["quote"]
        == "SQLite 旧版本"
    )
    with pytest.raises(ValueError):
        selected_excerpts(["invented"], snapshot)
    assert (
        MagiLocalRetrievalProvider(tmp_path).retrieve(RetrievalRequest("同义改写"))[
            "status"
        ]
        == "NO_RESULTS"
    )
    path.unlink()
    assert provider.retrieve(RetrievalRequest("SQLite"))["status"] == "NO_RESULTS"


def test_read_failure(tmp_path):
    """资料根目录不存在时明确失败，不伪装成没有答案。"""
    assert (
        MagiLocalRetrievalProvider(tmp_path / "missing").retrieve(
            RetrievalRequest("词")
        )["status"]
        == "FAILED"
    )


def test_index_restart_and_incomplete(tmp_path):
    """索引重开保留旧版本，但当前检索不能召回已删除文件。"""
    import sqlite3

    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "guide.md"
    source.write_text("计划导出只生成文件，不代表执行完成。", encoding="utf-8")
    database = tmp_path / "retrieval.db"
    first = MagiLocalRetrievalProvider(docs, database).retrieve(
        RetrievalRequest("计划导出")
    )
    assert first["matches"][0]["score"] >= 3
    source.write_text("计划导出新说明。", encoding="utf-8")
    second = MagiLocalRetrievalProvider(docs, database).retrieve(
        RetrievalRequest("计划导出")
    )
    assert first["matches"][0]["version_id"] != second["matches"][0]["version_id"]
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 2
    source.unlink()
    assert (
        MagiLocalRetrievalProvider(docs, database).retrieve(
            RetrievalRequest("计划导出")
        )["status"]
        == "NO_RESULTS"
    )
    (docs / "big.txt").write_bytes(b"x" * 32769)
    assert not MagiLocalRetrievalProvider(docs, database).retrieve(
        RetrievalRequest("x")
    )["complete"]


def test_chinese_question_and_chunk_positions(tmp_path):
    """中文自然问句能匹配字词；长行窗口保留准确位置和原文。"""
    text = "甲" * 160 + "计划导出" + "乙" * 180
    (tmp_path / "long.txt").write_text(text, encoding="utf-8")
    result = MagiLocalRetrievalProvider(tmp_path).retrieve(
        RetrievalRequest("计划导出意味着完成吗")
    )
    match = result["matches"][0]
    assert match["line"] == 1 and match["offset"] == 128
    assert (
        text[match["offset"] : match["offset"] + len(match["quote"])] == match["quote"]
    )


def test_fts5_available():
    """只探测标准组件可用性；当前排序没有冒充BM25。"""
    import sqlite3

    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
        db.execute("INSERT INTO probe VALUES ('SQLite evidence')")
        assert (
            db.execute(
                "SELECT count(*) FROM probe WHERE probe MATCH 'SQLite'"
            ).fetchone()[0]
            == 1
        )


def test_documents_remain_selectable_with_long_context():
    """长问题不能挤掉本轮资料的引用目录；编号绑定当前快照而非永久ID。"""
    document = dict(evidence_ref="document:chunk", quote="导出不代表执行完成。")
    snapshot = dict(
        question="长" * 8000,
        memories=[dict(key="偏好", value="记忆" * 2000)],
        retrieval={"matches": [document]},
        evidence_ids=["user:current", "memory:偏好", "document:chunk"],
    )
    options = source_options(snapshot)
    assert len(options) == 24
    selected = next(
        item["id"] for item in options if item["evidence_ref"] == "document:chunk"
    )
    assert selected_excerpts([selected], snapshot) == [document]


def test_overlap_recovers_boundary_and_preserves_exact_quote(tmp_path):
    """跨旧160字切口的英文标识，在重叠块中完整保留且可字面检索。"""
    from magi.retrieval import chunks

    text = "甲" * 156 + "SQLiteRestart" + "乙" * 180
    assert not any(
        "SQLiteRestart" in text[i : i + 160] for i in range(0, len(text), 160)
    )
    (tmp_path / "boundary.txt").write_text(text, encoding="utf-8")
    result = MagiLocalRetrievalProvider(tmp_path).retrieve(
        RetrievalRequest("SQLiteRestart")
    )
    assert result["status"] == "FOUND"
    item = result["matches"][0]
    assert item["offset"] == 128 and "SQLiteRestart" in item["quote"]
    assert item["quote"] == text[item["offset"] : item["offset"] + len(item["quote"])]
    assert list(chunks("甲" * 160)) == [(1, 0, "甲" * 160)]


def test_duplicate_content_does_not_consume_return_budget(tmp_path):
    """同文档重复段不挤掉其他依据，跨文档同文仍保留独立来源。"""
    (tmp_path / "a.txt").write_text(
        "\n".join(["SQLite repeated"] * 8 + ["SQLite additional"]), encoding="utf-8"
    )
    (tmp_path / "b.txt").write_text("SQLite repeated", encoding="utf-8")
    result = MagiLocalRetrievalProvider(tmp_path).retrieve(RetrievalRequest("SQLite"))
    assert len(result["matches"]) == 3
    assert result["deduplicated"] == 7 and not result["truncated"]
    assert any(m["quote"] == "SQLite additional" for m in result["matches"])


def test_dedup_keeps_additional_context_and_stops_at_return_budget():
    """先选短块不应抹掉后续额外信息；找到第六条即可证明截断，无需二次全量比较。"""
    from magi.retrieval import distinct_matches

    items = [
        dict(version_id="v", quote="SQLite"),
        dict(version_id="v", quote="SQLite还需要备份"),
    ]
    selected, removed, truncated = distinct_matches(items)
    assert len(selected) == 2 and removed == 0 and not truncated

    class Bounded:
        def __iter__(self):
            for index in range(6):
                yield dict(version_id="v", quote="独立" + str(index) + "结尾")
            raise AssertionError("已证明截断后不应继续扫描")

    assert distinct_matches(Bounded())[2]


def test_chunk_policy_change_does_not_reuse_legacy_vector(tmp_path):
    """同文档版本升级分块策略后，旧块与旧向量留存但不当成新策略缓存。"""
    import json
    import sqlite3
    from contextlib import closing

    from magi.retrieval import digest
    from tests.test_embedding import FakeEmbedding, vector

    (tmp_path / "guide.txt").write_text("SQLite原文", encoding="utf-8")
    database = tmp_path / "index.db"
    first = MagiLocalRetrievalProvider(tmp_path, database).retrieve(
        RetrievalRequest("SQLite")
    )["matches"][0]
    legacy_id = digest(f"{first['version_id']}:1:0")
    with closing(sqlite3.connect(database)) as db:
        db.execute(
            "INSERT INTO chunks VALUES (?,?,?,?,?)",
            (legacy_id, first["version_id"], 1, 0, first["quote"]),
        )
        db.execute(
            "CREATE TABLE embeddings(provider TEXT,chunk_id TEXT,vector TEXT,PRIMARY KEY(provider,chunk_id))"
        )
        db.execute(
            "INSERT INTO embeddings VALUES (?,?,?)",
            (FakeEmbedding.identity, legacy_id, json.dumps(vector(1))),
        )
        db.commit()
    current = MagiLocalRetrievalProvider(
        tmp_path, database, embedding=FakeEmbedding()
    ).retrieve(RetrievalRequest("SQLite"))
    assert (
        current["embedding"]["cache_hits"] == 0
        and current["embedding"]["embedded_chunks"] == 1
    )
    assert current["matches"][0]["chunk_id"] != legacy_id
    with closing(sqlite3.connect(database)) as db:
        assert (
            db.execute("SELECT quote FROM chunks WHERE id=?", (legacy_id,)).fetchone()[
                0
            ]
            == first["quote"]
        )
