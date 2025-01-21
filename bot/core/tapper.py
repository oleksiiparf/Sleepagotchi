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
                        logger.error(f"{self.session_name} | Cloudflare protection on attempt {attempt + 1}/{settings.REQUEST_RETRIES}")
                        logger.error(f"{self.session_name} | Status: {response.status}")
                        logger.error(f"{self.session_name} | URL: {url}")
                        logger.error(f"{self.session_name} | Headers: {dict(response.headers)}")
                        
                        if attempt < settings.REQUEST_RETRIES - 1:
                            await asyncio.sleep(uniform(1, 3))
                            continue
                        return None
                        
                    error_json = json.loads(response_text)
                    error_name = error_json.get('name', 'Unknown')
                    error_message = error_json.get('message', 'No message')
                    
                    if "error_level_up_no_resources" in error_message or "error_star_up_no_resources" in error_message:
                        raise Exception(error_message)
                        
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
        user_data = await self.get_user_data()
        if not user_data:
            return
            
        current_time = int(time() * 1000)
        shop_next_claim = user_data.get("player", {}).get("meta", {}).get("shopNextClaimAt", 0)
        
        if shop_next_claim > current_time:
            logger.info(f"{self.session_name} | ⏳ Shop → {self._format_next_time(shop_next_claim)}")
            return
            
        shop_data = await self.get_shop()
        if not shop_data:
            return

        free_slots = [slot for slot in shop_data.get("shop", []) if slot.get("slotType") == "free"]
        if not free_slots:
            logger.info(f"{self.session_name} | ❌ No free items in shop")
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
                            logger.info(f"{self.session_name} | ✨ Free items: {' | '.join(rewards)}")
                    elif result.get("status") == "ok" and content_info:
                        logger.info(f"{self.session_name} | ✨ Free items: {' | '.join(content_info)}")
                    else:
                        logger.info(f"{self.session_name} | ✨ Free items collected")
                else:
                    logger.info(f"{self.session_name} | ❌ Failed to collect free items")
            except Exception as e:
                logger.error(f"{self.session_name} | Error collecting free items: {str(e)}")
                continue

    async def _use_free_gacha(self) -> None:
        user_data = await self.get_user_data()
        if not user_data:
            return

        meta = user_data.get("player", {}).get("meta", {})
        resources = user_data.get("player", {}).get("resources", {})
        current_time = int(time() * 1000)
        next_claim = meta.get("freeGachaNextClaim", 0)
        gacha_amount = resources.get("gacha", {}).get("amount", 0)
        
        if next_claim > current_time:
            logger.info(f"{self.session_name} | ⏳ Gacha → {self._format_next_time(next_claim)}")
        else:
            try:
                response = await self.make_request(
                    method="POST",
                    url="https://tgapi.sleepagotchi.com/v1/tg/spendGacha",
                    params=self._init_data,
                    json={"amount": 1, "strategy": "free"}
                )
                if response and "rewards" in response:
                    for reward in response["rewards"]:
                        reward_name = reward.get("name", "Unknown")
                        reward_type = reward.get("type", "Unknown")
                        logger.info(f"{self.session_name} | 🎁 {reward_name} ({reward_type})")
                user_data = await self.get_user_data()
                if user_data:
                    gacha_amount = user_data.get("player", {}).get("resources", {}).get("gacha", {}).get("amount", 0)
            except Exception as e:
                logger.error(f"{self.session_name} | Error spending gacha: {str(e)}")
            
        if gacha_amount > 0:
            logger.info(f"{self.session_name} | 🎲 {gacha_amount} attempts")
            
            for _ in range(gacha_amount):
                result = await self.spend_gacha(amount=1, strategy="gacha")
                if result and "rewards" in result:
                    for reward in result["rewards"]:
                        reward_name = reward.get("name", "Unknown")
                        reward_type = reward.get("type", "Unknown")
                        logger.info(f"{self.session_name} | 🎁 {reward_name} ({reward_type})")
                await asyncio.sleep(1)

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

        logger.info(f"{self.session_name} | 💰 {gold} | 💎 {green_stones}")
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

        for hero in heroes:
            hero_type = hero.get("heroType")
            hero_name = hero.get("name")
            hero_class = hero.get("class")
            hero_rarity = self._get_hero_rarity(hero_type)
            
            if hero_rarity in ["epic", "legendary"]:
                continue
                
            if hero_rarity == "special":
                current_level = hero.get("level", 0)
                if current_level >= 50:
                    continue
                    
            key = f"{hero_class}_{hero_rarity}"
            if hero != best_heroes.get(key):
                continue
                
            current_stars = hero.get("stars", 0)
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
                        # Обновляем балансы после каждого повышения уровня
                        user_data = await self.get_user_data()
                        if not user_data:
                            return
                        resources = user_data.get("player", {}).get("resources", {})
                        gold = resources.get("gold", {}).get("amount", 0)
                        green_stones = resources.get("greenStones", {}).get("amount", 0)
                else:
                    not_enough_resources_count += 1

        if upgraded_heroes:
            logger.info(f"{self.session_name} | ✨ {' | '.join(upgraded_heroes)}")
        if not_enough_resources_count > 0:
            logger.info(f"{self.session_name} | ❌ {not_enough_resources_count} heroes are waiting for resources")

    async def star_up_hero(self, hero_type: str) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="POST",
                url=f"https://tgapi.sleepagotchi.com/v1/tg/starUpHero",
                params=self._init_data,
                json={"heroType": hero_type}
            )
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error upgrading star level of hero: {str(e)}")
            return None

    async def _send_heroes_to_challenges(self) -> None:
        user_data = await self.get_user_data()
        if not user_data:
            return

        heroes = user_data.get("player", {}).get("heroes", [])
        if not heroes:
            logger.info(f"{self.session_name} | ❌ No heroes for challenges")
            return

        available_heroes = [hero for hero in heroes if hero.get("unlockAt", 0) == 0]
        if not available_heroes:
            logger.info(f"{self.session_name} | ❌ All heroes are busy")
            return
            
        logger.info(f"{self.session_name} | 👥 Available heroes: {len(available_heroes)}")

        constellations = await self.get_constellations()
        if not constellations:
            return
            
        for constellation in constellations.get("constellations", []):
            await self._process_constellation(constellation)

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
        await self._use_free_gacha()
        
        await delay()
        await self._level_up_best_heroes()
        
        await delay()
        await self._send_heroes_to_challenges()
        
        sleep_time = uniform(settings.SLEEP_TIME[0], settings.SLEEP_TIME[1])
        next_time = datetime.fromtimestamp(time() + sleep_time).strftime("%H:%M:%S")
        logger.info(f"{self.session_name} | 💤 → {next_time} ({self._format_time(int(sleep_time * 1000))})")
        await asyncio.sleep(sleep_time)

    async def get_user_data(self) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="GET",
                url=f"https://tgapi.sleepagotchi.com/v1/tg/getUserData",
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
                url=f"https://tgapi.sleepagotchi.com/v1/tg/spendGacha",
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
                url=f"https://tgapi.sleepagotchi.com/v1/tg/getConstellations",
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
        heroes: list
    ) -> Optional[dict]:
        try:
            formatted_heroes = self._format_heroes_for_challenge(heroes)
            if not formatted_heroes:
                logger.error(f"{self.session_name} | ❌ Failed to format heroes for sending")
                return None
                
            hero_names = [f"{hero.get('name')} ({hero.get('class')})" for hero in heroes]
            logger.info(f"{self.session_name} | 📤 Sending to challenge {challenge_type}")
            logger.info(f"{self.session_name} | 👥 Heroes: {', '.join(hero_names)}")
            
            response = await self.make_request(
                method="POST",
                url="https://tgapi.sleepagotchi.com/v1/tg/sendToChallenge",
                params=self._init_data,
                json={
                    "type": challenge_type,
                    "heroes": formatted_heroes
                }
            )
            
            if response:
                logger.info(f"{self.session_name} | ✅ Successfully sent to challenge {challenge_type}")
            else:
                logger.error(f"{self.session_name} | ❌ Error sending to challenge {challenge_type}")
                
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error sending heroes to challenge {challenge_type}: {str(e)}")
            return None

    async def claim_challenges_rewards(self) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="GET",
                url=f"https://tgapi.sleepagotchi.com/v1/tg/claimChallengesRewards",
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
                url=f"https://tgapi.sleepagotchi.com/v1/tg/getDailyRewards",
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
                url=f"https://tgapi.sleepagotchi.com/v1/tg/claimDailyRewards",
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
                url=f"https://tgapi.sleepagotchi.com/v1/tg/levelUpHero",
                params=self._init_data,
                json={"heroType": hero_type}
            )
            return response
        except Exception as e:
            logger.error(f"{self.session_name} | Error leveling up hero: {str(e)}")
            return None

    async def get_shop(self) -> Optional[Dict]:
        try:
            response = await self.make_request(
                method="GET",
                url=f"https://tgapi.sleepagotchi.com/v1/tg/getShop",
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
                url=f"https://tgapi.sleepagotchi.com/v1/tg/buyShop",
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
                url=f"https://tgapi.sleepagotchi.com/v1/tg/useRedeemCode",
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
                url=f"https://tgapi.sleepagotchi.com/v1/tg/getReferralsInfo",
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
                url=f"https://tgapi.sleepagotchi.com/v1/tg/claimReferralRewards",
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

    def _check_slot_requirements(
        self,
        hero: dict,
        slot: dict,
        min_level: int,
        min_stars: int,
        required_skill: str,
        required_power: int
    ) -> bool:
        hero_class = hero.get("class")
        required_class = slot.get("heroClass")
        
        if hero_class != "universal" and hero_class != required_class:
            return False

        hero_level = hero.get("level", 0)
        if hero_level < min_level:
            return False

        hero_stars = hero.get("stars", 0)
        if hero_stars < min_stars:
            return False

        hero_power = hero.get("power", 0)
        if hero_power < required_power:
            return False

        if required_skill:
            hero_skills = hero.get("skills", [])
            if not hero_skills or required_skill not in hero_skills:
                return False

        return True

    def _find_suitable_heroes(
        self,
        heroes: list,
        slot_requirements: list,
        min_level: int,
        min_stars: int,
        required_skill: str,
        required_power: int
    ) -> list:
        suitable_heroes = []
        used_heroes = set()
        available_heroes = [h for h in heroes if not h.get("busy")]
        
        for slot_index, slot in enumerate(slot_requirements):
            if slot.get("optional", False) and not slot.get("unlocked", True):
                continue

            required_class = slot.get("heroClass")
            if not required_class:
                continue
            
            found_hero = None
            
            for hero in available_heroes:
                if hero.get("id") not in used_heroes:
                    if self._check_slot_requirements(
                        hero, 
                        slot, 
                        min_level, 
                        min_stars, 
                        required_skill, 
                        required_power
                    ):
                        found_hero = hero
                        break
            
            if found_hero:
                suitable_heroes.append(found_hero)
                used_heroes.add(found_hero.get("id"))
            else:
                return []

        return suitable_heroes

    async def _process_constellation(self, constellation: dict) -> None:
        try:
            constellation_name = constellation.get("name", "Unknown constellation")
            challenges = constellation.get("challenges", [])
            
            for challenge in challenges:
                try:
                    challenge_name = challenge.get("name", "Unknown challenge")
                    challenge_type = challenge.get("challengeType")
                    
                    if not challenge_type:
                        logger.error(f"{self.session_name} | ❌ Challenge type not specified for {challenge_name}")
                        continue
                    
                    min_level = challenge.get("minLevel", 1)
                    min_stars = challenge.get("minStars", 1)
                    required_skill = challenge.get("heroSkill")
                    required_power = challenge.get("power", 0)
                    
                    slots = challenge.get("orderedSlots", [])
                    
                    user_data = await self.get_user_data()
                    if not user_data:
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
                        await self._send_heroes_to_challenge(challenge_type, suitable_heroes)
                    else:
                        pass
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


async def run_tapper(tg_client: UniversalTelegramClient):
    bot = BaseBot(tg_client=tg_client)
    try:
        await bot.run()
    except InvalidSession as e:
        logger.error(f"Invalid Session: {e}")
