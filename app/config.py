from pydantic import Field
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    log_level: str = Field("INFO", alias="APP_LOG_LEVEL")
    debug: bool = Field(False, alias="APP_DEBUG")
    allowed_origins: str = Field("*", alias="APP_ALLOWED_ORIGINS")
    telegram_token: str = Field(..., alias="APP_TELEGRAM_TOKEN")

    model_config = {"populate_by_name": True}
