package com.yggdrasil.knowledge;

import static org.assertj.core.api.Assertions.*;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

/** 仅对独立空测试库运行真实迁移和 SQL，不使用用户知识库或本地模型。 */
@EnabledIfEnvironmentVariable(named = "YG_MEMORY_TEST_DATABASE_URL", matches = ".+")
class RagReuseDatabaseAuditTest {
    private static JdbcTemplate jdbc;

    @BeforeAll
    static void migrate() {
        var data = new DriverManagerDataSource(System.getenv("YG_MEMORY_TEST_DATABASE_URL"),
                "postgres", "memory-test-only");
        Flyway.configure().dataSource(data).load().migrate();
        jdbc = new JdbcTemplate(data);
    }

    @Test
    void sourceScopedSearchReturnsBothVersionsUnlessCallerChoosesAVersionPolicy() throws Exception {
        UUID org = UUID.randomUUID(), workspace = UUID.randomUUID(), source = UUID.randomUUID();
        UUID document = UUID.randomUUID(), older = UUID.randomUUID(), newer = UUID.randomUUID();
        var reader = new TenantContext(org, workspace, "audit-reader");
        jdbc.update("INSERT INTO organizations(id,slug,display_name) VALUES (?,?,'Audit')", org, org.toString());
        jdbc.update("INSERT INTO workspaces(id,organization_id,slug,display_name) VALUES (?,?,?,'Audit')",
                workspace, org, workspace.toString());
        jdbc.update("INSERT INTO sources(id,organization_id,workspace_id,connector_type,exact_location,owner_subject,classification_ceiling) VALUES (?,?,?,'file',?,'audit-reader','internal')",
                source, org, workspace, "fixture:" + source);
        jdbc.update("INSERT INTO documents(id,organization_id,workspace_id,source_id,stable_identity,owner_subject,classification) VALUES (?,?,?,?,?,'audit-reader','internal')",
                document, org, workspace, source, document.toString());
        insertVersion(reader, source, document, older, "v1", "住宿报销额度为600元。", "2026-01-01T00:00:00Z");
        insertVersion(reader, source, document, newer, "v2", "住宿报销额度为800元。", "2026-02-01T00:00:00Z");
        var repository = new RetrievalRepository(new NamedParameterJdbcTemplate(jdbc));
        var candidates = repository.candidates(reader, "住宿报销额度", "住宿\n报销\n额度", "fixture",
                "[1,0]", List.of("internal"), List.of(source), 20);
        var items = repository.materialize(reader, "住宿报销额度", "住宿\n报销\n额度", "fixture",
                "[1,0]", List.of("internal"), List.of(source),
                candidates.stream().map(RetrievalRepository.Candidate::chunkId).toList());

        // 返回旧版可以服务历史比较；但若调用方要求“当前规则”，不能将其误当最新版本检索。
        assertThat(items).extracting(RetrievalResult::documentVersionId).containsExactlyInAnyOrder(older, newer);
        assertThat(items).extracting(RetrievalResult::text)
                .containsExactlyInAnyOrder("住宿报销额度为600元。", "住宿报销额度为800元。");
    }

    /** 为同一文档建立两个可追溯版本；正文摘要真实计算，不建立向量以单独验证词法 SQL。 */
    private void insertVersion(TenantContext reader, UUID source, UUID document, UUID version,
                               String label, String content, String published) throws Exception {
        UUID artifact = UUID.randomUUID();
        String digest = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                .digest(content.getBytes(StandardCharsets.UTF_8)));
        jdbc.update("INSERT INTO source_artifacts(id,organization_id,workspace_id,source_id,upstream_version,content_digest,media_type,size_bytes,acquired_at,object_key) VALUES (?,?,?,?,?,?,'text/plain',?,CURRENT_TIMESTAMP,?)",
                artifact, reader.organizationId(), reader.workspaceId(), source, label, digest,
                content.getBytes(StandardCharsets.UTF_8).length,
                reader.organizationId() + "/" + reader.workspaceId() + "/" + source + "/" + digest);
        jdbc.update("INSERT INTO document_versions(id,organization_id,workspace_id,source_id,document_id,artifact_id,upstream_version,artifact_digest,parser_version,normalized_digest,effective_classification,published_at) VALUES (?,?,?,?,?,?,?,?,'fixture',?,'internal',CAST(? AS timestamptz))",
                version, reader.organizationId(), reader.workspaceId(), source, document, artifact,
                label, digest, digest, published);
        jdbc.update("INSERT INTO document_chunks(id,organization_id,workspace_id,source_id,document_id,document_version_id,ordinal,content,retrieval_text,structural_path,start_offset,end_offset,language,content_digest,effective_classification) VALUES (?,?,?,?,?,?,0,?,?,'section',0,?,'zh',?,'internal')",
                UUID.randomUUID(), reader.organizationId(), reader.workspaceId(), source, document, version,
                content, content, content.length(), digest);
    }
}
