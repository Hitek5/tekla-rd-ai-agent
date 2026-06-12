from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    agent_env: str = "local"
    agent_host: str = "0.0.0.0"
    agent_port: int = 8080

    audit_log_path: Path = Path("data/audit/orchestrator.jsonl")

    llm_base_url: str = "http://127.0.0.1:8000/v1"
    llm_api_key: str = "local-dev-key"
    llm_model: str = "qwen2.5-coder-7b-instruct-q4"
    llm_timeout_seconds: float = 120.0

    rag_chunks_path: Path = Path("data/rag/chunks.jsonl")
    rag_top_k: int = 5
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "tekla_rd_docs"

    default_dry_run: bool = True
    require_approval_for_mutations: bool = True
    allow_production_model_writes: bool = False

    tool_policy_path: Path = Field(default=Path("configs/tools-policy.yaml"))


settings = Settings()

