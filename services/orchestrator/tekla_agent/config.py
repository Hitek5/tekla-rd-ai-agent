from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    agent_env: str = "local"
    agent_host: str = "0.0.0.0"
    agent_port: int = 8080

    audit_log_path: Path = Path("data/audit/orchestrator.jsonl")
    approval_ledger_path: Path = Path("data/audit/consumed-approvals.log")

    llm_base_url: str = "http://127.0.0.1:8000/v1"
    llm_api_key: str = "local-dev-key"
    llm_model: str = "qwen2.5-coder-7b-instruct-q4"
    llm_timeout_seconds: float = 120.0

    # Optional local embedding endpoint (OpenAI-compatible /embeddings). Leave the
    # model empty to keep retrieval BM25-only and reserve all VRAM for the LLM.
    embedding_model: str = ""
    embedding_base_url: str = ""

    rag_chunks_path: Path = Path("data/rag/chunks.jsonl")
    rag_top_k: int = 5
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "tekla_rd_docs"

    default_dry_run: bool = True
    require_approval_for_mutations: bool = True
    allow_production_model_writes: bool = False

    tool_policy_path: Path = Field(default=Path("configs/tools-policy.yaml"))

    # --- security -------------------------------------------------------
    # Shared HMAC secret for approval tokens. MUST match the secret configured
    # on the C# workstation host so it can independently verify approvals.
    approval_secret: str = "change-me-please-set-a-32-char-secret"
    approval_ttl_seconds: int = 600

    # Bearer key required to call the orchestrator API. Empty disables auth
    # (local dev only — never ship an empty key into КСПД).
    api_key: str = ""
    # Separate, stronger key authorising the /approvals minting endpoint.
    approver_api_key: str = ""

    # Key that HMACs the audit hash chain so a file-only attacker cannot forge it.
    # Empty falls back to approval_secret (always strong); set a dedicated value to
    # decouple audit integrity from the approval secret.
    audit_hmac_key: str = ""

    # Abuse limits.
    max_request_bytes: int = 65_536
    rate_limit_per_minute: int = 60

    # Model integrity manifest (SHA-256 of each served model file).
    model_manifest_path: Path = Path("data/models/manifest.json")


settings = Settings()
