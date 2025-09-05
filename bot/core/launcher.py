import glob
import asyncio
import argparse
import os
import subprocess
import signal
from copy import deepcopy
from random import uniform
from colorama import init, Fore, Style

from bot.utils.universal_telegram_client import UniversalTelegramClient
from bot.utils.web import run_web_and_tunnel, stop_web_and_tunnel
from bot.config import settings
from bot.core.agents import generate_random_user_agent
from bot.utils import logger, config_utils, proxy_utils, CONFIG_PATH, SESSIONS_PATH, PROXIES_PATH
from bot.core.tapper import run_tapper
from bot.core.registrator import register_sessions
from bot.utils.updater import UpdateManager

init()
shutdown_event = asyncio.Event()

def signal_handler(signum: int, frame) -> None:
    shutdown_event.set()

START_TEXT = f"""
{Fore.CYAN}🎮 Sleepagotchi LITE Bot{Style.RESET_ALL}
{Fore.GREEN}Automated constellation resource farming using bonk and dragon cards{Style.RESET_ALL}

{Fore.CYAN}Select action:{Style.RESET_ALL}

    {Fore.GREEN}1. Launch clicker{Style.RESET_ALL}
    {Fore.GREEN}2. Create session{Style.RESET_ALL}
    {Fore.GREEN}3. Create session via QR{Style.RESET_ALL}
    {Fore.GREEN}4. Upload sessions via web (BETA){Style.RESET_ALL}
    {Fore.RED}5. Remove session{Style.RESET_ALL}
    {Fore.YELLOW}6. Exit{Style.RESET_ALL}

{Fore.CYAN}Developed by: @Mffff4{Style.RESET_ALL}
{Fore.CYAN}Updated by: @ale55io{Style.RESET_ALL}
"""

API_ID = settings.API_ID
API_HASH = settings.API_HASH

def prompt_user_action() -> int:
    logger.info(START_TEXT)
    while True:
        action = input("> ").strip()
        if action.isdigit() and action in ("1", "2", "3", "4", "5", "6"):
            return int(action)
        logger.warning("Invalid action. Please enter a number between 1 and 6.")

