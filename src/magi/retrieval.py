"""本地资料版本与分块；支持独立关键词基线及用户显式启用的云端向量。"""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from magi.agent.search import scan_documents
from magi.embedding import EmbeddingError, vector_rank


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


CHUNK_POLICY = "line160-overlap32-v2"


def chunks(text):
    """每行160字窗口、相邻重叠32字；原文及位置不改写，不产生已被覆盖的短尾块。"""
    for number, line in enumerate(text.splitlines(), 1):
        for offset in range(0, len(line), 128):
            quote = line[offset : offset + 160]
            if quote.strip():
                yield number, offset, quote
            if offset + 160 >= len(line):
                break


def distinct_matches(candidates, limit=5):
    """按已有排名保留同版本内不重复/不包含的原文，跨文档相同文字仍各保留来源。

    candidates已按检索分数排序。返回有界候选及去重计数；不合并原文或生成新证据。
    """
    unique = []
    removed = 0
    for item in candidates:
        if any(
            item["version_id"] == chosen["version_id"]
            and item["quote"] in chosen["quote"]
            for chosen in unique
        ):
            removed += 1
        else:
            unique.append(item)
            if len(unique) > limit:
                return unique[:limit], removed, True
    return unique, removed, False


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
    def __init__(
        self, root: Path, db_path: Path | None = None, *, embedding=None, min_score=0.45
    ):
        """root来自授权配置；embedding可选且由程序配置注入，min_score为余弦阈值。

        独立SQLite保存不可变版本，不复用业务schema编号。"""
        self.embedding = embedding
        self.min_score = min_score
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
        method = "bounded keyword overlap (Chinese bigrams)"
        embedding_state = {"enabled": self.embedding is not None, "degraded": False}
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
                            chunk_id = digest(
                                f"{CHUNK_POLICY}:{version_id}:{line}:{offset}"
                            )
                            db.execute(
                                "INSERT OR IGNORE INTO chunks VALUES (?,?,?,?,?)",
                                (chunk_id, version_id, line, offset, quote),
                            )
                            hits = [term for term in terms if term in quote.lower()]
                            if not hits and self.embedding is None:
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
                    if self.embedding is not None:
                        calls_before = getattr(self.embedding, "calls", 0)
                        tokens_before = getattr(self.embedding, "tokens", 0)
                        try:
                            # 保存点只回滚向量缓存，不撤销本轮已读取的文档版本记录。
                            db.execute("SAVEPOINT vector_cache")
                            candidates, diagnostics = vector_rank(
                                db,
                                candidates,
                                request.query,
                                self.embedding,
                                self.min_score,
                            )
                            db.execute("RELEASE vector_cache")
                            embedding_state.update(
                                diagnostics, provider=self.embedding.identity
                            )
                            method = "dense cosine BAAI/bge-m3"
                        except EmbeddingError as exc:
                            db.execute("ROLLBACK TO vector_cache")
                            db.execute("RELEASE vector_cache")
                            embedding_state.update(degraded=True, error_code=str(exc))
                            candidates = [
                                item for item in candidates if item["score"] > 0
                            ]
                        embedding_state.update(
                            calls=getattr(self.embedding, "calls", 0) - calls_before,
                            known_tokens=getattr(self.embedding, "tokens", 0)
                            - tokens_before,
                        )
                    candidates.sort(
                        key=lambda m: (-m["score"], m["path"], m["line"], m["offset"])
                    )
            finally:
                db.close()
            matches, deduplicated, truncated = distinct_matches(candidates)
            return dict(
                status="FOUND" if candidates else "NO_RESULTS",
                matches=matches,
                chunk_policy=CHUNK_POLICY,
                deduplicated=deduplicated,
                method=method,
                embedding=embedding_state,
                query_terms=terms,
                complete=scan["complete"]
                and not truncated
                and not embedding_state.get("pool_truncated", False)
                and not embedding_state.get("query_truncated", False),
                coverage={
                    k: v
                    for k, v in scan.items()
                    if k not in {"documents", "matches", "query"}
                },
                truncated=truncated,
                notice=(
                    "向量服务失败，已明确退回关键词检索。"
                    if embedding_state["degraded"]
                    else ""
                )
                + (
                    "已使用云端BGE-M3语义相似度检索。"
                    if method.startswith("dense")
                    else ""
                )
                + "仅授权文本范围；最多五条160字片段。相似度或字词重合不证明资料支持结论；无结果不等于没有答案。",
            )
        except (OSError, ValueError, sqlite3.Error):
            return dict(
                status="FAILED",
                matches=[],
                complete=False,
                method="bounded keywords",
                notice="资料读取或索引失败，本轮没有可用资料证据；未使用旧缓存。",
            )
