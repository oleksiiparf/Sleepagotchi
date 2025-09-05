import asyncio
import json
import os
import shutil
from bot.utils import logger, log_error, AsyncInterProcessLock
from opentele.api import API
from os import path, remove
from copy import deepcopy


def read_config_file(config_path: str) -> dict:
    try:
        with open(config_path, 'r') as file:
            content = file.read()
            if not content.strip():
                return {}
            return json.loads(content)
    except FileNotFoundError:
        with open(config_path, 'w'):
            logger.warning(f"Accounts config file `{config_path}` not found. Creating a new one.")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file `{config_path}`: {e}")
        return {}
    except Exception as e:
        logger.error(f"Error reading config file `{config_path}`: {e}")
        return {}


async def write_config_file(content: dict, config_path: str) -> None:
    lock = AsyncInterProcessLock(path.join(path.dirname(config_path), 'lock_files', 'accounts_config.lock'))
    async with lock:
        with open(config_path, 'w+') as file:
            json.dump(content, file, indent=2)
        await asyncio.sleep(0.1)


def get_session_config(session_name: str, config_path: str) -> dict:
    return read_config_file(config_path).get(session_name, {})


async def update_session_config_in_file(session_name: str, updated_session_config: dict, config_path: str) -> None:
    config = read_config_file(config_path)
    config[session_name] = updated_session_config
    await write_config_file(config, config_path)


async def restructure_config(config_path: str) -> None:
    config = read_config_file(config_path)
    if config:
        cfg_copy = deepcopy(config)
        for key, value in cfg_copy.items():
            api_info = {
                "api_id": value.get('api', {}).get("api_id") or value.pop("api_id", None),
                "api_hash": value.get('api', {}).get("api_hash") or value.pop("api_hash", None),
                "device_model": value.get('api', {}).get("device_model") or value.pop("device_model", None),
                "system_version": value.get('api', {}).get("system_version") or value.pop("system_version", None),
                "app_version": value.get('api', {}).get("app_version") or value.pop("app_version", None),
                "system_lang_code": value.get('api', {}).get("system_lang_code") or value.pop("system_lang_code", None),
                "lang_pack": value.get('api', {}).get("lang_pack") or value.pop("lang_pack", None),
                "lang_code": value.get('api', {}).get("lang_code") or value.pop("lang_code", None)
            }
            api_info = {k: v for k, v in api_info.items() if v is not None}
            cfg_copy[key]['api'] = api_info
        if cfg_copy != config:
            await write_config_file(cfg_copy, config_path)


def import_session_json(session_path: str) -> dict:
    lang_pack = {
        6: "android",
        4: "android",
        2040: 'tdesktop',
        10840: 'ios',
        21724: "android",
    }
    json_path = f"{session_path.replace('.session', '')}.json"
    if path.isfile(json_path):
        with open(json_path, 'r') as file:
            json_conf = json.loads(file.read())
        api = {
            'api_id': int(json_conf.get('app_id')),
            'api_hash': json_conf.get('app_hash'),
            'device_model': json_conf.get('device'),
            'system_version': json_conf.get('sdk'),
            'app_version': json_conf.get('app_version'),
            'system_lang_code': json_conf.get('system_lang_code'),
            'lang_code': json_conf.get('lang_code'),
            'lang_pack': json_conf.get('lang_pack', lang_pack[int(json_conf.get('app_id'))])
        }
        remove(json_path)
        return api
    return None


def get_api(acc_api: dict) -> API:
    api_generators = {
        4: API.TelegramAndroid.Generate,
        6: API.TelegramAndroid.Generate,
        2040: API.TelegramDesktop.Generate,
        10840: API.TelegramIOS.Generate,
        21724: API.TelegramAndroidX.Generate
    }
    generate_api = api_generators.get(acc_api.get('api_id'), API.TelegramDesktop.Generate)
    api = generate_api()
    api.api_id = acc_api.get('api_id', api.api_id)
    api.api_hash = acc_api.get('api_hash', api.api_hash)
    api.device_model = acc_api.get('device_model', api.device_model)
    api.system_version = acc_api.get('system_version', api.system_version)
    api.app_version = acc_api.get('app_version', api.app_version)
    api.system_lang_code = acc_api.get('system_lang_code', api.system_lang_code)
    api.lang_code = acc_api.get('lang_code', api.lang_code)
    api.lang_pack = acc_api.get('lang_pack', api.lang_pack)
    return api


