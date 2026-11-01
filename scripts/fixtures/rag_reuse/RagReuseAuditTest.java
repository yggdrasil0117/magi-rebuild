package com.yggdrasil.knowledge;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import java.time.Instant;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

/** 在隔离副本中刻画复用限制；通过表示缺口已复现，不表示缺口已修复。 */
class RagReuseAuditTest {
    @Test
    void embeddingFailurePreventsEvenLexicalRepositoryAccess() {
        var repository = mock(RetrievalRepository.class);
        var authorization = mock(SourceAuthorizationService.class);
        var embedding = mock(EmbeddingProvider.class);
        var reader = new TenantContext(UUID.randomUUID(), UUID.randomUUID(), "audit-reader");
        when(authorization.requireRead(reader)).thenReturn("internal");
        when(embedding.embed(anyString())).thenThrow(new IllegalStateException("synthetic embedding outage"));
        var service = new RetrievalService(repository, authorization, embedding);

        // 词法查询与向量查询共用入口；向量失败发生在任何 SQL 调用之前。
        assertThatThrownBy(() -> service.search(reader,
                new RetrievalService.SearchRequest("报销额度", 3)))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("synthetic embedding outage");
        verifyNoInteractions(repository);
    }

    @Test
    void frozenEvidenceIsNotARelevanceVerdictAndLosesScoringProvenance() {
        var retrieval = mock(RetrievalService.class);
        var authorization = mock(SourceAuthorizationService.class);
        var repository = mock(EvidencePackageRepository.class);
        var membership = mock(WorkspaceMembershipRepository.class);
        var reader = new TenantContext(UUID.randomUUID(), UUID.randomUUID(), "audit-reader");
        var now = Instant.parse("2026-11-01T00:00:00Z");
        var result = new RetrievalResult(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID(),
                UUID.randomUUID(), 0, "合成资料：办公地点。", "section:1", "a".repeat(64),
                "internal", now, now, 0.000001);
        when(retrieval.search(eq(reader), any())).thenReturn(new RetrievalService.SearchResponse(
                List.of(result), false, "audit-embedding", "audit-reranker", false, 1, 1));
        when(authorization.requireRead(reader)).thenReturn("internal");
        when(membership.currentPolicyRevision(reader)).thenReturn(1L);
        var service = new EvidencePackageService(retrieval, authorization, repository, membership);

        var frozen = service.freeze(reader, new EvidencePackageService.FreezeRequest("如何报销？", 3, "answer"));
        assertThat(frozen.lifecycleState()).isEqualTo("FROZEN");
        assertThat(frozen.items()).singleElement().satisfies(item -> {
            assertThat(item.exactQuote()).isEqualTo(result.text());
            assertThat(item.keywordScore()).isEqualTo(0.000001);
        });
        verify(repository).insert(frozen);
        // 冻结对象没有保留实际 provider/精排降级标志，下游不能统一套用一个分数阈值。
        assertThat(Arrays.stream(EvidencePackage.class.getRecordComponents()).map(c -> c.getName()).toList())
                .doesNotContain("embeddingProvider", "rerankerModel", "rerankerApplied");
    }
}
