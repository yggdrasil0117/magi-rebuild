from pathlib import Path

MAX_READ_BYTES = 32_768


def read_file(
    root: Path,
    path: str,
    max_bytes: int = MAX_READ_BYTES,
) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path 必须是非空字符串")

    relative_path = Path(path)

    if relative_path.is_absolute() or relative_path.drive:
        raise ValueError("path 必须是相对路径")

    root = root.resolve(strict=True)

    if not root.is_dir():
        raise ValueError("读取根目录必须是目录")

    # 解析 .. 和符号链接，再检查实际目标位置
    target = (root / relative_path).resolve()

    if not target.is_relative_to(root):
        raise ValueError("不允许读取根目录之外的文件")

    if not target.exists():
        raise ValueError("文件不存在")

    if not target.is_file():
        raise ValueError("目标必须是普通文件")

    # 最多读取上限加一个字节，用于判断是否超限
    with target.open("rb") as file:
        raw = file.read(max_bytes + 1)

    if len(raw) > max_bytes:
        raise ValueError("文件超过读取大小限制")

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("文件必须是 UTF-8 文本") from exc

    return content


def create_file_tools(root: Path):
    root = root.resolve(strict=True)

    if not root.is_dir():
        raise ValueError("读取根目录必须是目录")

    definitions = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "读取允许目录中的 UTF-8 文本文件。"
                    "path 必须是相对于该目录的路径。"
                    f"文件最多 {MAX_READ_BYTES} 字节。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": (
                    "列出允许目录中指定目录的直接子项，不递归。"
                    "根目录使用 .；空白字符串也表示根目录。"
                    "返回相对路径，最多允许 100 个目录条目。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        }
    ]

    def execute_file_tool(name: str, arguments: dict) -> str:
        if name not in ("read_file", "list_files"):
            raise ValueError(f"不支持的文件工具：{name}")

        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须是字典")

        if set(arguments) != {"path"}:
            raise ValueError("文件工具必须恰好包含 path 参数")

        if name == "read_file":
            return read_file(root=root, path=arguments["path"])

        return list_files(root=root, path=arguments["path"])

    return definitions, execute_file_tool


def list_files(root: Path, path: str = ".") -> str:
    if not isinstance(path, str):
        raise ValueError("path 必须是字符串")
    # 仅目录列表提供默认位置；不改变 read_file 的非空要求。
    if not path.strip():
        path = "."

    relative_path = Path(path)

    if relative_path.is_absolute() or relative_path.drive:
        raise ValueError("path 必须是相对路径")

    root = root.resolve(strict=True)
    target = (root / relative_path).resolve()

    if not target.is_relative_to(root):
        raise ValueError("不允许访问根目录之外的目录")

    if not target.exists():
        raise ValueError("目录不存在")

    if not target.is_dir():
        raise ValueError("目标必须是目录")

    entries = []
    for entry in target.iterdir():
        if len(entries) == 100:
            raise ValueError("目录条目超过 100 项，请指定更小的目录")
        entries.append(entry)
    entries.sort(key=lambda item: item.name.lower())

    lines = []

    for entry in entries:
        resolved_entry = entry.resolve()

        # 不展示实际指向允许范围外的链接
        if not resolved_entry.is_relative_to(root):
            continue

        relative_name = entry.relative_to(root).as_posix()

        if entry.is_dir():
            lines.append(f"目录：{relative_name}/")
        elif entry.is_file():
            lines.append(f"文件：{relative_name}")

    return "\n".join(lines) if lines else "目录为空或没有可展示条目"
