from ua_generator import generate
from ua_generator.options import Options
from ua_generator.data.version import VersionRange
from typing import Dict, Optional


def generate_random_user_agent(platform: str = 'macos', browser: str = 'chrome', 
                             min_version: int = 110, max_version: int = 131) -> str:
    options = Options(version_ranges={'chrome': VersionRange(min_version, max_version)})
    return generate(browser=browser, platform=platform, options=options).text


def get_default_user_agent() -> str:
    return 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
