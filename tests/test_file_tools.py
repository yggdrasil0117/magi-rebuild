import pytest

from magi.agent.file_tools import create_file_tools, read_file, list_files, MAX_READ_BYTES


def test_read_utf8_file(tmp_path):
    (tmp_path / "note.txt").write_text("MAGI 文件测试", encoding="utf-8")

    assert read_file(tmp_path, "note.txt") == "MAGI 文件测试"


def test_reject_path_outside_root(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("外部内容", encoding="utf-8")

    with pytest.raises(ValueError, match="根目录之外"):
        read_file(root, "../outside.txt")


def test_reject_absolute_path(tmp_path):
    target = tmp_path / "note.txt"

    with pytest.raises(ValueError, match="相对路径"):
        read_file(tmp_path, str(target))


def test_reject_missing_file(tmp_path):
    with pytest.raises(ValueError, match="文件不存在"):
        read_file(tmp_path, "missing.txt")


def test_reject_directory(tmp_path):
    with pytest.raises(ValueError, match="普通文件"):
        read_file(tmp_path, ".")


def test_reject_oversized_file(tmp_path):
    (tmp_path / "large.txt").write_bytes(
        b"x" * (MAX_READ_BYTES + 1)
    )

    with pytest.raises(ValueError, match="大小限制"):
        read_file(tmp_path, "large.txt")


def test_reject_non_utf8_file(tmp_path):
    (tmp_path / "binary.dat").write_bytes(b"\xff\xfe")

    with pytest.raises(ValueError, match="UTF-8"):
        read_file(tmp_path, "binary.dat")


def test_execute_file_tool(tmp_path):
    (tmp_path / "note.txt").write_text("测试内容", encoding="utf-8")
    definitions, execute = create_file_tools(tmp_path)

    assert definitions[0]["function"]["name"] == "read_file"
    assert execute("read_file", {"path": "note.txt"}) == "测试内容"


@pytest.mark.parametrize(
    "name, arguments, error",
    [
        ("unknown", {"path": "note.txt"}, "不支持"),
        ("read_file", None, "字典"),
        ("read_file", {}, "恰好包含"),
        ("read_file", {"path": 123}, "非空字符串"),
    ],
)
def test_reject_invalid_call(tmp_path, name, arguments, error):
    _, execute = create_file_tools(tmp_path)

    with pytest.raises(ValueError, match=error):
        execute(name, arguments)


@pytest.mark.parametrize("path", ["", "   ", "."])
def test_list_empty_path_means_root(tmp_path, path):
    (tmp_path / "note.txt").write_text("内容", encoding="utf-8")
    _, execute = create_file_tools(tmp_path)
    assert execute("list_files", {"path": path}) == "文件：note.txt"


def test_read_still_rejects_empty_path(tmp_path):
    _, execute = create_file_tools(tmp_path)
    with pytest.raises(ValueError, match="非空字符串"):
        execute("read_file", {"path": ""})


def test_list_files(tmp_path):
    (tmp_path / "note.txt").write_text("内容", encoding="utf-8")
    (tmp_path / "subdir").mkdir()

    result = list_files(tmp_path)

    assert "文件：note.txt" in result
    assert "目录：subdir/" in result


def test_list_nested_directory(tmp_path):
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "note.txt").write_text("内容", encoding="utf-8")

    assert list_files(tmp_path, "subdir") == "文件：subdir/note.txt"


def test_list_rejects_outside_root(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()

    with pytest.raises(ValueError, match="根目录之外"):
        list_files(root, "..")


def test_list_rejects_too_many_entries(tmp_path):
    for number in range(101):
        (tmp_path / f"{number}.txt").touch()

    with pytest.raises(ValueError, match="超过 100"):
        list_files(tmp_path)


def test_list_tool_can_be_executed(tmp_path):
    (tmp_path / "note.txt").write_text("内容", encoding="utf-8")
    _, execute = create_file_tools(tmp_path)

    assert execute("list_files", {"path": "."}) == "文件：note.txt"


def test_read_file_at_size_limit(tmp_path):
    content = "x" * MAX_READ_BYTES
    (tmp_path / "limit.txt").write_text(content, encoding="utf-8")

    assert read_file(tmp_path, "limit.txt") == content
