from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Dict, Tuple
from enum import Enum

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True)

    API_ID: int = None
    API_HASH: str = None
    GLOBAL_CONFIG_PATH: str = "TG_FARM"

    FIX_CERT: bool = False

    SESSION_START_DELAY: int = 360
    ACTION_DELAY: tuple[int, int] = (2, 5)
    REQUEST_RETRIES: int = 3

    SLEEP_TIME: tuple[int, int] = (600, 3600)
    REF_ID: str = '72633a323238363138373939'
    SESSIONS_PER_PROXY: int = 1
    USE_PROXY: bool = True
    DISABLE_PROXY_REPLACE: bool = False

    DEVICE_PARAMS: bool = False

    DEBUG_LOGGING: bool = False

    AUTO_UPDATE: bool = True
    CHECK_UPDATE_INTERVAL: int = 60
    BLACKLISTED_SESSIONS: str = ""

    BUY_GACHA_PACKS: bool = True
    GEMS_SAFE_BALANCE: int = 1000

    @property
    def blacklisted_sessions(self) -> List[str]:
        return [s.strip() for s in self.BLACKLISTED_SESSIONS.split(',') if s.strip()]

settings = Settings()
