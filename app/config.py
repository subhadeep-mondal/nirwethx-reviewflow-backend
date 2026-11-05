from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "NirwethX ReviewFlow API"
    environment: str = "development"
    app_url: str = "http://localhost:5173"
    api_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./reviewflow.db"
    jwt_secret: str = "development-only-change-this-secret"
    jwt_expire_minutes: int = 10080
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    cors_origins: str = "http://localhost:5173"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    ip_hash_secret: str = "development-ip-hash-secret"
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

settings = Settings()
