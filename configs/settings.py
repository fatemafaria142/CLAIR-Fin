"""Pydantic settings for LLM, Milvus, and filesystem paths, loaded from environment/.env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class LLMSettings(BaseSettings):
    """Model selection and inference defaults, shared by every LLM-backed agent/tool."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    openai_api_key: SecretStr = Field(alias="OPENAI_API_KEY")
    chat_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    vision_model: str = Field(default="gpt-4o-mini", alias="OPENAI_VISION_MODEL")
    embedding_model: str = Field(default="text-embedding-3-large", alias="EMBEDDING_MODEL")
    temperature: float = Field(default=0.2, alias="OPENAI_TEMPERATURE")


class MilvusSettings(BaseSettings):
    """Connection settings for Milvus, the vector store backing retrieval."""

    model_config = SettingsConfigDict(extra="ignore")

    uri: str = str(PROJECT_ROOT / "data" / "milvus_lite.db")
    collection_name: str = "clairfin_kb"


class PathSettings(BaseSettings):
    """Every directory the pipeline reads from or writes to, resolved from `PROJECT_ROOT`."""

    model_config = SettingsConfigDict(extra="ignore")

    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    configs_dir: Path = PROJECT_ROOT / "configs"
    prompts_dir: Path = PROJECT_ROOT / "prompts"
    results_dir: Path = PROJECT_ROOT / "results"
    logs_dir: Path = PROJECT_ROOT / "logs"

    def source_pdfs(self) -> list[Path]:
        """Every PDF currently in `data/` — the knowledge base. Not hardcoded to one filename:
        drop in whatever document(s) you're working with and ingestion picks them up."""
        return sorted(self.data_dir.glob("*.pdf"))

    def ensure_runtime_dirs(self) -> None:
        """Create the output directories a run needs (never the input `data_dir`)."""
        for directory in (self.results_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    """Aggregate settings object. Import `get_settings()`, not this class, in application code."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm: LLMSettings = Field(default_factory=lambda: LLMSettings())
    paths: PathSettings = Field(default_factory=PathSettings)
    milvus: MilvusSettings = Field(default_factory=MilvusSettings)

    aea_weights_path: Path = PROJECT_ROOT / "configs" / "aea_weights.yaml"
    agent_budgets_path: Path = PROJECT_ROOT / "configs" / "agent_budgets.yaml"
    pricing_path: Path = PROJECT_ROOT / "configs" / "pricing.yaml"


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached settings instance."""
    return Settings()
