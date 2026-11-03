"""以 SQLite 当前有效版本为准，按需补齐独立的本地 Qdrant 索引。"""

from uuid import NAMESPACE_URL, uuid5

from magi.embedding import SiliconFlowEmbeddingProvider


def recall(context, logical, question, settings):
    """只对已授权范围检索；缺失的版本下次请求可以重新索引。"""
    try:
        from qdrant_client import QdrantClient, models

        provider = SiliconFlowEmbeddingProvider()
        collection = "confirmed_memory_bge_m3_v1"
        binding, rows = context.build_current_context(logical, include_identity=True)
        ids = {
            str(uuid5(NAMESPACE_URL, f"{row['id']}:{row['version']}")): row
            for row in rows
        }
        with context.store.connect() as db:
            jobs = db.execute(
                """SELECT p.memory_id, p.version, m.status
                   FROM memory_index_pending p JOIN memories m ON m.id=p.memory_id
                   WHERE m.status='DELETED' OR m.scope IN ('global', ?)
                   ORDER BY p.memory_id LIMIT 128""",
                (f"session:{logical}",),
            ).fetchall()
        if not ids and not jobs:
            return {"status": "OK", "items": []}
        client = QdrantClient(
            path=str(settings.business_db_path.with_suffix(".memory-index"))
        )
        try:
            if not client.collection_exists(collection):
                client.create_collection(
                    collection,
                    vectors_config=models.VectorParams(
                        size=1024, distance=models.Distance.COSINE
                    ),
                )
            present = (
                {str(p.id) for p in client.retrieve(collection, list(ids))}
                if ids
                else set()
            )
            missing = [(key, row) for key, row in ids.items() if key not in present][
                :31
            ]
            vectors = (
                provider.embed(
                    [question[:2000]]
                    + [f"{row['key']}: {row['value']}"[:2000] for _, row in missing]
                )
                if ids
                else []
            )
            if missing:
                client.upsert(
                    collection,
                    points=[
                        models.PointStruct(
                            id=key,
                            vector=vector,
                            payload={"memory_id": row["id"], "version": row["version"]},
                        )
                        for (key, row), vector in zip(missing, vectors[1:], strict=True)
                    ],
                    wait=True,
                )
            indexed = present | {key for key, _ in missing}
            points = (
                client.query_points(
                    collection,
                    query=vectors[0],
                    limit=5,
                    query_filter=models.Filter(
                        must=[models.HasIdCondition(has_id=list(ids))]
                    ),
                    score_threshold=settings.embedding_min_score,
                ).points
                if ids
                else []
            )
            for job in jobs:
                job_id, version, status = job
                point_id = str(uuid5(NAMESPACE_URL, f"{job_id}:{version}"))
                if status != "DELETED" and point_id not in indexed:
                    continue
                # 只删除比本次工作版本早的向量，迟到任务不能删除新版。
                client.delete(
                    collection,
                    points_selector=models.FilterSelector(
                        filter=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="memory_id",
                                    match=models.MatchValue(value=job_id),
                                ),
                                models.FieldCondition(
                                    key="version", range=models.Range(lt=version)
                                ),
                            ]
                        )
                    ),
                    wait=True,
                )
                with context.store.transaction() as db:
                    db.execute(
                        "DELETE FROM memory_index_pending WHERE memory_id=? AND version=?",
                        (job_id, version),
                    )
        finally:
            client.close()
        current, latest = context.build_current_context(logical, include_identity=True)
        if current != binding:
            return {"status": "CONTEXT_CHANGED", "items": []}
        valid = {(row["id"], row["version"]): row for row in latest}
        items = []
        for point in points:
            row = ids[str(point.id)]
            checked = valid.get((row["id"], row["version"]))
            if checked:
                items.append(checked)
        return {"status": "OK", "items": items}
    except Exception:
        return {"status": "UNAVAILABLE", "items": []}
