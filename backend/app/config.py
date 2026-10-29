from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://rag:rag@localhost:5432/rag"

    # Generation provider: "gemini" or "groq". Embeddings always use Gemini.
    llm_provider: str = "gemini"
    llm_fallback: bool = True  # if the primary provider is out of quota or down, try the other (when its key is set)
    gemini_api_key: str = ""
    groq_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_fallback_model: str = "gemini-3.7-flash"
    groq_model: str = "openai/gpt-oss-120b"
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768

    # Retrieval
    top_k: int = 5
    candidate_k: int = 20
    min_relevance: float = 0.55  # cosine floor; retrieval alone cannot reject in-domain-but-uncovered questions (see eval/tune_threshold.py)
    keyword_mode: str = "or"  # full-text matching: "or" (any word) or "and" (all words); see eval/retrieval_lab.py
    rerank_enabled: bool = False  # cross-encoder reranking of the fused candidates
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_candidates: int = 20
    chunk_size: int = 800
    chunk_overlap: int = 120

    # Guardrails / API
    max_question_chars: int = 1000
    api_key: str = ""  # if set, clients must send X-API-Key
    cors_origins: str = "http://localhost:5173"
    admin_api_key: str = ""  # admin endpoints are disabled unless this is set
    rate_limit_per_minute: int = 20  # per client, /api/chat and uploads; 0 disables
    # Evaluation only: turns off screening, relevance floor and citation enforcement so the
    # same golden set can measure what the guardrails actually prevent.
    guardrails_enabled: bool = True
    log_level: str = "INFO"
    # Second-layer ML injection detector (see eval/injection_eval.py for the measurements behind these defaults).
    ml_guard: str = "off"  # "off" or "groq" (needs GROQ_API_KEY, independent of LLM_PROVIDER)
    ml_guard_model: str = "meta-llama/llama-prompt-guard-2-86m"
    ml_guard_threshold: float = 0.5
    ml_guard_fail_open: bool = True  # if the classifier is unreachable: allow (True) or block (False)
    ml_guard_timeout: float = 5.0
    # Only enable behind a reverse proxy that overwrites X-Forwarded-For (the bundled nginx does).
    # Otherwise any client could spoof its address and dodge the rate limit.
    trusted_proxy: bool = False
    # Rough list prices (USD per 1M tokens) used for the cost estimate in /api/admin/stats.
    price_in_per_m: float = 0.15
    price_out_per_m: float = 0.60


@lru_cache
def get_settings() -> Settings:
    return Settings()
