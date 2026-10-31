from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    app_env: str = "development"
    database_url: str = Field(default="postgresql+psycopg://localhost/reslot", repr=False)
    redis_url: str = Field(default="redis://localhost:6379/0", repr=False)
    cors_origins: list[str] = ["http://localhost:5173"]
    cookie_secure: bool = False
    session_ttl_seconds: int = Field(default=28800, ge=300, le=604800)

    @model_validator(mode="after")
    def production_cookies(self) -> "Settings":
        if self.app_env != "development" and not self.cookie_secure:
            raise ValueError("Secure cookies are required outside development")
        if "*" in self.cors_origins:
            raise ValueError("Explicit trusted origins are required")
        return self

    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = "http://localhost:8000/api/v1/oauth/google/callback"
    google_token_encryption_key: SecretStr = SecretStr("")
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "openai/gpt-oss-20b"


@lru_cache
def get_settings() -> Settings:
    return Settings()
