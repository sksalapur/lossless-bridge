from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    bridge_port: int = Field(default=3000)
    bridge_host: str = Field(default="0.0.0.0")
    log_level: str = Field(default="info")
    lastwave_personal_addon_url: Optional[str] = None
    resolver_priority: str = Field(default="lastwave,qobuz")
    bridge_secret: str = Field(default="secret")

    class Config:
        env_file = ".env"

settings = Settings()
