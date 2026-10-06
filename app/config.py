from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/salon"
    db_null_pool: bool = False

    # "production" unless told otherwise, so a forgotten setting fails loudly instead of running unsafe.
    app_env: str = "production"  # production | development
    jwt_secret: str = ""
    jwt_ttl_days: int = 30
    cookie_secure: bool = False

    sms_backend: str = "console"  # console | eskiz | none (production uses Telegram instead)
    telegram_backend: str = "fake"  # live | fake (development and tests)
    public_url: str = ""  # https://salonbron.uz, where Telegram sends bot updates
    token_key: str = ""  # optional separate key for bot tokens at rest; defaults to JWT_SECRET
    eskiz_email: str = ""
    eskiz_password: str = ""
    eskiz_from: str = "4546"

    default_timezone: str = "Asia/Tashkent"
    slot_step_min: int = 15
    min_lead_min: int = 30
    booking_horizon_days: int = 14
    cancel_deadline_hours: int = 2

    otp_ttl_min: int = 5
    otp_max_per_hour: int = 5
    otp_resend_cooldown_sec: int = 60
    otp_max_attempts: int = 5

    run_scheduler: bool = True

    # Platform super-admin (creates salons, extends subscriptions).
    # Make the hash with: python -m scripts.hash_password
    platform_login: str = ""
    platform_password_hash: str = ""

    @model_validator(mode="after")
    def _safe_in_production(self):
        if self.app_env == "development":
            self.jwt_secret = self.jwt_secret or "dev-only-secret-not-for-production-use"
            return self
        if len(self.jwt_secret) < 32:
            raise ValueError("JWT_SECRET kamida 32 belgili tasodifiy satr bo'lishi kerak (python -c \"import secrets; print(secrets.token_urlsafe(48))\")")
        self.cookie_secure = True  # sessions travel only over HTTPS
        if self.telegram_backend == "live" and not self.public_url.startswith("https://"):
            raise ValueError("TELEGRAM_BACKEND=live uchun PUBLIC_URL https:// bilan boshlanishi kerak")
        if self.telegram_backend != "live":
            raise ValueError("Production'da TELEGRAM_BACKEND=live bo'lishi kerak")
        return self


settings = Settings()
