from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/diagnostic_booking"
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    webhook_secret: str = "change-me-webhook-secret"
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    environment: str = "development"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    auth_rate_limit_per_minute: int = 30
    webhook_rate_limit_per_minute: int = 60
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 300

    @model_validator(mode="after")
    def validate_security_settings(self):
        if self.environment.lower() in {"production", "prod"}:
            if self.jwt_secret_key.startswith("change-me") or len(self.jwt_secret_key) < 32:
                raise ValueError("JWT_SECRET_KEY must be a strong secret in production")
            if self.webhook_secret.startswith("change-me") or len(self.webhook_secret) < 32:
                raise ValueError("WEBHOOK_SECRET must be a strong secret in production")
        return self

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
