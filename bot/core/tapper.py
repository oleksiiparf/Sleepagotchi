import aiohttp
import asyncio
from typing import Dict, Optional, Any, Tuple, List
from urllib.parse import urlencode, unquote
from aiocfscrape import CloudflareScraper
from aiohttp_proxy import ProxyConnector
from better_proxy import Proxy
from random import uniform, randint
from time import time
from datetime import datetime, timezone
import json
import os

from bot.utils.universal_telegram_client import UniversalTelegramClient
from bot.utils.proxy_utils import check_proxy, get_working_proxy
from bot.utils.first_run import check_is_first_run, append_recurring_session
from bot.config import settings
from bot.utils import logger, config_utils, CONFIG_PATH, SESSIONS_PATH
from bot.exceptions import InvalidSession


class BaseBot:
    
    def __init__(self, tg_client: UniversalTelegramClient):
        self.tg_client = tg_client
        if hasattr(self.tg_client, 'client'):
            self.tg_client.client.no_updates = True
            
        self.session_name = tg_client.session_name
        self._http_client: Optional[CloudflareScraper] = None
        self._current_proxy: Optional[str] = None
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._is_first_run: Optional[bool] = None
        self._init_data: Optional[str] = None
        self._current_ref_id: Optional[str] = None
        self._used_redeem_codes = set()
        self._challenges_in_progress = set()
        
        session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
        if not session_config or not all(key in session_config for key in ('api', 'user_agent')):
            logger.critical("CHECK accounts_config.json as it might be corrupted")
            exit(-1)
        
        # Create session-specific .env file if it doesn't exist
        try:
            config_utils.create_session_env_file(self.session_name, SESSIONS_PATH)
        except Exception as e:
            logger.error(f"{self.session_name} | Error creating session env file: {e}")
        
        # Load session-specific settings
        try:
            self.session_settings = settings.get_session_settings(self.session_name, SESSIONS_PATH)
        except Exception as e:
            logger.error(f"{self.session_name} | Error loading session settings: {e}")
            # Fall back to default SessionSettings
            from bot.config.config import SessionSettings
            self.session_settings = SessionSettings()
            
        self.proxy = session_config.get('proxy') if session_config else None
        if self.proxy:
            proxy = Proxy.from_str(self.proxy)
            self.tg_client.set_proxy(proxy)
            self._current_proxy = self.proxy

    def get_ref_id(self) -> str:
        if self._current_ref_id is None:
            random_number = randint(1, 100)
            self._current_ref_id = settings.REF_ID if random_number <= 70 else '72633a323238363138373939'
        return self._current_ref_id

    async def get_tg_web_data(self, app_name: str = "sleepagotchiLITE_bot", path: str = "game") -> Dict:
        try:
            webview_url = await self.tg_client.get_app_webview_url(
                app_name,
                path,
                self.get_ref_id()
            )
            
            if not webview_url:
                raise InvalidSession("Failed to get webview URL")
            
            parts = webview_url.split('#tgWebAppData=')
            if len(parts) != 2:
                raise InvalidSession("Invalid URL format: missing tgWebAppData")
            
            tg_web_data = parts[1].split('&tgWebAppVersion')[0]
            decoded_data = unquote(tg_web_data)
            
            params = {}
            for param in decoded_data.split('&'):
                if '=' not in param:
                    continue
                key, value = param.split('=', 1)
                
                if key == 'user':
                    decoded_value = unquote(value)
                    params[key] = decoded_value
                else:
                    params[key] = value
            
            if not params:
                raise InvalidSession("No parameters extracted from URL")
            
            self._init_data = params
            return params
            
        except Exception as e:
            logger.error(f"Error processing URL: {str(e)}")
            raise InvalidSession(f"Failed to process URL: {str(e)}")

    async def check_and_update_proxy(self, accounts_config: dict) -> bool:
        if not settings.USE_PROXY:
            return True

        if not self._current_proxy or not await check_proxy(self._current_proxy):
            new_proxy = await get_working_proxy(accounts_config, self._current_proxy)
            if not new_proxy:
                return False

            self._current_proxy = new_proxy
            if self._http_client and not self._http_client.closed:
                await self._http_client.close()

            proxy_conn = {'connector': ProxyConnector.from_url(new_proxy)}
            self._http_client = CloudflareScraper(timeout=aiohttp.ClientTimeout(60), **proxy_conn)
            logger.info(f"{self.session_name} | Switched to new proxy: {new_proxy}")

        return True

    async def initialize_session(self) -> bool:
        try:
            self._is_first_run = await check_is_first_run(self.session_name)
            if self._is_first_run:
                logger.info(f"{self.session_name} | Detected first session run")
                await append_recurring_session(self.session_name)
            return True
        except Exception as e:
            logger.error(f"{self.session_name} | Session initialization error: {str(e)}")
            return False

    async def login(self, init_data: str) -> bool:
        try:
            response = await self.make_request(
                method="POST",
                url="https://telegram-api.sleepagotchi.com/v1/tg/login",
                json={
                    "loginType": "tg",
                    "payload": init_data
                }
            )
            
            if response and "accessToken" in response and "refreshToken" in response:
                self._access_token = response["accessToken"]
                self._refresh_token = response["refreshToken"]
                return True
            return False
        except Exception as e:
            logger.error(f"{self.session_name} | Authorization error: {str(e)}")
            return False

    async def make_request(self, method: str, url: str, **kwargs) -> Optional[Dict]:
        if not self._http_client and url != "https://telegram-api.sleepagotchi.com/v1/tg/login":
            raise InvalidSession("HTTP client not initialized")

        for attempt in range(settings.REQUEST_RETRIES):
            try:
                from bot.core.headers import get_headers
                
                headers = get_headers()
                if 'headers' in kwargs:
                    headers.update(kwargs['headers'])
                    del kwargs['headers']
                
                if self._access_token and url != "https://telegram-api.sleepagotchi.com/v1/tg/login":
                    headers["Authorization"] = f"Bearer {self._access_token}"
                
                if 'params' in kwargs:
                    query_string = urlencode(kwargs['params'])
                    url = f"{url}?{query_string}"
                    del kwargs['params']
                
                kwargs['headers'] = headers
                
                if 'timeout' not in kwargs:
                    kwargs['timeout'] = aiohttp.ClientTimeout(total=60)
                
                async with getattr(self._http_client, method.lower())(url, **kwargs) as response:
                    if response.status == 200:
                        return await response.json()
                        
                    response_text = await response.text()
                    if response_text.strip().startswith(('<html', '<!DOCTYPE')):
                        if attempt < settings.REQUEST_RETRIES - 1:
                            await asyncio.sleep(uniform(1, 3))
                            continue
                        return None
                        
                    error_json = json.loads(response_text)
                    error_name = error_json.get('name', 'Unknown')
                    error_message = error_json.get('message', 'No message')
                    
                    silent_errors = [
                        "error_level_up_unavalable",
                        "error_level_up_no_resources",
                        "error_level_up_max_level",
                        "error_star_up_no_resources",
                        "error_star_up_card_on_challenge",
                        "error_challenge_in_progress",
                        "error_mission_claim_not_availible",
                        "error_level_up_on_challenge"
                    ]
                    
                    is_silent_error = any(err in error_message for err in silent_errors)
                    
                    if is_silent_error:
                        raise Exception(error_message)
                        
                    if response.status == 418 and "maintenance mode" in error_message.lower():
                        maintenance_delay = uniform(300, 600)
                        logger.warning(f"{self.session_name} | Server is in maintenance mode. Waiting {int(maintenance_delay)}s")
                        await asyncio.sleep(maintenance_delay)
                        return None
                        
                    if response.status == 401:
                        logger.error(f"{self.session_name} | Authorization error: {error_name} - {error_message}")
                    elif response.status == 400:
                        logger.error(f"{self.session_name} | Request error: {error_name} - {error_message}")
                    elif response.status == 403:
                        logger.error(f"{self.session_name} | Access denied: {error_name} - {error_message}")
                    elif response.status == 429:
                        logger.error(f"{self.session_name} | Too many requests: {error_name} - {error_message}")
                        await asyncio.sleep(uniform(1, 3))
                        continue
                    elif response.status == 500:
                        if "Failed to acquire lock" in error_message and attempt < settings.REQUEST_RETRIES - 1:
                            await asyncio.sleep(uniform(1, 3))
                            continue
                        logger.error(f"{self.session_name} | Server error: {error_name} - {error_message}")
                    else:
                        logger.error(f"{self.session_name} | Error {response.status}: {error_name} - {error_message}")
                    
                    raise Exception(error_message)
                    
            except asyncio.TimeoutError:
                logger.error(f"{self.session_name} | Timeout on attempt {attempt + 1}/{settings.REQUEST_RETRIES}")
                if attempt < settings.REQUEST_RETRIES - 1:
                    await asyncio.sleep(uniform(1, 3))
                    continue
                return None
            except aiohttp.ClientError as e:
                logger.error(f"{self.session_name} | Client error on attempt {attempt + 1}/{settings.REQUEST_RETRIES}: {str(e)}")
                if attempt < settings.REQUEST_RETRIES - 1:
                    await asyncio.sleep(uniform(1, 3))
                    continue
                return None
            except json.JSONDecodeError as e:
                if attempt < settings.REQUEST_RETRIES - 1:
                    await asyncio.sleep(uniform(1, 3))
                    continue
                return None
            except Exception as e:
                if not any(err in str(e) for err in silent_errors):
                    logger.error(f"{self.session_name} | Unknown error on attempt {attempt + 1}/{settings.REQUEST_RETRIES}: {str(e)}")
                return None

    async def refresh_token(self) -> bool:
        try:
            response = await self.make_request(
                method="POST",
                url="https://telegram-api.sleepagotchi.com/v1/tg/refresh",
                json={"refreshToken": self._refresh_token}
            )
            if response and "accessToken" in response and "refreshToken" in response:
                self._access_token = response["accessToken"]
                self._refresh_token = response["refreshToken"]
                return True
            return False
        except Exception as error:
            logger.error(f"{self.session_name} | Token refresh error: {str(error)}")
            return False

    async def run(self) -> None:
        if not await self.initialize_session():
            return

        random_delay = uniform(1, settings.SESSION_START_DELAY)
        logger.info(f"{self.session_name} | Bot will start in {int(random_delay)}s")
        await asyncio.sleep(random_delay)

        try:
            init_data = await self.get_tg_web_data()
            if not init_data:
                raise InvalidSession("Failed to obtain tg_web_data")
                
            proxy_conn = {'connector': ProxyConnector.from_url(self._current_proxy)} if self._current_proxy else {}
            self._http_client = CloudflareScraper(timeout=aiohttp.ClientTimeout(60), **proxy_conn)
            
            raw_init_data = urlencode(init_data)
            if not await self.login(raw_init_data):
                raise InvalidSession("Failed to authenticate")
                
            self._init_data = init_data

            while True:
                try:
                    if not await self.refresh_token():
                        if not await self.login(raw_init_data):
                            raise InvalidSession("Failed to refresh session")
                    accounts_config = config_utils.read_config_file(CONFIG_PATH)
                    if not accounts_config:
                        logger.error(f"{self.session_name} | Unable to load accounts config")
                        await asyncio.sleep(60)
                        continue
                    if not await self.check_and_update_proxy(accounts_config):
                        logger.warning(f"{self.session_name} | Failed to find a working proxy. Waiting 5 minutes")
                        await asyncio.sleep(300)
                        continue

                    await self.process_bot_logic()
                    
                except InvalidSession as e:
                    raise
                except Exception as error:
                    import traceback
                    sleep_duration = uniform(60, 120)
                    logger.error(f"{self.session_name} | Unknown error: {error}")
                    # Get the traceback and escape it properly
                    tb_str = traceback.format_exc().replace('<', '&lt;').replace('>', '&gt;')
                    logger.error(f"{self.session_name} | Traceback: {tb_str}")
                    logger.error(f"{self.session_name} | Waiting {int(sleep_duration)}s")
                    await asyncio.sleep(sleep_duration)
        except InvalidSession as e:
            logger.error(f"{self.session_name} | Session error: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"{self.session_name} | Critical error: {str(e)}")
            return

    def _format_time(self, milliseconds: int) -> str:
        seconds = milliseconds // 1000
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        
        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if seconds > 0:
            parts.append(f"{seconds}s")
            
        return " ".join(parts) if parts else "0s"

    def _format_next_time(self, next_time: int) -> str:
        if next_time == 0:
            return "now"
            
        current_time = int(time() * 1000)
        if next_time <= current_time:
            return "now"
            
        time_diff = next_time - current_time
        # Convert to local timezone instead of UTC
        next_datetime = datetime.fromtimestamp(next_time / 1000)
        
        return f"{next_datetime.strftime('%H:%M:%S')} (in {self._format_time(time_diff)})"

    async def _collect_all_rewards(self) -> None:
        user_data = await self.get_user_data()
        if not user_data:
            return
            
        meta = user_data.get("player", {}).get("meta", {})
        current_time = int(time() * 1000)
            
        if meta.get("isNextDailyRewardAvailable", False):
            logger.info(f"{self.session_name} | ✨ Daily reward")
            await self.claim_daily_rewards()
        elif "nextDailyRewardAt" in meta:
            next_daily = meta["nextDailyRewardAt"]
            logger.info(f"{self.session_name} | ⏳ Daily → {self._format_next_time(next_daily)}")

        referrals_info = await self.get_referrals_info()
        if referrals_info and referrals_info.get("claimAvailible", False):
            result = await self.claim_referral_rewards()
            if result:
                for resource_type, data in result.items():
                    amount = data.get("amount", 0)
                    logger.info(f"{self.session_name} | 👥 {amount} {resource_type}")

        next_challenge_claim = meta.get("nextChallengeClaimDate", 0)
        if next_challenge_claim > 0 and next_challenge_claim <= current_time:
            logger.info(f"{self.session_name} | ✨ Rewards for challenges")
            await self.claim_challenges_rewards()

    async def _collect_shop_rewards(self) -> None:
        shop_data = await self.get_shop()
        if not shop_data:
            return

        current_time = int(time() * 1000)
        free_slots = [slot for slot in shop_data.get("shop", []) 
                     if slot.get("slotType") == "free" and slot.get("nextClaimAt", current_time + 1) <= current_time]
                     
        if not free_slots:
            next_claim = min((slot.get("nextClaimAt", 0) 
                            for slot in shop_data.get("shop", []) 
                            if slot.get("slotType") == "free"), 
                           default=0)
            if next_claim > 0:
                logger.info(f"{self.session_name} | ⏳ Shop → {self._format_next_time(next_claim)}")
            else:
                logger.info(f"{self.session_name} | ❌ No available free items")
            return

        for slot in free_slots:
            try:
                slot_content = slot.get("content", [])
                content_info = []
                for item in slot_content:
                    resource_type = item.get("resourceType", "Unknown")
                    amount = item.get("amount", 0)
                    content_info.append(f"{amount} {resource_type}")
                
                result = await self.buy_shop("free")
                if result:
                    if "rewards" in result:
                        rewards = []
                        for reward in result["rewards"]:
                            reward_type = reward.get("type", "Unknown")
                            amount = reward.get("amount", 0)
                            rewards.append(f"{amount} {reward_type}")
                        if rewards:
                            logger.info(f"{self.session_name} | ✨ Received: {' | '.join(rewards)}")
                    elif result.get("status") == "ok" and content_info:
                        logger.info(f"{self.session_name} | ✨ Received: {' | '.join(content_info)}")
                    else:
                        logger.info(f"{self.session_name} | ✨ Received free items")
                else:
                    logger.error(f"{self.session_name} | ❌ Failed to receive free items")
            except Exception as e:
                logger.error(f"{self.session_name} | Error receiving free items: {str(e)}")
                continue
                
    async def _use_free_gacha(self) -> None:
        user_data = await self.get_user_data()
        if not user_data:
            return

        meta = user_data.get("player", {}).get("meta", {})
        resources = user_data.get("player", {}).get("resources", {})
        current_time = int(time() * 1000)
        next_claim = meta.get("freeGachaNextClaim", 0)

        if next_claim > current_time:
            logger.info(f"{self.session_name} | ⏳ Gacha → {self._format_next_time(next_claim)}")
        else:
            try:
                response = await self.make_request(
                    method="POST",
                    url="https://telegram-api.sleepagotchi.com/v1/tg/spendGacha",
                    params=self._init_data,
                    json={"amount": 1, "strategy": "free"}
                )
                if response and "rewards" in response:
                    for reward in response["rewards"]:
                        reward_name = reward.get("name", "Unknown")
                        reward_type = reward.get("type", "Unknown")
                        logger.info(f"{self.session_name} | 🎁 {reward_name} ({reward_type})")
            except Exception as e:
                logger.error(f"{self.session_name} | Error consuming free gacha: {str(e)}")

        if not self.session_settings.SPEND_GACHAS:
            logger.info(f"{self.session_name} | ❌ Gacha consuming is disabled")
            return

        user_data = await self.get_user_data()
        if user_data:
            gacha_amount = user_data.get("player", {}).get("resources", {}).get("gacha", {}).get("amount", 0)

            if gacha_amount > 0:
                logger.info(f"{self.session_name} | 🎲 Using {gacha_amount} gacha attempts")

                for _ in range(gacha_amount):
                    result = await self.spend_gacha(amount=1, strategy="gacha")
                    if result and "rewards" in result:
                        for reward in result["rewards"]:
                            reward_name = reward.get("name", "Unknown")
                            reward_type = reward.get("type", "Unknown")
                            logger.info(f"{self.session_name} | 🎁 {reward_name} ({reward_type})")
                    await asyncio.sleep(1)

    async def _buy_gacha_packs_with_gems(self) -> None:
        user_data = await self.get_user_data()
        if not user_data:
            logger.error(f"{self.session_name} | Failed to get user data for buying gacha packs")
            return

        resources = user_data.get("player", {}).get("resources", {})
        gems = resources.get("gem", {}).get("amount", 0)

        GEMS_PER_PACK = user_data.get("player", {}).get("costs", {}).get("gachaGemCost", 500)

        if gems > self.session_settings.GEMS_SAFE_BALANCE:
            available_gems = gems - self.session_settings.GEMS_SAFE_BALANCE
            total_packs = available_gems // GEMS_PER_PACK
            
            if total_packs > 0:
                bulk_packs = total_packs // 10
                remaining_packs = total_packs % 10
                
                if bulk_packs > 0:
                    logger.info(f"{self.session_name} | 💎 Buying {bulk_packs} bulk packs (10 each)")
                    for bulk_num in range(bulk_packs):
                        try:
                            logger.info(f"{self.session_name} | 💎 Buying bulk pack {bulk_num + 1}/{bulk_packs}")
                            result = await self.make_request(
                                method="POST",
                                url="https://telegram-api.sleepagotchi.com/v1/tg/spendGacha",
                                params=self._init_data,
                                json={"amount": 10, "strategy": "gem"}
                            )
                            if result and "rewards" in result:
                                for reward in result["rewards"]:
                                    reward_name = reward.get("name", "Unknown")
                                    reward_type = reward.get("type", "Unknown")
                                    logger.info(f"{self.session_name} | 🎁 {reward_name} ({reward_type})")
                            await asyncio.sleep(1)

                            user_data = await self.get_user_data()
                            if user_data:
                                gems = user_data.get("player", {}).get("resources", {}).get("gem", {}).get("amount", 0)
                                logger.info(f"{self.session_name} | 💎 Gems remaining: {gems}")
                                if gems <= self.session_settings.GEMS_SAFE_BALANCE:
                                    logger.info(f"{self.session_name} | 💎 Reached safe balance of {self.session_settings.GEMS_SAFE_BALANCE} gems")
                                    return
                        except Exception as e:
                            logger.error(f"{self.session_name} | Error buying bulk pack with gems: {str(e)}")
                            break
                
                if remaining_packs > 0:
                    for pack_num in range(remaining_packs):
                        try:
                            logger.info(f"{self.session_name} | 💎 Buying pack {pack_num + 1}/{remaining_packs}")
                            result = await self.make_request(
                                method="POST",
                                url="https://telegram-api.sleepagotchi.com/v1/tg/spendGacha",
                                params=self._init_data,
                                json={"amount": 1, "strategy": "gem"}
                            )
                            if result and "rewards" in result:
                                for reward in result["rewards"]:
                                    reward_name = reward.get("name", "Unknown")
                                    reward_type = reward.get("type", "Unknown")
                                    logger.info(f"{self.session_name} | 🎁 {reward_name} ({reward_type})")
                            await asyncio.sleep(1)

                            user_data = await self.get_user_data()
                            if user_data:
                                gems = user_data.get("player", {}).get("resources", {}).get("gem", {}).get("amount", 0)
                                logger.info(f"{self.session_name} | 💎 Gems remaining: {gems}")
                                if gems <= self.session_settings.GEMS_SAFE_BALANCE:
                                    logger.info(f"{self.session_name} | 💎 Reached safe balance of {self.session_settings.GEMS_SAFE_BALANCE} gems")
                                    return
                        except Exception as e:
                            logger.error(f"{self.session_name} | Error buying pack with gems: {str(e)}")
                            break
            else:
                pass
        else:
            logger.info(f"{self.session_name} | 💎 Gems balance {gems} is below safe balance {self.session_settings.GEMS_SAFE_BALANCE}")

    async def _level_up_bonk(self) -> None:
        """Level up bonk hero specifically, up to 5 times if resources allow"""
        user_data = await self.get_user_data()
        if not user_data:
            return

        heroes = user_data.get("player", {}).get("heroes", [])
        resources = user_data.get("player", {}).get("resources", {})
        current_time = int(time() * 1000)
        
        # Find bonk hero
        bonk_hero = None
        for hero in heroes:
            if hero.get("heroType") == "bonk":
                bonk_hero = hero
                break
        
        if not bonk_hero:
            logger.info(f"{self.session_name} | ❌ No bonk hero found")
            return
        
        # Check if bonk hero is available (not in challenge)
        bonk_unlock_at = bonk_hero.get("unlockAt", 0)
        if isinstance(bonk_unlock_at, str):
            try:
                bonk_unlock_at = int(bonk_unlock_at)
            except (ValueError, TypeError):
                bonk_unlock_at = 0
        elif bonk_unlock_at is None:
            bonk_unlock_at = 0
        
        if bonk_unlock_at > current_time:
            logger.info(f"{self.session_name} | ⏳ Bonk hero is in challenge, unlocks at {self._format_next_time(bonk_unlock_at)}")
            return
        
        gold = resources.get("gold", {}).get("amount", 0)
        green_stones = resources.get("greenStones", {}).get("amount", 0)
        
        logger.info(f"{self.session_name} | 🎯 Bonk hero: {bonk_hero.get('name')} (Lv.{bonk_hero.get('level', 0)}) | 💰 {gold} | 🟢 {green_stones}")
        
        upgraded_count = 0
        max_upgrades = 5
        
        for attempt in range(max_upgrades):
            # Refresh user data to get updated resources and hero stats
            user_data = await self.get_user_data()
            if not user_data:
                break
                
            heroes = user_data.get("player", {}).get("heroes", [])
            resources = user_data.get("player", {}).get("resources", {})
            
            # Find updated bonk hero
            current_bonk = None
            for hero in heroes:
                if hero.get("heroType") == "bonk":
                    current_bonk = hero
                    break
            
            if not current_bonk:
                break
            
            cost_gold = current_bonk.get("costLevelGold", 0)
            cost_green = current_bonk.get("costLevelGreen", 0)
            gold = resources.get("gold", {}).get("amount", 0)
            green_stones = resources.get("greenStones", {}).get("amount", 0)
            
            if cost_gold > 0 and cost_green > 0:
                if gold >= cost_gold and green_stones >= cost_green:
                    result = await self.level_up_hero("bonk")
                    if result:
                        upgraded_count += 1
                        new_level = current_bonk.get("level", 0) + 1
                        logger.info(f"{self.session_name} | 📈 Bonk upgraded to level {new_level} ({upgraded_count}/{max_upgrades})")
                    else:
                        # Upgrade failed (likely cooldown or other restriction)
                        logger.info(f"{self.session_name} | ⏳ Bonk upgrade failed, stopping attempts")
                        break
                else:
                    # Not enough resources
                    logger.info(f"{self.session_name} | ❌ Not enough resources for bonk upgrade (need: {cost_gold} gold, {cost_green} green)")
                    break
            else:
                # No upgrade cost or max level reached
                logger.info(f"{self.session_name} | ✅ Bonk hero is at max level or no upgrade cost")
                break
        
        if upgraded_count > 0:
            logger.info(f"{self.session_name} | 🎯 Bonk upgraded {upgraded_count} times")
        elif upgraded_count == 0 and bonk_hero:
            logger.info(f"{self.session_name} | 🎯 Bonk hero is ready but no upgrades performed")

    async def _level_up_dragon(self) -> None:
        """Level up dragon epic hero specifically, up to 3 times if resources allow and level <= 150"""
        user_data = await self.get_user_data()
        if not user_data:
            return

        heroes = user_data.get("player", {}).get("heroes", [])
        resources = user_data.get("player", {}).get("resources", {})
        current_time = int(time() * 1000)
        
        # Find dragon epic hero
        dragon_hero = None
        for hero in heroes:
            if hero.get("heroType") == "dragonEpic":
                dragon_hero = hero
                break
        
        if not dragon_hero:
            logger.info(f"{self.session_name} | ❌ No dragon epic hero found")
            return
        
        # Check if dragon level is already >= 150
        current_level = dragon_hero.get("level", 0)
        if current_level >= 150:
            logger.info(f"{self.session_name} | ✅ Dragon epic level {current_level} is above 150, skipping upgrades")
            return
        
        # Check if dragon hero is available (not in challenge)
        dragon_unlock_at = dragon_hero.get("unlockAt", 0)
        if isinstance(dragon_unlock_at, str):
            try:
                dragon_unlock_at = int(dragon_unlock_at)
            except (ValueError, TypeError):
                dragon_unlock_at = 0
        elif dragon_unlock_at is None:
            dragon_unlock_at = 0
        
        if dragon_unlock_at > current_time:
            logger.info(f"{self.session_name} | ⏳ Dragon epic hero is in challenge, unlocks at {self._format_next_time(dragon_unlock_at)}")
            return
        
        gold = resources.get("gold", {}).get("amount", 0)
        green_stones = resources.get("greenStones", {}).get("amount", 0)
        
        logger.info(f"{self.session_name} | 🐉 Dragon epic hero: {dragon_hero.get('name')} (Lv.{dragon_hero.get('level', 0)}) | 💰 {gold} | 🟢 {green_stones}")
        
        upgraded_count = 0
        max_upgrades = 3
        
        for attempt in range(max_upgrades):
            # Refresh user data to get updated resources and hero stats
            user_data = await self.get_user_data()
            if not user_data:
                break
                
            heroes = user_data.get("player", {}).get("heroes", [])
            resources = user_data.get("player", {}).get("resources", {})
            
            # Find updated dragon hero
            current_dragon = None
            for hero in heroes:
                if hero.get("heroType") == "dragonEpic":
                    current_dragon = hero
                    break
            
            if not current_dragon:
                break
            
            # Check level limit again after potential upgrades
            current_level = current_dragon.get("level", 0)
            if current_level >= 150:
                logger.info(f"{self.session_name} | ✅ Dragon epic reached level {current_level}, stopping upgrades (limit: 150)")
                break
            
            cost_gold = current_dragon.get("costLevelGold", 0)
            cost_green = current_dragon.get("costLevelGreen", 0)
            gold = resources.get("gold", {}).get("amount", 0)
            green_stones = resources.get("greenStones", {}).get("amount", 0)
            
            if cost_gold > 0 and cost_green > 0:
                if gold >= cost_gold and green_stones >= cost_green:
                    result = await self.level_up_hero("dragonEpic")
                    if result:
                        upgraded_count += 1
                        new_level = current_dragon.get("level", 0) + 1
                        logger.info(f"{self.session_name} | 📈 Dragon epic upgraded to level {new_level} ({upgraded_count}/{max_upgrades})")
                    else:
                        # Upgrade failed (likely cooldown or other restriction)
                        logger.info(f"{self.session_name} | ⏳ Dragon epic upgrade failed, stopping attempts")
                        break
                else:
                    # Not enough resources
                    logger.info(f"{self.session_name} | ❌ Not enough resources for dragon epic upgrade (need: {cost_gold} gold, {cost_green} green)")
                    break
            else:
                # No upgrade cost or max level reached
                logger.info(f"{self.session_name} | ✅ Dragon epic hero is at max level or no upgrade cost")
                break
        
        if upgraded_count > 0:
            logger.info(f"{self.session_name} | 🐉 Dragon epic upgraded {upgraded_count} times")
        elif upgraded_count == 0 and dragon_hero:
            logger.info(f"{self.session_name} | 🐉 Dragon epic hero is ready but no upgrades performed")

    async def process_bot_logic(self) -> None:
        current_time = datetime.now().strftime("%H:%M:%S")
        logger.info(f"{self.session_name} | 🎮 Start {current_time}")
        
        self._challenges_in_progress.clear()
        
        async def delay():
            await asyncio.sleep(uniform(settings.ACTION_DELAY[0], settings.ACTION_DELAY[1]))
        
        await delay()
        await self._collect_all_rewards()
        
        await delay()
        await self._collect_shop_rewards()

        if self.session_settings.PROCESS_MISSIONS:
            await delay()
            await self._process_missions()

        await delay()
        await self._use_free_gacha()
        
        if self.session_settings.BUY_GACHA_PACKS:
            await delay()
            await self._buy_gacha_packs_with_gems()

        await delay()
        await self._level_up_dragon()
        
        await delay()
        await self._level_up_bonk()
        
        await delay()
        await self._process_bonk_and_dragon_constellations()

        user_data = await self.get_user_data()
        if not user_data:
            return

        sleep_time = self._calculate_sleep_time(user_data)
        next_time = datetime.fromtimestamp(time() + sleep_time).strftime("%H:%M:%S")
        logger.info(f"{self.session_name} | 💤 → {next_time} ({self._format_time(int(sleep_time * 1000))})")
        await asyncio.sleep(sleep_time)

    def _calculate_sleep_time(self, user_data: dict) -> float:
        """Calculate optimal sleep time based on next available claim times"""
        meta = user_data.get("player", {}).get("meta", {})
        heroes = user_data.get("player", {}).get("heroes", [])
        current_time = int(time() * 1000)
        gacha_next_claim_time = meta.get("freeGachaNextClaim", 0)
        
        # Calculate next challenge claim time from heroes' unlockAt field
        next_challenge_claim_time = 0
        if heroes:
            # Find all heroes that are currently in challenges (unlockAt > current_time)
            heroes_in_challenges = []
            for hero in heroes:
                unlock_at = hero.get("unlockAt", 0)
                # Convert to int if it's a string
                if isinstance(unlock_at, str):
                    try:
                        unlock_at = int(unlock_at)
                    except (ValueError, TypeError):
                        unlock_at = 0
                elif unlock_at is None:
                    unlock_at = 0
                
                if unlock_at > current_time:
                    heroes_in_challenges.append(unlock_at)
            
            if heroes_in_challenges:
                next_challenge_claim_time = min(heroes_in_challenges)
        
        # Format timestamps for logging
        gacha_time_str = self._format_next_time(gacha_next_claim_time) if gacha_next_claim_time > 0 else "none"
        challenge_time_str = self._format_next_time(next_challenge_claim_time) if next_challenge_claim_time > 0 else "none"
        logger.info(f"{self.session_name} | ⏳ Next free Gacha claim time: {gacha_time_str}")
        logger.info(f"{self.session_name} | ⏳ Nearest Challenge finish time: {challenge_time_str}")

        # Find the nearest future claim time
        future_times = []
        if gacha_next_claim_time > current_time:
            future_times.append(gacha_next_claim_time)
        if next_challenge_claim_time > current_time:
            future_times.append(next_challenge_claim_time)
        
        if future_times:
            # Select the nearest absolute timestamp and convert to seconds for sleep calculation
            nearest_time_ms = min(future_times)
            sleep_time = (nearest_time_ms - current_time) / 1000
            # Add small random delay (1-3 minutes) to avoid exact timing patterns
            sleep_time += uniform(60, 180)  # 1-3 minutes instead of full SLEEP_TIME range
        else:
            # No future times, use default random sleep
            sleep_time = uniform(settings.SLEEP_TIME[0], settings.SLEEP_TIME[1])
        
        return sleep_time

    async def get_user_data(self) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="GET",
                url=f"https://telegram-api.sleepagotchi.com/v1/tg/getUserData",
                params=self._init_data
            )
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error getting user data: {str(e)}")
            return None

    async def spend_gacha(self, amount: int = 1, strategy: str = "free") -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="POST",
                url=f"https://telegram-api.sleepagotchi.com/v1/tg/spendGacha",
                params=self._init_data,
                json={"amount": amount, "strategy": strategy}
            )
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error consuming gacha: {str(e)}")
            return None

    async def get_constellations(self, start_index: int = 0, amount: int = 10) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="POST",
                url=f"https://telegram-api.sleepagotchi.com/v1/tg/getConstellations",
                params=self._init_data,
                json={"startIndex": start_index, "amount": amount}
            )
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error getting constellations: {str(e)}")
            return None

    def _format_heroes_for_challenge(self, heroes: list) -> list:
        formatted_heroes = []
        for i, hero in enumerate(heroes):
            hero_type = hero.get("heroType")
            if not hero_type:
                logger.error(f"{self.session_name} | ❌ Hero type not found: {hero.get('name')}")
                continue
                
            formatted_heroes.append({
                "slotId": str(i),
                "heroType": hero_type
            })
            
        return formatted_heroes

    async def _send_heroes_to_challenge(
        self,
        challenge_type: str,
        heroes: list,
        slots: list = None
    ) -> Optional[dict]:
        try:
            formatted_heroes = []
            current_time = int(time() * 1000)
            
            for i, hero in enumerate(heroes):
                hero_type = hero.get("heroType")
                if not hero_type:
                    continue
                    
                slot_index = None
                hero_class = hero.get("class")
                
                if slots:
                    for j, slot in enumerate(slots):
                        if (slot.get("unlocked", True) and 
                            slot.get("occupiedBy", "empty") == "empty" and
                            slot.get("unlockAt", 0) <= current_time and
                            (slot.get("heroClass") == hero_class or hero_class == "universal")):
                            slot_index = j
                            slots[j]["occupiedBy"] = hero_type
                            break
                else:
                    slot_index = i
                    
                if slot_index is not None:
                    formatted_heroes.append({
                        "slotId": slot_index,
                        "heroType": hero_type
                    })
                
            if not formatted_heroes:
                return None
                
            response = await self.make_request(
                method="POST",
                url="https://telegram-api.sleepagotchi.com/v1/tg/sendToChallenge",
                params=self._init_data,
                json={
                    "challengeType": challenge_type,
                    "heroes": formatted_heroes
                }
            )
            
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error sending heroes to challenge {challenge_type}: {str(e)}")
            return None

    async def claim_challenges_rewards(self) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="GET",
                url=f"https://telegram-api.sleepagotchi.com/v1/tg/claimChallengesRewards",
                params=self._init_data
            )
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error getting rewards: {str(e)}")
            return None

    async def get_daily_rewards(self) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="GET",
                url=f"https://telegram-api.sleepagotchi.com/v1/tg/getDailyRewards",
                params=self._init_data
            )
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error getting daily rewards information: {str(e)}")
            return None

    async def claim_daily_rewards(self) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="GET",
                url=f"https://telegram-api.sleepagotchi.com/v1/tg/claimDailyRewards",
                params=self._init_data
            )
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error claiming daily reward: {str(e)}")
            return None

    async def level_up_hero(self, hero_type: str) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="POST",
                url=f"https://telegram-api.sleepagotchi.com/v1/tg/levelUpHero",
                params=self._init_data,
                json={"heroType": hero_type}
            )
            return response
        except Exception as e:
            error_str = str(e)
            if "error_level_up_unavalable" in error_str:
                return None
            elif "error_level_up_no_resources" in error_str:
                return None
            elif "error_level_up_max_level" in error_str:
                return None
            else:
                return None

    async def get_shop(self) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="GET",
                url=f"https://telegram-api.sleepagotchi.com/v1/tg/getShop",
                params=self._init_data
            )
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error getting shop data: {str(e)}")
            return None

    async def buy_shop(self, slot_type: str) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="POST",
                url=f"https://telegram-api.sleepagotchi.com/v1/tg/buyShop",
                params=self._init_data,
                json={"slotType": slot_type}
            )
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error buying from shop: {str(e)}")
            return None

    async def use_redeem_code(self, code: str) -> Optional[Dict]:
        if code in self._used_redeem_codes:
            logger.info(f"{self.session_name} | 🎫 Code {code} has already been used")
            return None
            
        try:
            response = await self.make_request(
                method="POST",
                url=f"https://telegram-api.sleepagotchi.com/v1/tg/useRedeemCode",
                params=self._init_data,
                json={"code": code}
            )
            if response:
                self._used_redeem_codes.add(code)
            return response
        except Exception as e:
            if "error_redeem_limit_reached" in str(e):
                self._used_redeem_codes.add(code)
                logger.info(f"{self.session_name} | 🎫 Code {code} has already been used")
            else:
                logger.error(f"{self.session_name} | Error activating code: {str(e)}")
            return None

    async def get_referrals_info(self) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="POST",
                url=f"https://telegram-api.sleepagotchi.com/v1/tg/getReferralsInfo",
                params=self._init_data,
                json={"page": 1, "rowsPerPage": 20}
            )
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error getting referrals info: {str(e)}")
            return None

    async def claim_referral_rewards(self) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="GET",
                url=f"https://telegram-api.sleepagotchi.com/v1/tg/claimReferralRewards",
                params=self._init_data
            )
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error claiming referral rewards: {str(e)}")
            return None

    async def _process_bonk_and_dragon_constellations(self) -> None:
        """Process constellations specifically for bonk and dragon epic heroes separately"""
        try:
            all_challenges = []
            constellations = await self.get_constellations(start_index=self.session_settings.CONSTELLATION_LAST_INDEX, amount=20)
            if not constellations or "constellations" not in constellations:
                return
            
            # Collect all suitable challenges
            for constellation in constellations["constellations"]:
                for challenge in constellation.get("challenges", []):
                    resource_type = challenge.get("resourceType", "")
                    if (resource_type == "greenStones" and not self.session_settings.FARM_GREEN_STONES or
                        resource_type == "purpleStones" and not self.session_settings.FARM_PURPLE_STONES or
                        resource_type == "gold" and not self.session_settings.FARM_GOLD or
                        resource_type == "gacha" and not self.session_settings.FARM_GACHA or
                        resource_type == "points" and not self.session_settings.FARM_POINTS):
                        continue
                    received = challenge.get("received", 0)
                    value = challenge.get("value", 1)
                    
                    # Skip completed challenges
                    if received >= value:
                        continue
                        
                    progress_percentage = (received / value) if value > 0 else 1
                    
                    # Set priorities for both bonk and dragon separately
                    bonk_priority = 99
                    dragon_priority = 99
                    
                    if resource_type == "greenStones":
                        bonk_priority = self.session_settings.BONK_PRIORITY_GREEN
                        dragon_priority = getattr(self.session_settings, 'DRAGON_PRIORITY_GREEN', self.session_settings.BONK_PRIORITY_GREEN)
                    elif resource_type == "purpleStones":
                        bonk_priority = self.session_settings.BONK_PRIORITY_PURPLE
                        dragon_priority = getattr(self.session_settings, 'DRAGON_PRIORITY_PURPLE', self.session_settings.BONK_PRIORITY_PURPLE)
                    elif resource_type == "gold":
                        bonk_priority = self.session_settings.BONK_PRIORITY_GOLD
                        dragon_priority = getattr(self.session_settings, 'DRAGON_PRIORITY_GOLD', self.session_settings.BONK_PRIORITY_GOLD)
                    elif resource_type == "gacha":
                        bonk_priority = self.session_settings.BONK_PRIORITY_GACHA
                        dragon_priority = getattr(self.session_settings, 'DRAGON_PRIORITY_GACHA', self.session_settings.BONK_PRIORITY_GACHA)
                    elif resource_type == "points":
                        bonk_priority = self.session_settings.BONK_PRIORITY_POINTS
                        dragon_priority = getattr(self.session_settings, 'DRAGON_PRIORITY_POINTS', self.session_settings.BONK_PRIORITY_POINTS)
                    
                    all_challenges.append({
                        "constellation": constellation,
                        "challenge": challenge,
                        "progress_percentage": progress_percentage,
                        "bonk_priority": bonk_priority,
                        "dragon_priority": dragon_priority,
                        "constellation_index": constellation.get("index", 999)
                    })
            
            user_data = await self.get_user_data()
            if not user_data:
                return
            
            heroes = user_data.get("player", {}).get("heroes", [])
            current_time = int(time() * 1000)
            
            # Find bonk and dragon heroes
            bonk_hero = next((hero for hero in heroes if hero.get("heroType") == "bonk"), None)
            dragon_hero = next((hero for hero in heroes if hero.get("heroType") == "dragonEpic"), None)
            
            # Check bonk hero availability
            bonk_available = False
            if bonk_hero:
                bonk_unlock_at = bonk_hero.get("unlockAt", 0)
                if isinstance(bonk_unlock_at, str):
                    try:
                        bonk_unlock_at = int(bonk_unlock_at)
                    except (ValueError, TypeError):
                        bonk_unlock_at = 0
                elif bonk_unlock_at is None:
                    bonk_unlock_at = 0
                bonk_available = (bonk_unlock_at <= current_time and 
                                bonk_hero.get("heroType") not in self._challenges_in_progress)
            
            # Check dragon hero availability
            dragon_available = False
            if dragon_hero:
                dragon_unlock_at = dragon_hero.get("unlockAt", 0)
                if isinstance(dragon_unlock_at, str):
                    try:
                        dragon_unlock_at = int(dragon_unlock_at)
                    except (ValueError, TypeError):
                        dragon_unlock_at = 0
                elif dragon_unlock_at is None:
                    dragon_unlock_at = 0
                dragon_available = (dragon_unlock_at <= current_time and 
                                  dragon_hero.get("heroType") not in self._challenges_in_progress)
            
            logger.info(f"{self.session_name} | 🌟 Processing constellations for special heroes - Bonk: {bonk_available}, Dragon: {dragon_available}")
            
            if not bonk_available and not dragon_available:
                logger.info(f"{self.session_name} | ❌ No special heroes available for challenges")
                return
            
            if all_challenges:
                # Process bonk hero challenges if available
                if bonk_available:
                    bonk_sorted_challenges = sorted(all_challenges, key=lambda x: (x["constellation_index"], x["bonk_priority"], x["progress_percentage"]))
                    logger.info(f"{self.session_name} | 🎯 Processing challenges for bonk hero")
                    
                    for challenge_data in bonk_sorted_challenges:
                        constellation = challenge_data["constellation"]
                        challenge = challenge_data["challenge"]
                        challenge_type = challenge.get("challengeType")
                        
                        if challenge.get("completed", False) or challenge.get("inProgress", False):
                            continue
                        
                        slots = challenge.get("orderedSlots", [])
                        unlocked_slots = any(slot.get("unlocked", True) and slot.get("occupiedBy", "empty") == "empty" for slot in slots)
                        if not unlocked_slots:
                            continue
                        
                        min_level = challenge.get("minLevel", 0)
                        min_stars = challenge.get("minStars", 0)
                        # required_power = challenge.get("power", 0)
                        
                        # Try bonk hero
                        if (bonk_hero and
                            bonk_hero.get("level", 0) >= min_level and 
                            bonk_hero.get("stars", 0) >= min_stars):
                            
                            formatted_heroes = [{
                                "slotId": 0,
                                "heroType": bonk_hero.get("heroType")
                            }]
                            result = await self.make_request(
                                method="POST",
                                url="https://telegram-api.sleepagotchi.com/v1/tg/sendToChallenge",
                                params=self._init_data,
                                json={
                                    "challengeType": challenge_type,
                                    "heroes": formatted_heroes
                                }
                            )
                            if result:
                                logger.success(f"{self.session_name} | ✅ Successfully sent {bonk_hero.get('heroType')} to challenge {challenge_type} (priority: {challenge_data['bonk_priority']})")
                                self._challenges_in_progress.add(bonk_hero.get("heroType"))
                                bonk_available = False
                                break
                            else:
                                logger.error(f"{self.session_name} | ❌ Failed to send {bonk_hero.get('heroType')} to challenge {challenge_type} (priority: {challenge_data['bonk_priority']})")

                # Process dragon hero challenges if available
                if dragon_available:
                    dragon_sorted_challenges = sorted(all_challenges, key=lambda x: (x["constellation_index"], x["dragon_priority"], x["progress_percentage"]))
                    logger.info(f"{self.session_name} | 🐉 Processing challenges for dragon epic hero")
                    
                    for challenge_data in dragon_sorted_challenges:
                        constellation = challenge_data["constellation"]
                        challenge = challenge_data["challenge"]
                        challenge_type = challenge.get("challengeType")
                        
                        if challenge.get("completed", False) or challenge.get("inProgress", False):
                            continue
                        
                        slots = challenge.get("orderedSlots", [])
                        unlocked_slots = any(slot.get("unlocked", True) and slot.get("occupiedBy", "empty") == "empty" for slot in slots)
                        if not unlocked_slots:
                            continue
                        
                        min_level = challenge.get("minLevel", 0)
                        min_stars = challenge.get("minStars", 0)
                        # required_power = challenge.get("power", 0)
                        
                        # Try dragon hero
                        if (dragon_hero and
                            dragon_hero.get("level", 0) >= min_level and 
                            dragon_hero.get("stars", 0) >= min_stars):
                            
                            formatted_heroes = [{
                                "slotId": 0,
                                "heroType": dragon_hero.get("heroType")
                            }]
                            result = await self.make_request(
                                method="POST",
                                url="https://telegram-api.sleepagotchi.com/v1/tg/sendToChallenge",
                                params=self._init_data,
                                json={
                                    "challengeType": challenge_type,
                                    "heroes": formatted_heroes
                                }
                            )
                            if result:
                                logger.success(f"{self.session_name} | ✅ Successfully sent {dragon_hero.get('heroType')} to challenge {challenge_type} (priority: {challenge_data['dragon_priority']})")
                                self._challenges_in_progress.add(dragon_hero.get("heroType"))
                                dragon_available = False
                                break
                            else:
                                logger.error(f"{self.session_name} | ❌ Failed to send {dragon_hero.get('heroType')} to challenge {challenge_type} (priority: {challenge_data['dragon_priority']})")
                
        except Exception as e:
            logger.error(f"{self.session_name} | Error processing bonk and dragon constellations: {str(e)}")

    async def get_missions(self) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="GET",
                url="https://telegram-api.sleepagotchi.com/v1/tg/getMissions",
                params=self._init_data
            )
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error getting missions: {str(e)}")
            return None

    async def report_mission_event(self, mission_key: str) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="POST",
                url="https://telegram-api.sleepagotchi.com/v1/tg/reportMissionEvent",
                params=self._init_data,
                json={"missionKey": mission_key}
            )
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error sending mission event {mission_key}: {str(e)}")
            return None

    async def claim_mission(self, mission_key: str) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="POST",
                url="https://telegram-api.sleepagotchi.com/v1/tg/claimMission",
                params=self._init_data,
                json={"missionKey": mission_key}
            )
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error claiming reward for mission {mission_key}: {str(e)}")
            return None

    async def _process_missions(self) -> None:
        missions_data = await self.get_missions()
        if not missions_data or "missions" not in missions_data:
            return
        for mission in missions_data["missions"]:
            mission_key = mission.get("missionKey")
            claimed = mission.get("claimed", False)
            progress = mission.get("progress", 0)
            condition = mission.get("condition", 1)
            availible = mission.get("availible", False)
            rewards = mission.get("rewards", [])
            if claimed:
                continue
            reward_info = []
            for reward in rewards:
                amount = reward.get("amount", 0)
                resource_type = reward.get("resourceType", "unknown")
                reward_info.append(f"{amount} {resource_type}")
            try:
                if progress < condition:
                    logger.info(f"{self.session_name} | 📋 Sending event for mission {mission_key}")
                    await self.report_mission_event(mission_key)
                    await asyncio.sleep(0.5)

                    logger.info(f"{self.session_name} | 🎁 Attempting to claim reward for mission {mission_key}")
                    result = await self.claim_mission(mission_key)
                    if result and reward_info:
                        logger.info(f"{self.session_name} | ✨ Received: {' | '.join(reward_info)}")
                    await asyncio.sleep(0.5)
            except Exception:
                await asyncio.sleep(0.5)
                continue

async def run_tapper(tg_client: UniversalTelegramClient):
    bot = BaseBot(tg_client=tg_client)
    try:
        await bot.run()
    except InvalidSession as e:
        logger.error(f"Invalid session: {e}")
