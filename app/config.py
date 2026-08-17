from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    cors_origins: str = "http://localhost:3000,http://192.168.0.2:3000"
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30
    kalkan_url: str = "http://localhost:8001"

    # KGD Smart Bridge integration
    kgd_smartbridge_enabled: bool = False
    kgd_sender_id: str = ""
    kgd_sender_password: str = ""
    kgd_service_key: str = "FNO_INTEGRATION_STATUS"

    # Kaspi POS Automation integration
    kaspi_pos_url: str = "http://localhost:3001"
    kaspi_pos_token_sn: str = ""
    kaspi_pos_vtoken_secret: str = ""
    kaspi_pos_profile_id: str = ""
    kaspi_webhook_secret: str = "salyq-webhook-secret-change-me"
    # Service fee in KZT (what user pays for declaration filing)
    service_fee_amount: int = 2000

    # SIGEX eGov QR integration
    sigex_url: str = "https://sigex.kz"

    # Database
    database_url: str = "sqlite:///./salyq.db"

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
