"""硅基流动稠密向量适配；不启动本地模型，不记录凭据或响应正文。"""

import json
import math
import os
from typing import Protocol

import httpx


class EmbeddingError(Exception):
    """只携带稳定错误码，避免异常正文泄漏请求头和资料。"""


class EmbeddingProvider(Protocol):
    identity: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """将受预算约束的文本转为同维度向量，顺序与输入一致。"""
        ...


def normalize(vector, dimension=1024):
    """校验维度、有限数与非零长度并归一化，缓存与响应使用同一边界。"""
    if not isinstance(vector, list) or len(vector) != dimension:
        raise EmbeddingError("INVALID_VECTOR")
    if any(
        type(v) not in (int, float) or abs(v) > 1e100 or not math.isfinite(v)
        for v in vector
    ):
        raise EmbeddingError("INVALID_VECTOR")
    norm = math.sqrt(sum(v * v for v in vector))
    if not math.isfinite(norm) or norm == 0:
        raise EmbeddingError("INVALID_VECTOR")
    return [v / norm for v in vector]


class SiliconFlowEmbeddingProvider:
    identity = "https://api.siliconflow.cn/v1|BAAI/bge-m3|dense1024|v1"

    def __init__(self, client=None):
        """client仅供离线注入；真实凭据仅从SILICONFLOW_API_KEY读取。"""
        self.client = client
        self.calls = 0
        self.tokens = 0

    def embed(self, texts):
        """每批最多32项、每项最多2000字符，不自动重试；返回1024维单位向量。"""
        if not 1 <= len(texts) <= 32 or any(
            not t.strip() or len(t) > 2000 for t in texts
        ):
            raise EmbeddingError("INPUT_BUDGET")
        key = os.getenv("SILICONFLOW_API_KEY", "").strip()
        if not key:
            raise EmbeddingError("MISSING_API_KEY")
        owned = self.client is None
        client = self.client or httpx.Client(timeout=10, trust_env=False)
        try:
            self.calls += 1
            response = client.post(
                "https://api.siliconflow.cn/v1/embeddings",
                headers={"Authorization": "Bearer " + key},
                json={
                    "model": "BAAI/bge-m3",
                    "input": texts,
                    "encoding_format": "float",
                },
            )
            if response.status_code != 200:
                raise EmbeddingError(
                    {
                        401: "AUTHENTICATION_FAILED",
                        403: "ACCESS_DENIED",
                        429: "RATE_LIMITED",
                    }.get(response.status_code, "HTTP_FAILURE")
                )
            data = response.json()
            if not isinstance(data, dict):
                raise EmbeddingError("INVALID_RESPONSE")
            if data.get("model", "BAAI/bge-m3") != "BAAI/bge-m3":
                raise EmbeddingError("MODEL_MISMATCH")
            rows = data["data"]
            if not isinstance(rows, list) or any(
                not isinstance(r, dict) or type(r.get("index")) is not int for r in rows
            ):
                raise EmbeddingError("INVALID_RESPONSE")
            if len(rows) != len(texts) or {r["index"] for r in rows} != set(
                range(len(texts))
            ):
                raise EmbeddingError("INVALID_RESPONSE")
            rows = sorted(rows, key=lambda r: r["index"])
            vectors = [normalize(row["embedding"]) for row in rows]
            usage = (
                (data.get("usage") or {}).get("total_tokens")
                if isinstance(data.get("usage"), dict)
                else None
            )
            if type(usage) is int and usage >= 0:
                self.tokens += usage
            return vectors
        except httpx.TimeoutException:
            raise EmbeddingError("TIMEOUT") from None
        except httpx.HTTPError:
            raise EmbeddingError("NETWORK_FAILURE") from None
        except (ValueError, KeyError, TypeError, OverflowError):
            raise EmbeddingError("INVALID_RESPONSE") from None
        finally:
            if owned:
                client.close()


def vector_rank(db, candidates, query, provider, min_score):
    """只向量化当前扫描前128块；按provider+chunk缓存，不查询历史块。

    db来自本轮资料索引连接。只保存完整校验成功的一轮新向量；异常交给调用方显式降级。
    小规模直接计算余弦，不引入向量数据库。min_score是可配置经验阈值，不是事实概率。
    """
    db.execute(
        "CREATE TABLE IF NOT EXISTS embeddings(provider TEXT NOT NULL, chunk_id TEXT NOT NULL, vector TEXT NOT NULL, PRIMARY KEY(provider,chunk_id))"
    )
    pool = candidates[:128]
    if not pool:
        return [], dict(calls=0, tokens=0, cache_hits=0, pool_truncated=False)
    query_vector = normalize(provider.embed([query[:2000]])[0])
    vectors, missing = {}, []
    for item in pool:
        row = db.execute(
            "SELECT vector FROM embeddings WHERE provider=? AND chunk_id=?",
            (provider.identity, item["chunk_id"]),
        ).fetchone()
        if row:
            try:
                vectors[item["chunk_id"]] = normalize(json.loads(row[0]))
            except (ValueError, EmbeddingError):
                missing.append(item)
        else:
            missing.append(item)
    for start in range(0, len(missing), 32):
        batch = missing[start : start + 32]
        generated = provider.embed([item["quote"] for item in batch])
        if len(generated) != len(batch):
            raise EmbeddingError("INVALID_RESPONSE")
        for item, vector in zip(batch, generated, strict=True):
            vectors[item["chunk_id"]] = normalize(vector)
    for item in missing:
        db.execute(
            "INSERT OR REPLACE INTO embeddings VALUES (?,?,?)",
            (
                provider.identity,
                item["chunk_id"],
                json.dumps(vectors[item["chunk_id"]]),
            ),
        )
    ranked = []
    for item in pool:
        score = sum(
            a * b for a, b in zip(query_vector, vectors[item["chunk_id"]], strict=True)
        )
        if score >= min_score:
            ranked.append(
                dict(item, keyword_score=item["score"], score=round(score, 6))
            )
    ranked.sort(key=lambda m: (-m["score"], m["path"], m["line"], m["offset"]))
    return ranked, dict(
        cache_hits=len(pool) - len(missing),
        embedded_chunks=len(missing),
        pool_truncated=len(candidates) > 128,
        query_truncated=len(query) > 2000,
    )
