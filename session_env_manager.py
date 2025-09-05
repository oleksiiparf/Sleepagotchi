#!/usr/bin/env python3
"""
Session Environment Configuration Manager
Manage session-specific settings through individual .env files
"""

import os
import shutil
import glob
from typing import Dict, List


def get_sessions_folder() -> str:
    """Get the sessions folder path"""
    return "sessions"


def get_session_env_files() -> List[str]:
    """Get list of all session .env files"""
    sessions_folder = get_sessions_folder()
    env_files = glob.glob(os.path.join(sessions_folder, "*.env"))
    return [os.path.basename(f).replace('.env', '') for f in env_files]


def get_session_files() -> List[str]:
    """Get list of all session files (both .session and .env)"""
    sessions_folder = get_sessions_folder()
    session_files = glob.glob(os.path.join(sessions_folder, "*.session"))
    return [os.path.basename(f).replace('.session', '') for f in session_files]


def create_session_env_file(session_name: str, template_path: str = ".env-session") -> str:
    """Create a session-specific .env file from template"""
    sessions_folder = get_sessions_folder()
    session_env_file = os.path.join(sessions_folder, f"{session_name}.env")
    
    if os.path.exists(session_env_file):
        print(f"Session config already exists: {session_env_file}")
        return session_env_file
    
    if os.path.exists(template_path):
        shutil.copy2(template_path, session_env_file)
        print(f"✅ Created session config from template: {session_env_file}")
    else:
        # Create default session config
        default_config = f"""# Session-specific configuration for {session_name}

# Gacha and spending settings
BUY_GACHA_PACKS=False
SPEND_GACHAS=False
GEMS_SAFE_BALANCE=100000
PROCESS_MISSIONS=False

# Resource farming settings
FARM_GREEN_STONES=True
FARM_PURPLE_STONES=True
FARM_GOLD=True
FARM_GACHA=True
FARM_POINTS=True

# Constellation settings (empty = use API value, number = manual override)
CONSTELLATION_LAST_INDEX=

# Priority for bonk hero (1 = highest, 5 = lowest)
BONK_PRIORITY_GREEN=3
BONK_PRIORITY_PURPLE=4
BONK_PRIORITY_GOLD=1
BONK_PRIORITY_GACHA=2
BONK_PRIORITY_POINTS=5

# Priority for dragon epic hero (1 = highest, 5 = lowest)
DRAGON_PRIORITY_GREEN=2
DRAGON_PRIORITY_PURPLE=1
DRAGON_PRIORITY_GOLD=3
DRAGON_PRIORITY_GACHA=4
DRAGON_PRIORITY_POINTS=5
"""
        
        with open(session_env_file, 'w') as f:
            f.write(default_config)
        print(f"✅ Created default session config: {session_env_file}")
    
    return session_env_file


