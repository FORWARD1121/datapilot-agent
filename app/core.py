"""Central settings, public errors, and JSON normalization."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppError(Exception):
    """Only this exception's message is safe to expose through the API."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: Literal["local", "public"] = "local"
    data_dir: Path = Path(".datapilot")
    database_url: str | None = None
    api_token: SecretStr = SecretStr("")
    max_upload_bytes: int = Field(10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    max_rows: int = Field(50000, ge=1, le=100000)
    max_columns: int = Field(100, ge=2, le=200)
    max_cells: int = Field(1000000, ge=2, le=5000000)
    max_uncompressed_bytes: int = Field(50 * 1024 * 1024, ge=1024)
    llm_provider: Literal["mock", "openai_compatible"] = "mock"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_timeout_seconds: float = Field(20, gt=0, le=120)
    rules_path: Path | None = None

    @model_validator(mode="after")
    def validate_public(self):
        if self.app_env == "public" and len(self.api_token.get_secret_value()) < 32:
            raise ValueError("Public mode requires an API_TOKEN of at least 32 characters")
        return self

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir.resolve() / 'datapilot.db'}"


def ensure_json(value):
    """Reject NaN/Infinity and unsupported types at trust boundaries."""
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
