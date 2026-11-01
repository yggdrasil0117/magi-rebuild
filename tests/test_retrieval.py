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
    assert match["line"] == 1 and match["offset"] == 160
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