def get_session_farming_config(session_name: str, config_path: str) -> dict:
    """Get farming configuration for a specific session"""
    session_config = get_session_config(session_name, config_path)
    return session_config.get('farming', {})


def get_session_priority_config(session_name: str, config_path: str) -> dict:
    """Get priority configuration for a specific session"""
    session_config = get_session_config(session_name, config_path)
    return session_config.get('priority', {})


async def update_session_farming_config(session_name: str, farming_config: dict, config_path: str) -> None:
    """Update farming configuration for a specific session"""
    config = read_config_file(config_path)
    if session_name not in config:
        config[session_name] = {}
    config[session_name]['farming'] = farming_config
    await write_config_file(config, config_path)


async def update_session_priority_config(session_name: str, priority_config: dict, config_path: str) -> None:
    """Update priority configuration for a specific session"""
    config = read_config_file(config_path)
    if session_name not in config:
        config[session_name] = {}
    config[session_name]['priority'] = priority_config
    await write_config_file(config, config_path)


async def set_session_farming_setting(session_name: str, setting_name: str, value: bool, config_path: str) -> None:
    """Set a specific farming setting for a session"""
    farming_config = get_session_farming_config(session_name, config_path)
    farming_config[setting_name] = value
    await update_session_farming_config(session_name, farming_config, config_path)


async def set_session_priority_setting(session_name: str, setting_name: str, value: int, config_path: str) -> None:
    """Set a specific priority setting for a session"""
    priority_config = get_session_priority_config(session_name, config_path)
    priority_config[setting_name] = value
    await update_session_priority_config(session_name, priority_config, config_path)


def create_session_env_file(session_name: str, sessions_path: str, template_path: str = ".env-session") -> str:
    """Create a session-specific .env file from template"""
    session_env_file = os.path.join(sessions_path, f"{session_name}.env")
    
    if not os.path.exists(session_env_file):
        if os.path.exists(template_path):
            shutil.copy2(template_path, session_env_file)
            logger.info(f"Created session config file: {session_env_file}")
        else:
            # Create default session config if template doesn't exist
            default_config = """# Session-specific configuration for {session_name}

BUY_GACHA_PACKS=False
SPEND_GACHAS=False
GEMS_SAFE_BALANCE=100000

# Resource farming settings
FARM_GREEN_STONES=True
FARM_PURPLE_STONES=True
FARM_GOLD=True
FARM_GACHA=True
FARM_POINTS=True

# Priority for bonk hero (1 = highest, 5 = lowest)
BONK_PRIORITY_GREEN=3
BONK_PRIORITY_PURPLE=4
BONK_PRIORITY_GOLD=1
BONK_PRIORITY_GACHA=2
BONK_PRIORITY_POINTS=5
""".format(session_name=session_name)
            
            with open(session_env_file, 'w') as f:
                f.write(default_config)
            logger.info(f"Created default session config file: {session_env_file}")
    
    return session_env_file


def get_session_env_file_path(session_name: str, sessions_path: str) -> str:
    """Get the path to a session's .env file"""
    return os.path.join(sessions_path, f"{session_name}.env")


def session_env_file_exists(session_name: str, sessions_path: str) -> bool:
    """Check if a session has its own .env file"""
    session_env_file = get_session_env_file_path(session_name, sessions_path)
    return os.path.exists(session_env_file)


def update_session_env_setting(session_name: str, sessions_path: str, setting_name: str, value) -> bool:
    """Update a specific setting in a session's .env file"""
    session_env_file = get_session_env_file_path(session_name, sessions_path)
    
    if not os.path.exists(session_env_file):
        logger.warning(f"Session .env file not found: {session_env_file}")
        return False
    
    try:
        # Read the current file
        with open(session_env_file, 'r') as f:
            lines = f.readlines()
        
        # Update the setting
        setting_updated = False
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            if line_stripped.startswith(f"{setting_name}=") or line_stripped.startswith(f"{setting_name}: "):
                # Convert boolean values to proper format
                if isinstance(value, bool):
                    value_str = "True" if value else "False"
                else:
                    value_str = str(value)
                
                lines[i] = f"{setting_name}={value_str}\n"
                setting_updated = True
                break
        
        # If setting wasn't found, add it
        if not setting_updated:
            if isinstance(value, bool):
                value_str = "True" if value else "False"
            else:
                value_str = str(value)
            lines.append(f"{setting_name}={value_str}\n")
        
        # Write back to file
        with open(session_env_file, 'w') as f:
            f.writelines(lines)
        
        logger.info(f"Updated {setting_name}={value} in {session_env_file}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to update session env file: {e}")
        return False
