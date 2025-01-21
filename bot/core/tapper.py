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

        for slot in shop_data.get("shop", []):
            if slot.get("slotType") == "free":
                logger.info(f"{self.session_name} | ✨ Free items")
                await self.buy_shop("free")
                break

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
        
        # Получаем доступные карточки героев
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

        # Группируем героев по классам и редкости
        heroes_by_class_and_rarity = {}
        for hero in heroes:
            hero_class = hero.get("class")
            hero_type = hero.get("heroType")
            hero_rarity = self._get_hero_rarity(hero_type)
            
            key = f"{hero_class}_{hero_rarity}"
            if key not in heroes_by_class_and_rarity:
                heroes_by_class_and_rarity[key] = []
            heroes_by_class_and_rarity[key].append(hero)

        # Определяем лучших героев в каждом классе по редкости
        best_heroes = {}
        for key, class_heroes in heroes_by_class_and_rarity.items():
            class_heroes.sort(key=lambda x: (x.get("stars", 0), x.get("level", 0), x.get("power", 0)), reverse=True)
            best_heroes[key] = class_heroes[0]

        upgraded_heroes = []
        not_enough_resources_count = 0

        # Повышение звезд героям
        for hero in heroes:
            hero_type = hero.get("heroType")
            hero_name = hero.get("name")
            hero_class = hero.get("class")
            hero_rarity = self._get_hero_rarity(hero_type)
            
            # Пропускаем epic и legendary героев
            if hero_rarity in ["epic", "legendary"]:
                continue
                
            # Особый случай для Bonk
            if hero_rarity == "special":
                current_level = hero.get("level", 0)
                if current_level >= 50:  # Ограничиваем прокачку Bonk до 50 уровня
                    continue
                    
            # Проверяем, является ли герой лучшим в своем классе и редкости
            key = f"{hero_class}_{hero_rarity}"
            if hero != best_heroes.get(key):
                continue
                
            # Повышаем звезды если есть карточки
            current_stars = hero.get("stars", 0)
            if hero_type in hero_cards and hero_cards[hero_type] >= hero.get("costStar", 0):
                cards_needed = hero.get("costStar", 0)
                if cards_needed > 0:
                    result = await self.star_up_hero(hero_type)
                    if result:
                        upgraded_heroes.append(f"⭐ {hero_name}")
                        # Обновляем данные после повышения звезд
                        user_data = await self.get_user_data()
                        if not user_data:
                            return
                        gold = user_data.get("player", {}).get("resources", {}).get("gold", {}).get("amount", 0)
                        green_stones = user_data.get("player", {}).get("resources", {}).get("greenStones", {}).get("amount", 0)
                        hero_cards = {
                            card["heroType"]: card["amount"] 
                            for card in user_data.get("player", {}).get("resources", {}).get("heroCard", [])
                            if card["amount"] > 0
                        }

        # Прокачка уровней
        for hero in heroes:
            hero_type = hero.get("heroType")
            hero_name = hero.get("name")
            hero_class = hero.get("class")
            hero_rarity = self._get_hero_rarity(hero_type)
            current_level = hero.get("level", 0)
            
            # Пропускаем epic и legendary героев
            if hero_rarity in ["epic", "legendary"]:
                continue
                
            # Особый случай для Bonk
            if hero_rarity == "special":
                if current_level >= 50:  # Ограничиваем прокачку Bonk до 50 уровня
                    continue
                    
            # Проверяем, является ли герой лучшим в своем классе и редкости
            key = f"{hero_class}_{hero_rarity}"
            if hero != best_heroes.get(key):
                continue
                
            # Прокачиваем уровень если есть ресурсы
            cost_gold = hero.get("costLevelGold", 0)
            cost_green = hero.get("costLevelGreen", 0)
            
            if cost_gold > 0 and cost_green > 0:
                if gold >= cost_gold and green_stones >= cost_green:
                    result = await self.level_up_hero(hero_type)
                    if result:
                        upgraded_heroes.append(f"📈 {hero_name}")
                        gold -= cost_gold
                        green_stones -= cost_green
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

        available_heroes = [
            {
                "name": hero.get("name"),
                "type": hero.get("heroType"),
                "class": hero.get("class"),
                "level": hero.get("level", 1),
                "stars": hero.get("stars", 1),
                "rarity": self._get_hero_rarity(hero.get("heroType"))
            }
            for hero in available_heroes
        ]

        active_challenges = set()
        constellations = await self.get_constellations(start_index=0, amount=10)
        if not constellations:
            return

        for constellation in constellations.get("constellations", []):
            for challenge in constellation.get("challenges", []):
                if challenge.get("status") == "inProgress":
                    active_challenges.add(challenge.get("challengeType"))

        used_heroes = set()
        current_time = int(time() * 1000)

        for constellation in constellations.get("constellations", []):
            for challenge in constellation.get("challenges", []):
                challenge_type = challenge.get("challengeType")
                
                if challenge_type in active_challenges:
                    continue
                    
                total_value = challenge.get("value", 0)
                received_value = challenge.get("received", 0)
                remaining_value = total_value - received_value
                
                if remaining_value <= 0:
                    continue
                    
                unlock_at = challenge.get("unlockAt", 0)
                if unlock_at > current_time:
                    continue
                    
                slots = challenge.get("orderedSlots", [])
                if not slots or any(slot.get("occupiedBy") != "empty" or slot.get("unlockAt", 0) > current_time for slot in slots):
                    continue

                challenge_name = challenge.get("name", "Unknown")
                challenge_time = challenge.get("time", 0)
                challenge_reward = remaining_value
                challenge_resource = challenge.get("resourceType", "unknown")

                required_heroes_count = len(slots)
                if len(available_heroes) - len(used_heroes) < required_heroes_count:
                    continue

                min_level = challenge.get("minLevel", 1)
                min_stars = challenge.get("minStars", 1)
                
                suitable_heroes = []
                used_heroes_for_challenge = set()

                for slot_index, slot in enumerate(slots):
                    required_class = slot.get("heroClass")
                    required_rarity = slot.get("heroRarity", "rare").lower()
                    hero_found = False
                    
                    for hero in available_heroes:
                        hero_type = hero.get("type")
                        if (hero_type not in used_heroes and 
                            hero_type not in used_heroes_for_challenge and
                            hero.get("class") == required_class and 
                            hero.get("level", 1) >= min_level and
                            hero.get("stars", 1) >= min_stars and
                            hero.get("rarity") == required_rarity):
                            
                            suitable_heroes.append(hero)
                            used_heroes_for_challenge.add(hero_type)
                            hero_found = True
                            break
                    
                    if not hero_found:
                        break

                if len(suitable_heroes) < required_heroes_count:
                    continue

                challenge_heroes = []
                for i, hero in enumerate(suitable_heroes):
                    hero_type = hero.get("type")
                    if not hero_type:
                        logger.error(
                            f"{self.session_name} | Hero type not found: {hero.get('name')}"
                        )
                        continue

                    challenge_heroes.append({
                        "slotId": i,
                        "heroType": hero_type
                    })

                if len(challenge_heroes) != required_heroes_count:
                    logger.error(
                        f"{self.session_name} | Incorrect number of heroes for challenge {challenge_name}: "
                        f"needed {required_heroes_count}, prepared {len(challenge_heroes)}"
                    )
                    continue

                result = await self.send_to_challenge(
                    challenge_type=challenge_type,
                    heroes=challenge_heroes
                )
                
                if result:
                    logger.info(f"{self.session_name} | ✅ {len(challenge_heroes)}👥 → {challenge_name} | ⏱️ {challenge_time}m | 💎 {challenge_reward} {challenge_resource} ({received_value}/{total_value})")
                    active_challenges.add(challenge_type)
                    used_heroes.update(used_heroes_for_challenge)
                else:
                    logger.error(
                        f"{self.session_name} | Failed to send heroes to challenge {challenge_name}. "
                        f"Challenge type: {challenge_type}"
                    )
                
                if len(available_heroes) == len(used_heroes):
                    logger.info(f"{self.session_name} | ✅ All heroes are busy")
                    return

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

    async def send_to_challenge(
        self, 
        challenge_type: str,
        heroes: List[Dict[str, str]]
    ) -> Optional[Dict]:
        try:
            if not isinstance(heroes, list):
                logger.error(
                    f"{self.session_name} | Invalid hero data type: {type(heroes)}"
                )
                return None
                
            hero_types = set()
            for i, hero in enumerate(heroes):
                if not isinstance(hero, dict):
                    logger.error(
                        f"{self.session_name} | Invalid format for hero {i}: {hero}"
                    )
                    return None
                    
                slot_id = hero.get("slotId")
                hero_type = hero.get("heroType")
                
                if not isinstance(slot_id, int):
                    logger.error(
                        f"{self.session_name} | Invalid type for slotId for hero {i}: {type(slot_id)}"
                    )
                    return None
                    
                if not isinstance(hero_type, str):
                    logger.error(
                        f"{self.session_name} | Invalid type for heroType for hero {i}: {type(hero_type)}"
                    )
                    return None
                    
                if hero_type in hero_types:
                    logger.error(
                        f"{self.session_name} | Duplicate hero {hero_type}"
                    )
                    return None
                hero_types.add(hero_type)
                    
                if slot_id != i:
                    logger.error(
                        f"{self.session_name} | Invalid slotId order: {slot_id} should be {i}"
                    )
                    return None

            response = await self.make_request(
                method="POST",
                url=f"https://tgapi.sleepagotchi.com/v1/tg/sendToChallenge",
                params=self._init_data,
                json={
                    "challengeType": challenge_type,
                    "heroes": heroes
                }
            )
            
            return response
            
        except Exception as e:
            logger.error(f"{self.session_name} | Error sending to challenge: {str(e)}")
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
            
        # Определяем редкость по суффиксу
        if hero_type.endswith("Legendary"):
            return "legendary"
        elif hero_type.endswith("Epic"):
            return "epic"
        elif hero_type.endswith("Rare"):
            return "rare"
            
        # Особый случай для Bonk
        if hero_type == "bonk":
            return "special"
            
        # Для элементалей
        if "Element" in hero_type:
            if hero_type.endswith("3"):
                return "legendary"
            elif hero_type.endswith("2"):
                return "epic"
            return "rare"
            
        # По умолчанию считаем rare
        return "rare"


async def run_tapper(tg_client: UniversalTelegramClient):
    bot = BaseBot(tg_client=tg_client)
    try:
        await bot.run()
    except InvalidSession as e:
        logger.error(f"Invalid Session: {e}")
