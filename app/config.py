import os
from dotenv import load_dotenv

load_dotenv()


# Per-million-token pricing in USD: (input_per_M, output_per_M).
# Keep updated when adding/changing models. Looked up by ANTHROPIC_MODEL.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-7":           (15.0, 75.0),
    "claude-opus-4-6":           (15.0, 75.0),
    "claude-sonnet-4-6":         ( 3.0, 15.0),
    "claude-sonnet-4-5":         ( 3.0, 15.0),
    "claude-haiku-4-5":          ( 1.0,  5.0),
    "claude-haiku-4-5-20251001": ( 1.0,  5.0),
}

# Used when an unknown model is configured.
_FALLBACK_PRICING: tuple[float, float] = (3.0, 15.0)


def get_model_pricing(model: str) -> tuple[float, float]:
    """Return (input_per_M_usd, output_per_M_usd) for the named model."""
    return MODEL_PRICING.get(model, _FALLBACK_PRICING)


class Config:
    # Anthropic
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str   = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    # Database
    DB_HOST: str     = os.getenv("DB_HOST", "localhost")
    DB_PORT: int     = int(os.getenv("DB_PORT", "5432"))
    DB_NAME: str     = os.getenv("DB_NAME", "talk_to_your_data")
    DB_USER: str     = os.getenv("DB_USER", os.getenv("USER", ""))
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")

    # Active tenant
    ACTIVE_TENANT_VKORG: str = os.getenv("ACTIVE_TENANT_VKORG", "1004")
    ACTIVE_TENANT_NAME: str  = os.getenv("ACTIVE_TENANT_NAME", "Brennwald")

    # Pipeline
    MAX_ROWS_RETURNED: int      = int(os.getenv("MAX_ROWS_RETURNED", "500"))
    MAX_RETRIES: int            = int(os.getenv("MAX_RETRIES", "3"))
    RATE_LIMIT_PER_SESSION: int = int(os.getenv("RATE_LIMIT_PER_SESSION", "50"))

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: str   = os.getenv("LOG_DIR", "logs")

    @classmethod
    def validate(cls):
        errors = []
        if not cls.ANTHROPIC_API_KEY or cls.ANTHROPIC_API_KEY.startswith("sk-ant-your"):
            errors.append("ANTHROPIC_API_KEY is not set in .env")
        if not cls.DB_USER:
            errors.append("DB_USER is not set in .env")
        if errors:
            raise EnvironmentError(
                "Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors)
            )

    @classmethod
    def db_dsn(cls) -> str:
        return (
            f"host={cls.DB_HOST} port={cls.DB_PORT} "
            f"dbname={cls.DB_NAME} user={cls.DB_USER} "
            f"password={cls.DB_PASSWORD}"
        )


config = Config()