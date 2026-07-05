"""T-ATTACH-R.1b — Container exposes startup-sweep fields and wires TaskiqDispatcher.

Verifies that:
1. Container dataclass declares pending_stale_seconds, uploaded_stale_seconds,
   max_attempts, and dispatcher fields.
2. Default values match the spec (300 / 300 / 5 / None).
3. build_container() constructs a TaskiqDispatcher wrapping the module broker.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest


def test_container_has_startup_sweep_fields() -> None:
    """Container must declare all startup-sweep and maintenance-loop fields."""
    from ragent.bootstrap.composition import Container

    field_names = {f.name for f in dataclasses.fields(Container)}
    for name in (
        "pending_stale_seconds",
        "uploaded_stale_seconds",
        "deleting_stale_seconds",
        "max_attempts",
        "maintenance_interval_seconds",
        "dispatcher",
    ):
        assert name in field_names, f"Container must have {name} field"


def test_container_startup_sweep_defaults() -> None:
    """Default values for startup-sweep / maintenance-loop thresholds must match spec."""
    from ragent.bootstrap.composition import Container

    assert Container.pending_stale_seconds == 300
    assert Container.uploaded_stale_seconds == 300
    assert Container.deleting_stale_seconds == 300
    assert Container.max_attempts == 5
    assert Container.maintenance_interval_seconds == 300
    assert Container.dispatcher is None


@pytest.fixture()
def _composition_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Minimum env for build_container() to reach the dispatcher wiring site."""
    monkeypatch.setenv("RAGENT_AUTH_MODE", "user_header")
    monkeypatch.setenv("MARIADB_DSN", "mysql+aiomysql://u:p@h:3306/db")
    monkeypatch.setenv("AI_API_AUTH_URL", "http://auth.example/token")
    monkeypatch.setenv("AI_LLM_API_J1_TOKEN", "j1-llm")
    monkeypatch.setenv("AI_EMBEDDING_API_J1_TOKEN", "j1-emb")
    monkeypatch.setenv("AI_RERANK_API_J1_TOKEN", "j1-rerank")
    monkeypatch.setenv("EMBEDDING_API_URL", "http://emb.example")
    monkeypatch.setenv("LLM_API_URL", "http://llm.example")
    monkeypatch.setenv("RERANK_API_URL", "http://rerank.example")
    monkeypatch.setenv("ES_HOSTS", "http://es.example:9200")
    monkeypatch.setenv(
        "MINIO_SITES",
        (
            '[{"name":"__default__","endpoint":"minio.example:9000",'
            '"access_key":"ak","secret_key":"example_minio_secret_not_real",'
            '"bucket":"b"}]'
        ),  # pragma: allowlist secret
    )


def test_build_container_wires_taskiq_dispatcher(
    _composition_env: None,
) -> None:
    """build_container() must set container.dispatcher to a TaskiqDispatcher."""
    from ragent.bootstrap.dispatcher import TaskiqDispatcher

    with (
        patch("ragent.bootstrap.init_schema.patch_aiomysql_ping"),
        patch("sqlalchemy.ext.asyncio.create_async_engine", MagicMock()),
        patch("ragent.clients.embedding.EmbeddingClient", MagicMock()),
        patch("ragent.clients.llm.LLMClient", MagicMock()),
        patch("ragent.clients.rerank.RerankClient", MagicMock()),
        patch("ragent.clients.auth.TokenManager", MagicMock()),
        patch(
            "haystack_integrations.document_stores.elasticsearch.ElasticsearchDocumentStore",
            MagicMock(),
        ),
        patch("elasticsearch.Elasticsearch", MagicMock()),
        patch("ragent.pipelines.retrieve.build_retrieval_pipeline", MagicMock()),
        patch("ragent.pipelines.ingest.build_ingest_pipeline", MagicMock()),
        patch("ragent.repositories.document_repository.DocumentRepository", MagicMock()),
        patch("ragent.storage.minio_registry.MinioSiteRegistry", MagicMock()),
        patch("ragent.clients.rate_limiter.RateLimiter", MagicMock()),
        patch("ragent.extractors.registry.PluginRegistry", MagicMock()),
        patch("ragent.extractors.stub_graph.StubGraphExtractor", MagicMock()),
        patch("httpx.Client"),
    ):
        import ragent.bootstrap.composition as comp

        container = comp.build_container()

    assert isinstance(container.dispatcher, TaskiqDispatcher)
