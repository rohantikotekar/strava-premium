"""Runtime configuration, read once from the environment / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    environment: str = "development"

    #: `sp_app`, not the `sp` superuser — superusers bypass RLS (see the migration).
    database_url: str = "postgresql+psycopg://sp_app:sp_dev_password@localhost:5432/strava_premium"
    redis_url: str = "redis://localhost:6379/0"

    # object storage
    s3_endpoint: str = "http://localhost:9000"
    #: What the browser should use for presigned URLs. Differs from `s3_endpoint`
    #: when the API runs in Docker and the browser does not.
    s3_public_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "strava-premium"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_region: str = "us-east-1"

    # security
    session_secret: str = "dev-only-not-a-real-secret-change-me"
    token_encryption_key: str = ""
    cookie_secure: bool = False
    session_ttl_days: int = 30

    # app
    api_base_url: str = "http://localhost:8000"
    web_base_url: str = "http://localhost:5173"
    cors_origins: str = "http://localhost:5173"

    # google sign-in (optional)
    google_client_id: str = ""
    google_client_secret: str = ""

    # strava (optional for local v1)
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_webhook_verify_token: str = "dev-verify-token"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def strava_enabled(self) -> bool:
        return bool(self.strava_client_id and self.strava_client_secret)

    @property
    def sync_database_url(self) -> str:
        """Workers use the sync driver; the API uses async (CLAUDE.md §3)."""
        return self.database_url.replace("+asyncpg", "+psycopg")

    @property
    def async_database_url(self) -> str:
        url = self.database_url
        if "+psycopg" not in url and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
