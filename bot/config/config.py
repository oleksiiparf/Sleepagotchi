from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Dict, Tuple, Optional
from enum import Enum
import os

class SessionSettings(BaseSettings):
    """Session-specific settings loaded from individual .env files"""
    model_config = SettingsConfigDict(env_ignore_empty=True)
    
    BUY_GACHA_PACKS: bool = False
    SPEND_GACHAS: bool = False
    GEMS_SAFE_BALANCE: int = 100000
    PROCESS_MISSIONS: bool = False
    UPGRADE_CARDS: bool = True

    # Resource farming settings
    FARM_GREEN_STONES: bool = True
    FARM_PURPLE_STONES: bool = True
    FARM_GOLD: bool = True
    FARM_GACHA: bool = True
    FARM_POINTS: bool = True
    
    # Constellation settings (None = use API value, int = manual override)
    CONSTELLATION_LAST_INDEX: Optional[int] = None

    # Priority for bonk hero (1 = highest, 5 = lowest)
    BONK_PRIORITY_GREEN: int = 3
    BONK_PRIORITY_PURPLE: int = 4
    BONK_PRIORITY_GOLD: int = 1
    BONK_PRIORITY_GACHA: int = 2
    BONK_PRIORITY_POINTS: int = 5

    # Priority for dragon epic hero (1 = highest, 5 = lowest)
    DRAGON_PRIORITY_GREEN: int = 2
    DRAGON_PRIORITY_PURPLE: int = 1
    DRAGON_PRIORITY_GOLD: int = 3
    DRAGON_PRIORITY_GACHA: int = 4
    DRAGON_PRIORITY_POINTS: int = 5

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

    @property
    def blacklisted_sessions(self) -> List[str]:
        return [s.strip() for s in self.BLACKLISTED_SESSIONS.split(',') if s.strip()]
    
    def get_session_settings(self, session_name: str, sessions_path: str) -> "SessionSettings":
        """Get session-specific settings from session's .env file"""
        session_env_file = os.path.join(sessions_path, f"{session_name}.env")
        
        if os.path.exists(session_env_file):
            # Create a new SessionSettings instance with the session-specific env file
            session_settings = SessionSettings(_env_file=session_env_file)
            return session_settings
        else:
            # Return default settings if no session-specific .env file exists
            return SessionSettings()

settings = Settings()
