#!/usr/bin/env bash
# 在同一次 WSL 调用中保持数据库存活；参数是隔离源码目录与已有 Maven 缓存目录。
set -euo pipefail
audit_core="${1:?需要隔离 core-api 目录}"
audit_cache="${2:?需要 Maven 缓存目录}"
audit_container="magi-rag-audit-025-$$"
audit_created=0
cleanup() {
    # 只停止本脚本创建的容器，未创建成功时不操作同名对象。
    if [ "$audit_created" = 1 ]; then
        docker logs "$audit_container" > "$audit_core/../database-audit-$$.log" 2>&1 || true
        docker rm -f "$audit_container" >/dev/null
    fi
}
trap cleanup EXIT
docker run --detach --pull=never --name "$audit_container" --memory=384m --cpus=1 \
    -e POSTGRES_PASSWORD=memory-test-only pgvector/pgvector:pg17
audit_created=1
audit_ready=0
for audit_attempt in $(seq 1 20); do
    if docker exec "$audit_container" pg_isready -U postgres >/dev/null 2>&1; then
        audit_ready=1
        break
    fi
    sleep 1
done
if [ "$audit_ready" != 1 ]; then
    echo '隔离数据库未就绪；不执行测试。' >&2
    exit 1
fi
docker run --rm --pull=never --memory=768m --cpus=2 \
    --network "container:$audit_container" \
    --mount "type=bind,src=$audit_core,dst=/work" \
    --mount "type=bind,src=$audit_cache,dst=/m2" \
    -w /work -e YG_MEMORY_TEST_DATABASE_URL=jdbc:postgresql://localhost:5432/postgres \
    maven:3.9.11-eclipse-temurin-21 mvn -o -Dmaven.repo.local=/m2 \
    '-Dtest=RagReuseAuditTest,RagReuseDatabaseAuditTest,EvidenceRevocationIntegrationTest' test
