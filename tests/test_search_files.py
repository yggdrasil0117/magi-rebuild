"""关键词检索的路径、快照和有界覆盖检查。"""

import hashlib
import os
import subprocess

import pytest

from magi.agent import search


def test_search_returns_exact_quote_line_and_raw_hash(tmp_path):
    raw = "开头\nSQLite保存计划\n结尾\n".encode("utf-8-sig")
    (tmp_path / "note.md").write_bytes(raw)
    result = search.search_files(tmp_path, "SQLite")
    assert result["complete"] and len(result["matches"]) == 1
    assert result["matches"][0] == {
        "path": "note.md",
        "line": 2,
        "quote": "SQLite保存计划",
        "quote_truncated": False,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    assert not search.search_files(tmp_path, "sqlite")["matches"]


def test_literal_query_is_not_regex_and_subdirectory_is_respected(tmp_path):
    (tmp_path / "outside.md").write_text("needle", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inside.txt").write_text("literal .*", encoding="utf-8")
    result = search.search_files(tmp_path, ".*", "sub")
    assert result["matches"][0]["quote"] == "literal .*"
    assert search.search_files(tmp_path, "needle", "sub")["matches"] == []


@pytest.mark.parametrize(
    "query,path",
    [
        ("", "."),
        (" ", "."),
        ("a\nb", "."),
        ("x" * 81, "."),
        ("needle", ".."),
        ("needle", ""),
        ("needle", ".hidden"),
    ],
)
def test_invalid_queries_and_paths_rejected(tmp_path, query, path):
    (tmp_path / ".hidden").mkdir()
    with pytest.raises(ValueError):
        search.search_files(tmp_path, query, path)


def test_absolute_path_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        search.search_files(tmp_path, "needle", str(tmp_path))


def test_excluded_names_types_and_changed_snapshot(tmp_path):
    for name in [".hidden.md", "ignored.json"]:
        (tmp_path / name).write_text("needle", encoding="utf-8")
    dependency = tmp_path / "node_modules"
    dependency.mkdir()
    (dependency / "note.md").write_text("needle", encoding="utf-8")
    note = tmp_path / "note.rst"
    note.write_text("needle old", encoding="utf-8")
    first = search.search_files(tmp_path, "needle")
    assert len(first["matches"]) == 1 and first["complete"]
    note.write_text("needle new", encoding="utf-8")
    second = search.search_files(tmp_path, "needle")
    assert first["matches"][0]["sha256"] != second["matches"][0]["sha256"]


def test_long_line_quotes_around_match_without_inventing_text(tmp_path):
    line = "前" * 600 + "needle" + "后" * 600
    (tmp_path / "long.md").write_text(line, encoding="utf-8")
    hit = search.search_files(tmp_path, "needle")["matches"][0]
    assert "needle" in hit["quote"] and hit["quote"] in line
    assert len(hit["quote"]) <= search.MAX_QUOTE and hit["quote_truncated"]


def test_skipped_files_mark_incomplete_instead_of_global_no_result(tmp_path):
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe")
    (tmp_path / "binary.txt").write_bytes(b"needle\x00")
    (tmp_path / "large.txt").write_bytes(b"x" * (search.MAX_FILE_BYTES + 1))
    result = search.search_files(tmp_path, "needle")
    assert not result["complete"] and result["matches"] == []
    assert set(result["incomplete_reasons"]) == {
        "non_utf8_file",
        "binary_file",
        "oversized_file",
    }


def test_result_count_is_bounded(tmp_path):
    (tmp_path / "many.md").write_text("needle\n" * 20, encoding="utf-8")
    result = search.search_files(tmp_path, "needle")
    assert len(result["matches"]) == search.MAX_RESULTS
    assert not result["complete"] and "result_limit" in result["incomplete_reasons"]


def test_entry_and_file_budgets_are_bounded(tmp_path):
    for number in range(search.MAX_ENTRIES + 2):
        (tmp_path / f"{number:03}.md").write_text("other", encoding="utf-8")
    result = search.search_files(tmp_path, "needle")
    assert result["entries_seen"] == search.MAX_ENTRIES
    assert result["files_examined"] == search.MAX_FILES
    assert {"entry_limit", "file_limit"} <= set(result["incomplete_reasons"])


def test_total_read_bytes_are_bounded(tmp_path):
    for number in range(10):
        (tmp_path / f"{number}.md").write_bytes(b"x" * 30_000)
    result = search.search_files(tmp_path, "needle")
    assert result["bytes_read"] == search.MAX_BYTES
    assert "byte_limit" in result["incomplete_reasons"]


def test_directory_read_failure_is_explicit(tmp_path, monkeypatch):
    def denied(directory):
        """模拟权限变化，不能把目录读取失败当成搜索完成。"""
        raise PermissionError("not readable")

    monkeypatch.setattr(search.os, "scandir", denied)
    result = search.search_files(tmp_path, "needle")
    assert not result["complete"] and result["skipped"]["unreadable_directory"] == 1


def test_directory_link_is_not_followed(tmp_path):
    root, outside = tmp_path / "allowed", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.md").write_text("needle secret", encoding="utf-8")
    link = root / "link"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    result = search.search_files(root, "needle")
    assert not result["matches"] and result["skipped"]["link"] == 1
    with pytest.raises(ValueError):
        search.search_files(root, "needle", "link")