async def process() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-a", "--action", type=int, help="Action to perform")
    parser.add_argument("--update-restart", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    # If a specific action is provided via command line, execute it once and exit
    if args.action or args.update_restart:
        await process_single_action(args.action, args.update_restart)
    else:
        # Interactive mode - loop until user chooses to exit
        await interactive_mode()

async def process_single_action(action: int = None, update_restart: bool = False) -> None:
    """Process a single action and exit (for command line usage)"""
    if not settings.USE_PROXY:
        logger.info(f"Detected {len(get_sessions(SESSIONS_PATH))} sessions | USE_PROXY=False")
    else:
        logger.info(f"Detected {len(get_sessions(SESSIONS_PATH))} sessions | "
                    f"{len(proxy_utils.get_proxies(PROXIES_PATH))} proxies")

    if not action and not update_restart:
        action = prompt_user_action()

    await execute_action(action, interactive=False)

async def interactive_mode() -> None:
    """Interactive mode that returns to menu after non-persistent actions"""
    while True:
        if not settings.USE_PROXY:
            logger.info(f"Detected {len(get_sessions(SESSIONS_PATH))} sessions | USE_PROXY=False")
        else:
            logger.info(f"Detected {len(get_sessions(SESSIONS_PATH))} sessions | "
                        f"{len(proxy_utils.get_proxies(PROXIES_PATH))} proxies")

        action = prompt_user_action()
        
        # Exit option
        if action == 6:
            logger.info(f"{Fore.YELLOW}👋 Goodbye!{Style.RESET_ALL}")
            break
        
        # Actions that should exit the program
        elif action in [1, 4]:  # Launch clicker or Web interface
            await execute_action(action, interactive=True)
            # If we reach here, it means no sessions were found for action 1
            # Continue the loop to return to menu
            if action == 1:
                logger.info(f"\n{Fore.CYAN}Returning to main menu...{Style.RESET_ALL}")
                await asyncio.sleep(2)
            else:
                break  # Exit for web interface (action 4)
        
        # Actions that should return to menu
        elif action in [2, 3, 5]:  # Create session, QR session, Remove session
            try:
                await execute_action(action, interactive=True)
                # After session management actions, show a brief pause and return to menu
                logger.info(f"\n{Fore.CYAN}Returning to main menu...{Style.RESET_ALL}")
                await asyncio.sleep(2)  # Brief pause
            except Exception as e:
                logger.error(f"Error executing action: {str(e)}")
                await asyncio.sleep(2)

async def execute_action(action: int, interactive: bool = False) -> None:
    """Execute the specified action"""
    if action == 1:
        if not API_ID or not API_HASH:
            raise ValueError("API_ID and API_HASH not found in the .env file.")
        await run_tasks(exit_on_no_sessions=not interactive)
    elif action == 2:
        await register_sessions()
    elif action == 3:
        session_name = input("Enter the session name for QR code authentication: ")
        print("Initializing QR code authentication...")
        subprocess.run(["python", "-m", "bot.utils.loginQR", "-s", session_name])
        print("QR code authentication was successful!")
    elif action == 4:
        logger.info("Starting web interface for uploading sessions...")
        signal.signal(signal.SIGINT, signal_handler)
        try:
            web_task = asyncio.create_task(run_web_and_tunnel())
            await shutdown_event.wait()
        finally:
            web_task.cancel()
            await stop_web_and_tunnel()
            print("Program terminated.")
    elif action == 5:
        await remove_session()
    elif action == 6:
        # Exit action - handled in interactive_mode
        pass

def get_sessions(sessions_folder: str) -> list[str]:
    session_names = glob.glob(f"{sessions_folder}/*.session")
    session_names += glob.glob(f"{sessions_folder}/telethon/*.session")
    session_names += glob.glob(f"{sessions_folder}/pyrogram/*.session")
    return [file.replace('.session', '') for file in sorted(session_names)]

async def remove_session() -> None:
    """Interactive session removal with numbered selection"""
    try:
        sessions = get_sessions(SESSIONS_PATH)
        
        if not sessions:
            logger.info("No sessions found to remove.")
            return
        
        # Display sessions with numbers
        logger.info(f"\n{Fore.CYAN}Available sessions:{Style.RESET_ALL}")
        for i, session in enumerate(sessions, 1):
            session_name = os.path.basename(session)
            logger.info(f"  {Fore.GREEN}{i}.{Style.RESET_ALL} {session_name}")
        
        logger.info(f"  {Fore.YELLOW}0.{Style.RESET_ALL} Cancel and return to main menu")
        
        # Get user selection
        while True:
            try:
                choice = input(f"\n{Fore.CYAN}Select session to remove (0 to cancel): {Style.RESET_ALL}").strip()
                
                if choice == "0":
                    logger.info("Operation cancelled.")
                    return
                
                choice_num = int(choice)
                if 1 <= choice_num <= len(sessions):
                    selected_session = os.path.basename(sessions[choice_num - 1])
                    break
                else:
                    logger.warning(f"Please enter a number between 0 and {len(sessions)}")
            except ValueError:
                logger.warning("Please enter a valid number")
        
        # Confirm deletion
        logger.warning(f"\n{Fore.RED}⚠️  WARNING: This will permanently delete session '{selected_session}'{Style.RESET_ALL}")
        logger.info("The following files will be removed:")
        
        # List files that will be deleted
        session_file = os.path.join(SESSIONS_PATH, f"{selected_session}.session")
        session_env_file = os.path.join(SESSIONS_PATH, f"{selected_session}.env")
        
        if os.path.exists(session_file):
            logger.info(f"  - {session_file}")
        if os.path.exists(session_env_file):
            logger.info(f"  - {session_env_file}")
        
        # Check if session exists in accounts_config.json
        accounts_config = config_utils.read_config_file(CONFIG_PATH)
        if selected_session in accounts_config:
            logger.info(f"  - Entry from {CONFIG_PATH}")
        
        confirm = input(f"\n{Fore.RED}Type 'DELETE' to confirm removal: {Style.RESET_ALL}").strip()
        
        if confirm != "DELETE":
            logger.info("Operation cancelled.")
            return
        
        # Perform deletion
        removed_files = []
        
        # Remove .session file
        if os.path.exists(session_file):
            os.remove(session_file)
            removed_files.append(session_file)
            logger.info(f"✅ Removed: {session_file}")
        
        # Remove .env file
        if os.path.exists(session_env_file):
            os.remove(session_env_file)
            removed_files.append(session_env_file)
            logger.info(f"✅ Removed: {session_env_file}")
        
        # Remove from accounts_config.json
        if selected_session in accounts_config:
            accounts_config.pop(selected_session)
            await config_utils.write_config_file(accounts_config, CONFIG_PATH)
            logger.info(f"✅ Removed entry from {CONFIG_PATH}")
        
        if removed_files:
            logger.success(f"🗑️  Session '{selected_session}' has been completely removed!")
        else:
            logger.warning(f"Session '{selected_session}' was not found or already removed.")
            
    except Exception as e:
        logger.error(f"Error removing session: {str(e)}")

async def get_tg_clients() -> list[UniversalTelegramClient]:
    session_paths = get_sessions(SESSIONS_PATH)

    if not session_paths:
        raise FileNotFoundError("Session files not found")
    tg_clients = []
    for session in session_paths:
        session_name = os.path.basename(session)

        if session_name in settings.blacklisted_sessions:
            logger.warning(f"{session_name} | Session is blacklisted | Skipping")
            continue

        accounts_config = config_utils.read_config_file(CONFIG_PATH)
        session_config: dict = deepcopy(accounts_config.get(session_name, {}))
        if 'api' not in session_config:
            session_config['api'] = {}
        api_config = session_config.get('api', {})
        api = None
        if api_config.get('api_id') in [4, 6, 2040, 10840, 21724]:
            api = config_utils.get_api(api_config)

        if api:
            client_params = {
                "session": session,
                "api": api
            }
        else:
            client_params = {
                "api_id": api_config.get("api_id", API_ID),
                "api_hash": api_config.get("api_hash", API_HASH),
                "session": session,
                "lang_code": api_config.get("lang_code", "en"),
                "system_lang_code": api_config.get("system_lang_code", "en-US")
            }

            for key in ("device_model", "system_version", "app_version"):
                if api_config.get(key):
                    client_params[key] = api_config[key]

        session_config['user_agent'] = session_config.get('user_agent', generate_random_user_agent())
        api_config.update(api_id=client_params.get('api_id') or client_params.get('api').api_id,
                          api_hash=client_params.get('api_hash') or client_params.get('api').api_hash)

        session_proxy = session_config.get('proxy')
        if not session_proxy and 'proxy' in session_config.keys():
            tg_clients.append(UniversalTelegramClient(**client_params))
            if accounts_config.get(session_name) != session_config:
                await config_utils.update_session_config_in_file(session_name, session_config, CONFIG_PATH)
            continue

        else:
            if settings.DISABLE_PROXY_REPLACE:
                proxy = session_proxy or next(iter(proxy_utils.get_unused_proxies(accounts_config, PROXIES_PATH)), None)
            else:
                proxy = await proxy_utils.get_working_proxy(accounts_config, session_proxy) \
                    if session_proxy or settings.USE_PROXY else None

            if not proxy and (settings.USE_PROXY or session_proxy):
                logger.warning(f"{session_name} | Didn't find a working unused proxy for session | Skipping")
                continue
            else:
                tg_clients.append(UniversalTelegramClient(**client_params))
                session_config['proxy'] = proxy
                if accounts_config.get(session_name) != session_config:
                    await config_utils.update_session_config_in_file(session_name, session_config, CONFIG_PATH)

    return tg_clients

async def init_config_file() -> None:
    session_paths = get_sessions(SESSIONS_PATH)

    if not session_paths:
        raise FileNotFoundError("Session files not found")
    for session in session_paths:
        session_name = os.path.basename(session)
        parsed_json = config_utils.import_session_json(session)
        if parsed_json:
            accounts_config = config_utils.read_config_file(CONFIG_PATH)
            session_config: dict = deepcopy(accounts_config.get(session_name, {}))
            session_config['user_agent'] = session_config.get('user_agent', generate_random_user_agent())
            session_config['api'] = parsed_json
            if accounts_config.get(session_name) != session_config:
                await config_utils.update_session_config_in_file(session_name, session_config, CONFIG_PATH)

async def run_tasks(exit_on_no_sessions: bool = True) -> None:
    try:
        await config_utils.restructure_config(CONFIG_PATH)
        await init_config_file()
        
        tasks = []
        
        if settings.AUTO_UPDATE:
            update_manager = UpdateManager()
            tasks.append(asyncio.create_task(update_manager.run()))
        
        tg_clients = await get_tg_clients()
        
        if not tg_clients:
            logger.warning(f"{Fore.YELLOW}No valid sessions found. Please add sessions first.{Style.RESET_ALL}")
            logger.info(f"{Fore.CYAN}Use option 2 or 3 to create sessions.{Style.RESET_ALL}")
            if exit_on_no_sessions:
                import sys
                sys.exit(1)  # Exit with code 1 to prevent auto-restart
            else:
                return  # Return to caller instead of exiting
        
        tasks.extend([asyncio.create_task(run_tapper(tg_client=tg_client)) for tg_client in tg_clients])

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            raise
    except FileNotFoundError as e:
        if "Session files not found" in str(e):
            logger.warning(f"{Fore.YELLOW}No session files found. Please add sessions first.{Style.RESET_ALL}")
            logger.info(f"{Fore.CYAN}Use option 2 or 3 to create sessions.{Style.RESET_ALL}")
            if exit_on_no_sessions:
                import sys
                sys.exit(1)  # Exit with code 1 to prevent auto-restart
            else:
                return  # Return to caller instead of exiting
        else:
            raise
