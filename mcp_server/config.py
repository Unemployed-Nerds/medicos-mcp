from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central configuration for the Medicos MCP backend.

    All values are loaded from environment variables with `MEDICOS_` prefix.
    You can also use a `.env` file in the backend directory during development.
    """

    model_config = SettingsConfigDict(
        env_prefix="MEDICOS_",
        env_file=".env",
        extra="ignore",
    )

    # General
    env: str = "dev"
    server_port: int = 8000
    server_host: str = "0.0.0.0"
    transport: str = "stdio"  # "stdio" or "http"

    # Firebase
    firebase_project_id: str
    firebase_credentials_file: Optional[str] = None

    # ArmorIQ
    armoriq_api_key: str

    # LLM
    llm_provider: str = "openai"
    llm_api_key: str


class RuntimeContext(BaseModel):
    """
    Process-wide runtime context for the MCP server.

    This is created once in `main.py` and passed down where needed.
    """

    settings: Settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache settings from environment."""
    return Settings()  # type: ignore[call-arg]

