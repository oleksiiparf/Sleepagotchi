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
from bot.utils import logger, config_utils, CONFIG_PATH
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
        self._is_first_run: Optional[bool] = None
        self._init_data: Optional[str] = None
        self._current_ref_id: Optional[str] = None
        self._used_redeem_codes = set()
        self._challenges_in_progress = set()
        
        session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
        if not all(key in session_config for key in ('api', 'user_agent')):
            logger.critical("CHECK accounts_config.json as it might be corrupted")
            exit(-1)
            
        self.proxy = session_config.get('proxy')
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

    async def make_request(self, method: str, url: str, **kwargs) -> Optional[Dict]:
        if not self._http_client:
            raise InvalidSession("HTTP client not initialized")

        for attempt in range(settings.REQUEST_RETRIES):
            try:
                from bot.core.headers import get_headers
                
                headers = get_headers()
                if 'headers' in kwargs:
                    headers.update(kwargs['headers'])
                    del kwargs['headers']
                
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
                        "error_challenge_in_progress"
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
            except Exception as e:
                if not any(err in str(e) for err in silent_errors):
                    logger.error(f"{self.session_name} | Unknown error on attempt {attempt + 1}/{settings.REQUEST_RETRIES}: {str(e)}")
                return None

    async def run(self) -> None:
        if not await self.initialize_session():
            return

        random_delay = uniform(1, settings.SESSION_START_DELAY)
        logger.info(f"{self.session_name} | Bot will start in {int(random_delay)}s")
        await asyncio.sleep(random_delay)

        try:
            await self.get_tg_web_data()
            if not self._init_data:
                raise InvalidSession("Failed to initialize tg_web_data")
        except Exception as e:
            logger.error(f"{self.session_name} | Error getting tg_web_data: {str(e)}")
            return

        proxy_conn = {'connector': ProxyConnector.from_url(self._current_proxy)} if self._current_proxy else {}
        async with CloudflareScraper(timeout=aiohttp.ClientTimeout(60), **proxy_conn) as http_client:
            self._http_client = http_client

            while True:
                try:
                    session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
                    if not await self.check_and_update_proxy(session_config):
                        logger.warning(f"{self.session_name} | Could not find a working proxy. Waiting 5 minutes.")
                        await asyncio.sleep(300)
                        continue

                    await self.process_bot_logic()
                    
                except InvalidSession as e:
                    raise
                except Exception as error:
                    sleep_duration = uniform(60, 120)
                    logger.error(f"{self.session_name} | Unknown error: {error}. Waiting {int(sleep_duration)}s")
                    await asyncio.sleep(sleep_duration)

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
        next_datetime = datetime.fromtimestamp(next_time / 1000, timezone.utc)
        
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
                logger.error(f"{self.session_name} | Error spending free gacha: {str(e)}")

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

        if gems > settings.GEMS_SAFE_BALANCE:
            available_gems = gems - settings.GEMS_SAFE_BALANCE
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
                                if gems <= settings.GEMS_SAFE_BALANCE:
                                    logger.info(f"{self.session_name} | 💎 Reached safe balance of {settings.GEMS_SAFE_BALANCE} gems")
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
                                if gems <= settings.GEMS_SAFE_BALANCE:
                                    logger.info(f"{self.session_name} | 💎 Reached safe balance of {settings.GEMS_SAFE_BALANCE} gems")
                                    return
                        except Exception as e:
                            logger.error(f"{self.session_name} | Error buying pack with gems: {str(e)}")
                            break
            else:
                pass
        else:
            logger.info(f"{self.session_name} | 💎 Gems balance {gems} is below safe balance {settings.GEMS_SAFE_BALANCE}")

    async def star_up_hero(self, hero_type: str) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="POST",
                url=f"https://telegram-api.sleepagotchi.com/v1/tg/starUpHero",
                params=self._init_data,
                json={"heroType": hero_type}
            )
            return response
        except Exception as e:
            if "error_star_up_no_resources" in str(e):
                return None
            logger.error(f"{self.session_name} | Error upgrading hero stars: {str(e)}")
            return None

    async def _level_up_best_heroes(self) -> None:
        user_data = await self.get_user_data()
        if not user_data:
            return

        heroes = user_data.get("player", {}).get("heroes", [])
        resources = user_data.get("player", {}).get("resources", {})

        hero_cards = {
            card["heroType"]: card["amount"]
            for card in resources.get("heroCard", [])
            if card["amount"] > 0
        }

        gold = resources.get("gold", {}).get("amount", 0)
        green_stones = resources.get("greenStones", {}).get("amount", 0)
        gems = resources.get("gem", {}).get("amount", 0)
        purple_stones = resources.get("purpleStones", {}).get("amount", 0)

        logger.info(f"{self.session_name} | 💰 {gold} | 🟢 {green_stones} | 🟣 {purple_stones} | 💎 {gems}")
        if hero_cards:
            logger.info(f"{self.session_name} | 🎴 {len(hero_cards)} types of cards")

        heroes_by_class_and_rarity = {}
        for hero in heroes:
            hero_class = hero.get("class")
            hero_type = hero.get("heroType")
            hero_rarity = self._get_hero_rarity(hero_type)

            key = f"{hero_class}_{hero_rarity}"
            if key not in heroes_by_class_and_rarity:
                heroes_by_class_and_rarity[key] = []
            heroes_by_class_and_rarity[key].append(hero)

        best_heroes = {}
        for key, class_heroes in heroes_by_class_and_rarity.items():
            class_heroes.sort(key=lambda x: (x.get("stars", 0), x.get("level", 0), x.get("power", 0)), reverse=True)
            best_heroes[key] = class_heroes[0]

        upgraded_heroes = []
        not_enough_resources_count = 0
        unavailable_upgrades_count = 0
        cooldown_heroes = []

        for hero in heroes:
            hero_type = hero.get("heroType")
            hero_name = hero.get("name")

            if hero_type in hero_cards and hero_cards[hero_type] >= hero.get("costStar", 0):
                cards_needed = hero.get("costStar", 0)
                if cards_needed > 0:
                    result = await self.star_up_hero(hero_type)
                    if result:
                        upgraded_heroes.append(f"⭐ {hero_name}")
                        user_data = await self.get_user_data()
                        if not user_data:
                            return
                        hero_cards = {
                            card["heroType"]: card["amount"]
                            for card in user_data.get("player", {}).get("resources", {}).get("heroCard", [])
                            if card["amount"] > 0
                        }

        user_data = await self.get_user_data()
        if not user_data:
            return

        heroes = user_data.get("player", {}).get("heroes", [])
        resources = user_data.get("player", {}).get("resources", {})
        gold = resources.get("gold", {}).get("amount", 0)
        green_stones = resources.get("greenStones", {}).get("amount", 0)

        for hero in heroes:
            hero_type = hero.get("heroType")
            hero_name = hero.get("name")
            hero_class = hero.get("class")
            hero_rarity = self._get_hero_rarity(hero_type)
            current_level = hero.get("level", 0)
            
            if hero_rarity in ["epic", "legendary"]:
                continue
                
            if hero_rarity == "special":
                if current_level >= 50:
                    continue
                    
            key = f"{hero_class}_{hero_rarity}"
            if hero != best_heroes.get(key):
                continue
                
            cost_gold = hero.get("costLevelGold", 0)
            cost_green = hero.get("costLevelGreen", 0)
            
            if cost_gold > 0 and cost_green > 0:
                if gold >= cost_gold and green_stones >= cost_green:
                    result = await self.level_up_hero(hero_type)
                    if result:
                        upgraded_heroes.append(f"📈 {hero_name}")
                        user_data = await self.get_user_data()
                        if not user_data:
                            return
                        resources = user_data.get("player", {}).get("resources", {})
                        gold = resources.get("gold", {}).get("amount", 0)
                        green_stones = resources.get("greenStones", {}).get("amount", 0)
                    elif result is None:
                        unavailable_upgrades_count += 1
                        cooldown_heroes.append(hero_name)
                else:
                    not_enough_resources_count += 1

        if upgraded_heroes:
            logger.info(f"{self.session_name} | ✨ {' | '.join(upgraded_heroes)}")
        if not_enough_resources_count > 0:
            logger.info(f"{self.session_name} | ❌ {not_enough_resources_count} heroes are waiting for resources")
        if unavailable_upgrades_count > 0:
            logger.info(f"{self.session_name} | ⏳ {unavailable_upgrades_count} heroes cannot be upgraded right now")
            if cooldown_heroes:
                logger.info(f"{self.session_name} | 🕒 On cooldown: {', '.join(cooldown_heroes)}")

    async def _send_heroes_to_challenges(self) -> None:
        user_data = await self.get_user_data()
        if not user_data:
            return

        heroes = user_data.get("player", {}).get("heroes", [])
        if not heroes:
            logger.info(f"{self.session_name} | ❌ No heroes for challenges")
            return

        current_time = int(time() * 1000)
        available_heroes = [hero for hero in heroes if int(hero.get("unlockAt", 0)) <= current_time]
        
        if not available_heroes:
            logger.info(f"{self.session_name} | ❌ All heroes are busy")
            return
            
        start_index = 0
        while True:
            constellations = await self.get_constellations(start_index=start_index, amount=10)
            if not constellations or not constellations.get("constellations"):
                if start_index == 0:
                    logger.info(f"{self.session_name} | ❌ Failed to get constellations")
                break
                
            current_constellations = constellations.get("constellations", [])
            if not current_constellations:
                break
                
            for constellation in current_constellations:
                constellation_name = constellation.get("name", "Unknown")
                
                for challenge in constellation.get("challenges", []):
                    challenge_name = challenge.get("name", "Unknown challenge")
                    received = challenge.get("received", 0)
                    value = challenge.get("value", 0)
                    
                    if received >= value:
                        continue
                        
                    challenge_type = challenge.get("challengeType")
                    if not challenge_type:
                        continue
                        
                    slots = challenge.get("orderedSlots", [])
                    min_level = challenge.get("minLevel", 1)
                    min_stars = challenge.get("minStars", 1)
                    required_power = challenge.get("power", 0)
                    required_skill = challenge.get("heroSkill")
                    
                    suitable_heroes = self._find_suitable_heroes(
                        heroes=available_heroes,
                        slot_requirements=slots,
                        min_level=min_level,
                        min_stars=min_stars,
                        required_skill=required_skill,
                        required_power=required_power
                    )
                    
                    if suitable_heroes:
                        logger.info(
                            f"{self.session_name} | 🎯 {challenge_name}: "
                            f"found {len(suitable_heroes)}/{len(slots)} "
                            f"(progress {received}/{value})"
                        )
                        await self._send_heroes_to_challenge(
                            challenge_type=challenge_type,
                            heroes=suitable_heroes,
                            slots=slots
                        )
                        for hero in suitable_heroes:
                            if hero in available_heroes:
                                available_heroes.remove(hero)
                    
                    if not available_heroes:
                        logger.info(f"{self.session_name} | ❌ No more available heroes")
                        return
            
            start_index += 10

    async def process_bot_logic(self) -> None:
        current_time = datetime.now().strftime("%H:%M:%S")
        logger.info(f"{self.session_name} | 🎮 Start {current_time}")
        
        self._challenges_in_progress.clear()
        
        async def delay():
            await asyncio.sleep(uniform(settings.ACTION_DELAY[0], settings.ACTION_DELAY[1]))
        
        if self._is_first_run:
            result = await self.use_redeem_code("013738")
            if result and "rewards" in result:
                rewards = result["rewards"]
                for reward_type, reward_data in rewards.items():
                    amount = reward_data.get("amount", 0)
                    logger.info(f"{self.session_name} | 🎫 {amount} {reward_type}")
        
        
        await delay()
        await self._collect_all_rewards()
        
        await delay()
        await self._collect_shop_rewards()
        
        await delay()
        await self._process_missions()

        await delay()
        await self._use_free_gacha()
        
        if settings.BUY_GACHA_PACKS:
            await delay()
            await self._buy_gacha_packs_with_gems()
        
        await delay()
        await self._level_up_best_heroes()
        
        await delay()
        await self._send_heroes_to_challenges()
        
        constellations = await self.get_constellations()
        current_time_ms = int(time() * 1000)
        max_challenge_time = 0
        
        if constellations:
            for constellation in constellations.get("constellations", []):
                for challenge in constellation.get("challenges", []):
                    if challenge.get("received", 0) < challenge.get("value", 0):
                        slots = challenge.get("orderedSlots", [])
                        has_busy_slots = any(
                            slot.get("occupiedBy", "empty") != "empty" 
                            for slot in slots
                        )
                        if has_busy_slots:
                            challenge_time = challenge.get("time", 0)
                            if challenge_time > max_challenge_time:
                                max_challenge_time = challenge_time
        
        sleep_time = max_challenge_time if max_challenge_time > 0 else uniform(settings.SLEEP_TIME[0], settings.SLEEP_TIME[1])
        
        next_time = datetime.fromtimestamp(time() + sleep_time).strftime("%H:%M:%S")
        logger.info(f"{self.session_name} | 💤 → {next_time} ({self._format_time(int(sleep_time * 1000))})")
        await asyncio.sleep(sleep_time)

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
            logger.error(f"{self.session_name} | Error spending gacha: {str(e)}")
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

    def _get_hero_rarity(self, hero_type: str) -> Optional[str]:
        if not hero_type:
            return None
            
        if hero_type.endswith("Legendary"):
            return "legendary"
        elif hero_type.endswith("Epic"):
            return "epic"
        elif hero_type.endswith("Rare"):
            return "rare"
            
        if hero_type == "bonk":
            return "special"
            
        if "Element" in hero_type:
            if hero_type.endswith("3"):
                return "legendary"
            elif hero_type.endswith("2"):
                return "epic"
            return "rare"
            
        return "rare"

    def _find_suitable_heroes(
        self,
        heroes: list,
        slot_requirements: list,
        min_level: int,
        min_stars: int,
        required_skill: str,
        required_power: int
    ) -> list:
        suitable_heroes: list = []
        used_heroes: set = set()
        current_time: int = int(time() * 1000)
        
        busy_heroes = set()
        for slot in slot_requirements:
            occupied_by = slot.get("occupiedBy")
            if occupied_by and occupied_by != "empty":
                busy_heroes.add(occupied_by)
                
        available_heroes = [
            h for h in heroes 
            if (int(h.get("unlockAt", 0)) <= current_time and 
                h.get("heroType") not in busy_heroes and
                h.get("heroType") not in used_heroes)
        ]
        
        if not available_heroes:
            return []
        
        unlocked_slots = [
            slot for slot in slot_requirements 
            if (slot.get("unlocked", True) and 
                slot.get("occupiedBy", "empty") == "empty" and
                slot.get("unlockAt", 0) <= current_time)
        ]
        
        if not unlocked_slots:
            return []
            
        for slot in unlocked_slots:
            required_class = slot.get("heroClass")
            if not required_class:
                continue
            
            slot_hero = None
            for hero in available_heroes:
                if hero.get("heroType") in used_heroes:
                    continue
                    
                hero_class = hero.get("class")
                hero_power = hero.get("power", 0)
                hero_level = hero.get("level", 0)
                hero_stars = hero.get("stars", 0)
                
                if hero_class != "universal" and hero_class != required_class:
                    continue
                    
                if hero_level < min_level:
                    continue
                    
                if hero_stars < min_stars:
                    continue
                    
                if hero_power < required_power:
                    continue
                    
                slot_hero = hero
                break
                    
            if slot_hero:
                suitable_heroes.append(slot_hero)
                used_heroes.add(slot_hero.get("heroType"))
                available_heroes.remove(slot_hero)
                
        return suitable_heroes

    async def _process_constellation(self, constellation: dict) -> None:
        try:
            constellation_name = constellation.get("name", "Unknown constellation")
            challenges = constellation.get("challenges", [])
            
            logger.info(f"{self.session_name} | 🌟 Processing constellation: {constellation_name}")
            logger.info(f"{self.session_name} | 📋 Total challenges: {len(challenges)}")
            
            def get_challenge_priority(challenge):
                resource_type = challenge.get("resourceType", "")
                value = challenge.get("value", 0)
                
                priorities = {
                    "gacha": 5,
                    "points": 3,
                    "purpleStones": 2,
                    "greenStones": 1,
                    "gold": 4
                }
                
                resource_priority = priorities.get(resource_type, 0)
                return (resource_priority, value)
                
            sorted_challenges = sorted(
                challenges,
                key=get_challenge_priority,
                reverse=True
            )
            
            for challenge in sorted_challenges:
                try:
                    challenge_name = challenge.get("name", "Unknown challenge")
                    challenge_type = challenge.get("challengeType")
                    
                    logger.info(f"{self.session_name} | 🎯 Checking challenge: {challenge_name}")
                    
                    if not challenge_type:
                        logger.error(f"{self.session_name} | ❌ Challenge type not specified for {challenge_name}")
                        continue
                    
                    value = challenge.get("value", 0)
                    received = challenge.get("received", 0)
                    if value <= received:
                        logger.info(f"{self.session_name} | ✅ Challenge already completed ({received}/{value})")
                        continue
                    
                    unlock_at = challenge.get("unlockAt", 0)
                    current_time = int(time() * 1000)
                    if unlock_at > current_time:
                        logger.info(f"{self.session_name} | ⏳ Challenge not available yet")
                        continue
                    
                    min_level = challenge.get("minLevel", 1)
                    min_stars = challenge.get("minStars", 1)
                    required_skill = challenge.get("heroSkill")
                    required_power = challenge.get("power", 0)
                    
                    slots = challenge.get("orderedSlots", [])
                    total_slots = len([s for s in slots if s.get("unlocked", True)])
                    
                    logger.info(f"{self.session_name} | 📊 Requirements: level {min_level}+, stars {min_stars}+, power {required_power}+")
                    logger.info(f"{self.session_name} | 🎯 Total slots: {total_slots}")
                    
                    user_data = await self.get_user_data()
                    if not user_data:
                        logger.error(f"{self.session_name} | ❌ Failed to get user data")
                        continue
                        
                    heroes = user_data.get("player", {}).get("heroes", [])
                    
                    suitable_heroes = self._find_suitable_heroes(
                        heroes,
                        slots,
                        min_level,
                        min_stars,
                        required_skill,
                        required_power
                    )
                    
                    if suitable_heroes:
                        logger.info(
                            f"{self.session_name} | 🎯 {challenge_name}: "
                            f"found {len(suitable_heroes)}/{total_slots} heroes "
                            f"(collected {received}/{value})"
                        )
                        await self._send_heroes_to_challenge(challenge_type, suitable_heroes, slots)
                    else:
                        logger.info(f"{self.session_name} | ❌ No suitable heroes found for challenge {challenge_name}")
                except Exception as e:
                    logger.error(f"{self.session_name} | Error processing challenge {challenge_name}: {str(e)}")
                    continue
        except Exception as e:
            logger.error(f"{self.session_name} | Error processing constellation {constellation_name}: {str(e)}")
            return

    async def _process_all_constellations(self) -> None:
        try:
            constellations = await self.get_constellations()
            if not constellations:
                return
                
            for constellation in constellations:
                await self._process_constellation(constellation)
        except Exception as e:
            logger.error(f"{self.session_name} | Error processing constellations: {str(e)}")

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
            available = mission.get("available", False)
            rewards = mission.get("rewards", [])

            if claimed:
                continue

            reward_info = []
            for reward in rewards:
                amount = reward.get("amount", 0)
                resource_type = reward.get("resourceType", "unknown")
                reward_info.append(f"{amount} {resource_type}")

            if progress < condition:
                logger.info(f"{self.session_name} | 📋 Sending event for mission {mission_key}")
                await self.report_mission_event(mission_key)
                await asyncio.sleep(2)

                logger.info(f"{self.session_name} | 🎁 Attempting to claim reward for mission {mission_key}")
                result = await self.claim_mission(mission_key)
                if result:
                    if reward_info:
                        logger.info(f"{self.session_name} | ✨ Received: {' | '.join(reward_info)}")
                await asyncio.sleep(0.5)

async def run_tapper(tg_client: UniversalTelegramClient):
    bot = BaseBot(tg_client=tg_client)
    try:
        await bot.run()
    except InvalidSession as e:
        logger.error(f"Invalid session: {e}")