def read_session_env_file(session_name: str) -> Dict[str, str]:
    """Read session .env file and return as dictionary"""
    sessions_folder = get_sessions_folder()
    session_env_file = os.path.join(sessions_folder, f"{session_name}.env")
    
    if not os.path.exists(session_env_file):
        print(f"❌ Session config not found: {session_env_file}")
        return {}
    
    config = {}
    with open(session_env_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                config[key.strip()] = value.strip()
    
    return config


def update_session_setting(session_name: str, setting_name: str, value: str) -> bool:
    """Update a specific setting in session's .env file"""
    sessions_folder = get_sessions_folder()
    session_env_file = os.path.join(sessions_folder, f"{session_name}.env")
    
    if not os.path.exists(session_env_file):
        print(f"❌ Session config not found: {session_env_file}")
        return False
    
    try:
        # Read current file
        with open(session_env_file, 'r') as f:
            lines = f.readlines()
        
        # Update the setting
        setting_updated = False
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            if line_stripped.startswith(f"{setting_name}="):
                lines[i] = f"{setting_name}={value}\n"
                setting_updated = True
                break
        
        # If setting wasn't found, add it
        if not setting_updated:
            lines.append(f"{setting_name}={value}\n")
        
        # Write back to file
        with open(session_env_file, 'w') as f:
            f.writelines(lines)
        
        print(f"✅ Updated {setting_name}={value} in {session_name}.env")
        return True
        
    except Exception as e:
        print(f"❌ Failed to update session config: {e}")
        return False


def show_session_config(session_name: str) -> None:
    """Display session configuration"""
    config = read_session_env_file(session_name)
    
    if not config:
        return
    
    print(f"\n=== Configuration for session: {session_name} ===")
    
    # Farming settings
    print("\n🌾 Farming Settings:")
    farming_settings = [
        ('FARM_GREEN_STONES', 'Green Stones'),
        ('FARM_PURPLE_STONES', 'Purple Stones'),
        ('FARM_GOLD', 'Gold'),
        ('FARM_GACHA', 'Gacha'),
        ('FARM_POINTS', 'Points')
    ]
    
    for key, label in farming_settings:
        value = config.get(key, 'Not set')
        emoji = "✅" if value == "True" else "❌" if value == "False" else "❓"
        print(f"  {emoji} {label}: {value}")
    
    # Bonk Priority settings
    print("\n🎯 Bonk Hero Priority Settings:")
    bonk_priority_settings = [
        ('BONK_PRIORITY_GREEN', 'Green Stones'),
        ('BONK_PRIORITY_PURPLE', 'Purple Stones'),
        ('BONK_PRIORITY_GOLD', 'Gold'),
        ('BONK_PRIORITY_GACHA', 'Gacha'),
        ('BONK_PRIORITY_POINTS', 'Points')
    ]
    
    for key, label in bonk_priority_settings:
        value = config.get(key, 'Not set')
        print(f"  🔢 {label}: {value}")
    
    # Dragon Priority settings
    print("\n🐉 Dragon Epic Hero Priority Settings:")
    dragon_priority_settings = [
        ('DRAGON_PRIORITY_GREEN', 'Green Stones'),
        ('DRAGON_PRIORITY_PURPLE', 'Purple Stones'),
        ('DRAGON_PRIORITY_GOLD', 'Gold'),
        ('DRAGON_PRIORITY_GACHA', 'Gacha'),
        ('DRAGON_PRIORITY_POINTS', 'Points')
    ]
    
    for key, label in dragon_priority_settings:
        value = config.get(key, 'Not set')
        print(f"  🔢 {label}: {value}")
    
    # Other settings
    print("\n⚙️  Other Settings:")
    other_settings = [
        ('BUY_GACHA_PACKS', 'Buy Gacha Packs'),
        ('SPEND_GACHAS', 'Spend Gachas'),
        ('PROCESS_MISSIONS', 'Process Missions'),
        ('GEMS_SAFE_BALANCE', 'Gems Safe Balance'),
        ('CONSTELLATION_LAST_INDEX', 'Constellation Last Index')
    ]
    
    for key, label in other_settings:
        value = config.get(key, 'Not set')
        if key in ['BUY_GACHA_PACKS', 'SPEND_GACHAS', 'PROCESS_MISSIONS']:
            emoji = "✅" if value == "True" else "❌" if value == "False" else "❓"
            print(f"  {emoji} {label}: {value}")
        else:
            print(f"  📊 {label}: {value}")


def create_configs_for_all_sessions() -> None:
    """Create .env config files for all existing session files"""
    session_files = get_session_files()
    existing_configs = get_session_env_files()
    
    created_count = 0
    for session_name in session_files:
        if session_name not in existing_configs:
            create_session_env_file(session_name)
            created_count += 1
    
    if created_count > 0:
        print(f"\n✅ Created {created_count} session config files")
    else:
        print("\n✨ All sessions already have config files")


def list_sessions() -> None:
    """List all sessions and their config status"""
    session_files = get_session_files()
    env_files = get_session_env_files()
    
    print(f"\n📁 Found {len(session_files)} sessions:")
    
    for session_name in session_files:
        config_status = "✅ Has config" if session_name in env_files else "❌ No config"
        print(f"  📱 {session_name} - {config_status}")


def interactive_config() -> None:
    """Interactive configuration menu"""
    while True:
        print("\n" + "="*50)
        print("🎮 Session Environment Configuration Manager")
        print("="*50)
        print("1. 📋 List all sessions")
        print("2. 👀 Show session configuration")
        print("3. 📝 Edit session configuration")
        print("4. 🆕 Create config for specific session")
        print("5. 🔄 Create configs for all sessions")
        print("6. 📁 Open session config file in editor")
        print("7. 🚪 Exit")
        
        choice = input("\n🎯 Select an option (1-7): ").strip()
        
        if choice == "1":
            list_sessions()
            
        elif choice == "2":
            session_name = input("📱 Enter session name: ").strip()
            show_session_config(session_name)
            
        elif choice == "3":
            session_name = input("📱 Enter session name: ").strip()
            if session_name not in get_session_files():
                print(f"❌ Session '{session_name}' not found!")
                continue
                
            # Ensure config file exists
            create_session_env_file(session_name)
            
            print(f"\n🔧 Editing configuration for {session_name}")
            print("💡 Enter new values (or press Enter to skip):")
            
            # Configure farming settings
            print("\n🌾 Farming Settings (True/False):")
            farming_settings = [
                ('FARM_GREEN_STONES', 'Farm Green Stones'),
                ('FARM_PURPLE_STONES', 'Farm Purple Stones'),
                ('FARM_GOLD', 'Farm Gold'),
                ('FARM_GACHA', 'Farm Gacha'),
                ('FARM_POINTS', 'Farm Points')
            ]
            
            for key, label in farming_settings:
                current = read_session_env_file(session_name).get(key, 'Not set')
                value = input(f"  {label} (current: {current}): ").strip()
                if value.lower() in ['true', 'false']:
                    update_session_setting(session_name, key, value.title())
            
            # Configure bonk priority settings
            print("\n🎯 Bonk Hero Priority Settings (1=highest, 5=lowest):")
            bonk_priority_settings = [
                ('BONK_PRIORITY_GREEN', 'Green Stones Priority'),
                ('BONK_PRIORITY_PURPLE', 'Purple Stones Priority'),
                ('BONK_PRIORITY_GOLD', 'Gold Priority'),
                ('BONK_PRIORITY_GACHA', 'Gacha Priority'),
                ('BONK_PRIORITY_POINTS', 'Points Priority')
            ]
            
            for key, label in bonk_priority_settings:
                current = read_session_env_file(session_name).get(key, 'Not set')
                value = input(f"  {label} (current: {current}): ").strip()
                if value.isdigit() and 1 <= int(value) <= 5:
                    update_session_setting(session_name, key, value)
            
            # Configure dragon priority settings
            print("\n🐉 Dragon Epic Hero Priority Settings (1=highest, 5=lowest):")
            dragon_priority_settings = [
                ('DRAGON_PRIORITY_GREEN', 'Green Stones Priority'),
                ('DRAGON_PRIORITY_PURPLE', 'Purple Stones Priority'),
                ('DRAGON_PRIORITY_GOLD', 'Gold Priority'),
                ('DRAGON_PRIORITY_GACHA', 'Gacha Priority'),
                ('DRAGON_PRIORITY_POINTS', 'Points Priority')
            ]
            
            for key, label in dragon_priority_settings:
                current = read_session_env_file(session_name).get(key, 'Not set')
                value = input(f"  {label} (current: {current}): ").strip()
                if value.isdigit() and 1 <= int(value) <= 5:
                    update_session_setting(session_name, key, value)
            
            # Configure other settings
            print("\n⚙️  Other Settings:")
            other_boolean_settings = [
                ('BUY_GACHA_PACKS', 'Buy Gacha Packs (True/False)'),
                ('SPEND_GACHAS', 'Spend Gachas (True/False)'),
                ('PROCESS_MISSIONS', 'Process Missions (True/False)')
            ]
            
            for key, label in other_boolean_settings:
                current = read_session_env_file(session_name).get(key, 'Not set')
                value = input(f"  {label} (current: {current}): ").strip()
                if value.lower() in ['true', 'false']:
                    update_session_setting(session_name, key, value.title())
            
            # Configure numeric settings
            other_numeric_settings = [
                ('GEMS_SAFE_BALANCE', 'Gems Safe Balance'),
                ('CONSTELLATION_LAST_INDEX', 'Constellation Last Index (empty = use API value)')
            ]
            
            for key, label in other_numeric_settings:
                current = read_session_env_file(session_name).get(key, 'Not set')
                value = input(f"  {label} (current: {current}): ").strip()
                if key == 'CONSTELLATION_LAST_INDEX':
                    # Allow empty value for CONSTELLATION_LAST_INDEX (means use API value)
                    if value == '' or value.isdigit():
                        update_session_setting(session_name, key, value)
                elif value.isdigit():
                    update_session_setting(session_name, key, value)
            
            print("\n✅ Configuration updated!")
            
        elif choice == "4":
            session_name = input("📱 Enter session name: ").strip()
            create_session_env_file(session_name)
            
        elif choice == "5":
            create_configs_for_all_sessions()
            
        elif choice == "6":
            session_name = input("📱 Enter session name: ").strip()
            sessions_folder = get_sessions_folder()
            config_file = os.path.join(sessions_folder, f"{session_name}.env")
            
            if os.path.exists(config_file):
                print(f"📝 Opening {config_file}")
                print(f"💡 You can edit this file directly with any text editor")
                print(f"📍 Full path: {os.path.abspath(config_file)}")
                
                # Try to open in system editor
                try:
                    if os.name == 'nt':  # Windows
                        os.system(f'notepad "{config_file}"')
                    elif os.name == 'posix':  # macOS/Linux
                        os.system(f'open "{config_file}"' if os.uname().sysname == 'Darwin' else f'xdg-open "{config_file}"')
                except:
                    pass
            else:
                print(f"❌ Config file not found. Create it first (option 4)")
            
        elif choice == "7":
            print("👋 Goodbye!")
            break
            
        else:
            print("❌ Invalid choice. Please select 1-7.")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "list":
            list_sessions()
        elif command == "show" and len(sys.argv) > 2:
            show_session_config(sys.argv[2])
        elif command == "create-all":
            create_configs_for_all_sessions()
        elif command == "create" and len(sys.argv) > 2:
            create_session_env_file(sys.argv[2])
        else:
            print("Usage:")
            print("  python session_env_manager.py                    # Interactive mode")
            print("  python session_env_manager.py list               # List all sessions")
            print("  python session_env_manager.py show <session>     # Show session config")
            print("  python session_env_manager.py create <session>   # Create config for session")
            print("  python session_env_manager.py create-all         # Create configs for all sessions")
    else:
        interactive_config()
