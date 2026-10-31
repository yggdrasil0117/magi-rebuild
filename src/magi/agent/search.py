"""授权资料目录内的有界字面检索；不建立索引、不调用网络或模型。"""

import hashlib
import os
from collections import deque
from pathlib import Path

MAX_ENTRIES = 200
MAX_FILES = 40
MAX_BYTES = 262_144
MAX_FILE_BYTES = 32_768
MAX_RESULTS = 8
MAX_QUOTE = 240
SUFFIXES = frozenset({".md", ".txt", ".rst"})
EXCLUDED_NAMES = frozenset({"node_modules", "__pycache__"})


def search_files(root: Path, query: str, path: str = ".") -> dict:
    """按相对目录检索一个字面词，返回原文定位、快照摘要和有界覆盖说明。

    未检索的文件必须通过计数与complete标志暴露，避免将局部无结果说成全局不存在。
    根目录来自配置，调用方不能传绝对路径或扩大预算。
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 80:
        raise ValueError("query必须是1—80字的非空查询词")
    if any(character in query for character in "\r\n\x00"):
        raise ValueError("query不能包含换行或空字符")
    if not isinstance(path, str) or not path.strip() or len(path) > 240:
        raise ValueError("path必须是非空相对目录")
    relative = Path(path)
    if relative.is_absolute() or relative.drive:
        raise ValueError("path必须是相对目录")
    root = root.resolve(strict=True)
    target = (root / relative).resolve()
    if not root.is_dir() or not target.is_relative_to(root):
        raise ValueError("不能检索授权根目录之外的资料")
    if not target.is_dir():
        raise ValueError("检索目标必须是已存在的目录")
    # 指定路径也不得经链接进入；resolve后的范围检查并不能拒绝范围内链接。
    cursor = root
    for part in relative.parts:
        if (
            part.startswith(".") and part not in {".", ".."}
        ) or part.lower() in EXCLUDED_NAMES:
            raise ValueError("检索范围排除隐藏目录和依赖目录")
        cursor = cursor / part
        if cursor.is_symlink() or cursor.is_junction():
            raise ValueError("检索目录不能经过链接")

    matches, reasons, skipped = [], set(), {}
    queue = deque([target])
    entries_seen = files_examined = bytes_read = 0

    def skip(reason, *, incomplete=False):
        """记录有意排除与读取失败；后者会使本次搜索覆盖不完整。"""
        skipped[reason] = skipped.get(reason, 0) + 1
        if incomplete:
            reasons.add(reason)

    def result():
        """只返回授权根目录下的定位，不暴露绝对路径或异常正文。"""
        return {
            "query": query,
            "path": target.relative_to(root).as_posix(),
            "match_mode": "case-sensitive literal substring",
            "scope": "仅.md/.txt/.rst；排除隐藏项、依赖目录及链接，不搜索互联网",
            "matches": matches,
            "complete": not reasons,
            "incomplete_reasons": sorted(reasons),
            "skipped": skipped,
            "entries_seen": entries_seen,
            "files_examined": files_examined,
            "bytes_read": bytes_read,
            "limits": {
                "entries": MAX_ENTRIES,
                "files": MAX_FILES,
                "bytes": MAX_BYTES,
                "results": MAX_RESULTS,
            },
            "notice": "片段仅证明本次读到这些文字，不保证结论正确或资料仍适用；无结果不代表所有资料没有答案。",
        }

    while queue:
        directory = queue.popleft()
        children, entry_limit = [], False
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if entries_seen == MAX_ENTRIES:
                        reasons.add("entry_limit")
                        entry_limit = True
                        break
                    entries_seen += 1
                    children.append(Path(entry.path))
        except OSError:
            skip("unreadable_directory", incomplete=True)
            continue
        for child in sorted(children, key=lambda value: value.name):
            try:
                if child.name.startswith(".") or child.name.lower() in EXCLUDED_NAMES:
                    skip("excluded_name")
                    continue
                if child.is_symlink() or child.is_junction():
                    skip("link")
                    continue
                actual = child.resolve()
                if not actual.is_relative_to(root):
                    skip("outside_root")
                    continue
                if child.is_dir():
                    queue.append(child)
                    continue
                if not child.is_file() or child.suffix.lower() not in SUFFIXES:
                    skip("excluded_type")
                    continue
                if files_examined == MAX_FILES:
                    reasons.add("file_limit")
                    return result()
                files_examined += 1
                if child.stat().st_size > MAX_FILE_BYTES:
                    skip("oversized_file", incomplete=True)
                    continue
                remaining = MAX_BYTES - bytes_read
                if remaining <= 0:
                    reasons.add("byte_limit")
                    return result()
                with child.open("rb") as handle:
                    raw = handle.read(min(MAX_FILE_BYTES + 1, remaining))
                bytes_read += len(raw)
                # 文件可能在stat后增长；不能把读到的前缀当成完整文件摘要。
                if len(raw) > MAX_FILE_BYTES:
                    skip("oversized_file", incomplete=True)
                    continue
                if len(raw) == remaining:
                    reasons.add("byte_limit")
                    return result()
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    skip("non_utf8_file", incomplete=True)
                    continue
                if "\x00" in text:
                    skip("binary_file", incomplete=True)
                    continue
                digest = hashlib.sha256(raw).hexdigest()
                for number, line in enumerate(text.splitlines(), 1):
                    offset = line.find(query)
                    if offset < 0:
                        continue
                    start = max(0, offset - 60)
                    quote = line[start : start + MAX_QUOTE]
                    matches.append(
                        {
                            "path": child.relative_to(root).as_posix(),
                            "line": number,
                            "quote": quote,
                            "quote_truncated": start > 0
                            or start + len(quote) < len(line),
                            "sha256": digest,
                        }
                    )
                    if len(matches) == MAX_RESULTS:
                        reasons.add("result_limit")
                        return result()
            except OSError:
                skip("unreadable_file", incomplete=True)
        if entry_limit:
            return result()
    return result()
