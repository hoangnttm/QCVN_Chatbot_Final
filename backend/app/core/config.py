"""Application settings (12-factor). Values come from environment / backend/.env."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> repo root is three levels up
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    # backend/.env by absolute path, so scripts run from the repo root see the same settings
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / "backend" / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Paths (relative to repo root unless absolute)
    data_dir: Path = Field(default=REPO_ROOT / "data")
    sources_dir: Path = Field(default=REPO_ROOT / "sources")

    # Datastores
    database_url: str = "postgresql+psycopg://qcvn:qcvn_dev_password@localhost:5432/qcvn"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "qcvn_dev_neo4j"
    neo4j_database: str = "neo4j"
    neo4j_timeout_s: float = (
        2.0  # connect + per-query timeout; on failure graph features switch off
    )

    # ---- graph at runtime (plan docs/plans/2026-09-29-graph-ppr-path-verification.md) ----
    # "neo4j": PPR / path verification may use the projection (needs GDS for PPR); "off": never
    graph_backend: str = "neo4j"
    # vector leg of the retriever: "pgvector" (system of record) or "neo4j" (vector index over the
    # embeddings build_graph.py copies onto the EvidenceChunk nodes)
    vector_backend: str = "pgvector"
    # Personalized PageRank (evaluation-only until the PO decides, D33): off unless a request sets ppr
    ppr_damping: float = 0.85
    ppr_max_iterations: int = 20
    ppr_entity_mass: float = 0.5  # share of the seed mass on query entities (rest on top chunks)
    ppr_seed_chunks: int = 10  # top-m fused chunks used as seeds
    ppr_weight: float = 1.0  # λ of the PPR list in RRF (fuse mode)
    # validator rule 8 (graph-grounded citations): off | observe (warn only, D31) | enforce
    graph_verify_mode: str = "observe"
    # query understanding v2 (plan G3, D74 2026-10-20): "v1" = the rules + LLM fallback
    # decide retrieval; "v2" = the frame's candidate rows and hops pin the evidence.
    # Shadow: with engine v1, run v2 anyway and only report it (audit, evaluation).
    understanding_engine: str = "v1"
    understanding_v2_shadow: bool = False
    # graph-first search (plan "quan hệ trước, vector sau"): "off" | "fuse" (the ranking of the
    # candidate region joins RRF) | "filter" (only the region stays; full corpus when empty)
    graph_first_mode: str = "off"
    graph_first_weight: float = 1.0  # λ of the graph-first list in RRF

    # QA / query-understanding LLM (ADR-0011): "gemini" (default, same key as embeddings) or
    # "anthropic" (kept as an alternative; needs ANTHROPIC_API_KEY)
    llm_provider: str = "gemini"
    gemini_answer_model: str = "gemini-3.8-flash"  # answer generation (structured output)
    gemini_answer_thinking: str = "low"  # Gemini 3.x thinking_level: low | medium | high
    gemini_understanding_model: str = "gemini-3.5-flash-lite"  # domain/intent/entity extraction
    gemini_judge_model: str = "gemini-3.8-flash"  # LLM-judge for evaluation (Phase 5)
    gemini_judge_thinking: str = "high"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    # Embeddings (ADR-0005): "gemini" (API) or "local" (sentence-transformers)
    embedding_provider: str = "gemini"
    gemini_api_key: str | None = None
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 1536  # pgvector HNSW supports <= 2000 dims; MRL truncation keeps quality
    embedding_task_document: str = "RETRIEVAL_DOCUMENT"
    embedding_task_query: str = "RETRIEVAL_QUERY"
    embedding_batch_size: int = 32  # texts per embed_content request
    embedding_max_chars: int = 6000  # ~2,000 tokens of Vietnamese; longer context_text is clipped
    local_embedding_model: str = "BAAI/bge-m3"
    # LLM reranker (research plan 2026-09-28): Gemini, same provider as embeddings/answers.
    # Off unless a request sets rerank="llm" (evaluation configs C9/C10/C4-rr).
    reranker_model: str = "gemini-3.5-flash-lite"
    rerank_timeout_s: int = 10  # HTTP timeout of the rerank call; on failure keep the RRF order
    rerank_max_chars: int = 700  # context_text characters shown per candidate

    # ---- operations (Phase 5: admin, monitoring, alerts) ----
    app_version: str = "0.2.0"
    log_level: str = "INFO"
    log_json: bool = True  # one JSON line per record with request_id / query_id
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"  # comma separated
    admin_token: str | None = None  # X-Admin-Token for /api/v1/admin/* until M9 (real auth)
    gemini_timeout_s: int = 60  # per-request HTTP timeout for Gemini calls
    # quota / budget: warn only, never block (PO decision 2026-09-24)
    quota_questions_per_client_day: int = 50
    quota_global_usd_per_day: float = 5.0
    # alerts (evaluated every alert_interval_s over the last alert_window_min minutes)
    alerts_enabled: bool = True
    alert_webhook_url: str | None = None  # Slack / Teams / Google Chat incoming webhook
    alert_interval_s: int = 60
    alert_window_min: int = 15
    alert_min_samples: int = 5
    alert_error_rate: float = 0.2
    alert_abstain_rate: float = 0.6
    alert_validator_reject_rate: float = 0.3
    alert_p95_latency_ms: int = 15000
    alert_gemini_consecutive_errors: int = 3
    alert_cooldown_min: int = 60
    health_gemini_probe: bool = False  # /admin/health?probe=true may call count_tokens (free)
    history_max: int = 100
    query_log_retention_days: int = 365  # scripts/purge_logs.py (M12)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def registry_file(self) -> Path:
        return self.data_dir / "registry" / "documents.yaml"

    @property
    def dictionaries_dir(self) -> Path:
        return self.data_dir / "dictionaries"

    @property
    def grammars_dir(self) -> Path:
        return self.data_dir / "grammars"


@lru_cache
def get_settings() -> Settings:
    return Settings()
