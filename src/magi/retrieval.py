"""本地资料版本、分块与关键词基线；不依赖外部服务和嵌入模型。"""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from magi.agent.search import scan_documents


def digest(text):
    """生成资料域稳定身份；不是对话、计划或操作回执ID。"""
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class RetrievalRequest:
    """query来自本轮问题，预算由程序固定，拒绝空或过长输入。"""

    query: str


class RetrievalProvider(Protocol):
    def retrieve(self, request: RetrievalRequest) -> dict:
        """返回候选或失败，不生成结论、不确认计划。"""
        ...


def chunks(text):
    """按原始行切为最多160字窗口；返回行号、行内起点与未经改写的原文。"""
    for number, line in enumerate(text.splitlines(), 1):
        for offset in range(0, len(line), 160):
            quote = line[offset : offset + 160]
            if quote.strip():
                yield number, offset, quote


def query_terms(query):
    """英文按词、中文按双字窗口建立可解释基线；不扩写同义词。"""
    terms = []
    for word in re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+", query.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", word) and len(word) > 1:
            terms.extend(word[i : i + 2] for i in range(len(word) - 1))
        else:
            terms.append(word)
    return list(dict.fromkeys(terms))[:40]


class MagiLocalRetrievalProvider:
    def __init__(self, root: Path, db_path: Path | None = None):
        """root来自授权配置；独立SQLite保存不可变版本，不复用业务schema编号。"""
        self.root = root
        self.db_path = db_path

    def retrieve(self, request: RetrievalRequest) -> dict:
        """每轮有界扫描当前文件，只检索本轮读到版本，旧版本仅供追溯。

        扫描不完整不会回退旧缓存冒充当前资料；索引事务失败整体返回FAILED。
        返回片段冻结到web_turn，确认计划引用该回合且不重新读文件。
        """
        if (
            not isinstance(request.query, str)
            or not request.query.strip()
            or len(request.query) > 8000
        ):
            raise ValueError("资料查询必须为1—8000字")
        terms = query_terms(request.query)
        try:
            scan = scan_documents(self.root)
            if self.db_path:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.db_path or ":memory:")
            try:
                with db:
                    db.executescript("""
                        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, path TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, document_id TEXT NOT NULL, sha256 TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, version_id TEXT NOT NULL, line INTEGER NOT NULL, offset INTEGER NOT NULL, quote TEXT NOT NULL);
                    """)
                    candidates = []
                    for document in scan["documents"]:
                        document_id = digest(
                            str(self.root.resolve()) + "/" + document["path"]
                        )
                        version_id = digest(document_id + ":" + document["sha256"])
                        db.execute(
                            "INSERT OR IGNORE INTO documents VALUES (?,?)",
                            (document_id, document["path"]),
                        )
                        db.execute(
                            "INSERT OR IGNORE INTO versions VALUES (?,?,?)",
                            (version_id, document_id, document["sha256"]),
                        )
                        for line, offset, quote in chunks(document["text"]):
                            chunk_id = digest(f"{version_id}:{line}:{offset}")
                            db.execute(
                                "INSERT OR IGNORE INTO chunks VALUES (?,?,?,?,?)",
                                (chunk_id, version_id, line, offset, quote),
                            )
                            hits = [term for term in terms if term in quote.lower()]
                            if not hits:
                                continue
                            candidates.append(
                                dict(
                                    document_id=document_id,
                                    version_id=version_id,
                                    chunk_id=chunk_id,
                                    evidence_ref="document:" + chunk_id,
                                    path=document["path"],
                                    line=line,
                                    offset=offset,
                                    quote=quote,
                                    sha256=document["sha256"],
                                    score=len(hits),
                                    matched_terms=hits,
                                )
                            )
                    candidates.sort(
                        key=lambda m: (-m["score"], m["path"], m["line"], m["offset"])
                    )
            finally:
                db.close()
            return dict(
                status="FOUND" if candidates else "NO_RESULTS",
                matches=candidates[:5],
                method="bounded keyword overlap (Chinese bigrams)",
                query_terms=terms,
                complete=scan["complete"] and len(candidates) <= 5,
                coverage={
                    k: v
                    for k, v in scan.items()
                    if k not in {"documents", "matches", "query"}
                },
                truncated=len(candidates) > 5,
                notice="仅授权文本范围；最多五条160字片段。字词重合不证明资料支持结论；无结果不等于没有答案。",
            )
        except (OSError, ValueError, sqlite3.Error):
            return dict(
                status="FAILED",
                matches=[],
                complete=False,
                method="bounded keywords",
                notice="资料读取或索引失败，本轮没有可用资料证据；未使用旧缓存。",
            )
