# SleepagotchiLITE Bot

[🇷🇺 Русский](README-RU.md) | [🇬🇧 English](README.md)

[<img src="https://res.cloudinary.com/dkgz59pmw/image/upload/v1736756459/knpk224-28px-market_ksivis.svg" alt="Market Link" width="200">](https://t.me/MaineMarketBot?start=8HVF7S9K)
[<img src="https://res.cloudinary.com/dkgz59pmw/image/upload/v1736756459/knpk224-28px-channel_psjoqn.svg" alt="Channel Link" width="200">](https://t.me/+vpXdTJ_S3mo0ZjIy)
[<img src="https://res.cloudinary.com/dkgz59pmw/image/upload/v1736756459/knpk224-28px-chat_ixoikd.svg" alt="Chat Link" width="200">](https://t.me/+wWQuct9bljQ0ZDA6)

---

## 📑 Table of Contents
1. [Description](#description)
2. [Key Features](#key-features)
3. [Installation](#installation)
   - [Quick Start](#quick-start)
   - [Manual Installation](#manual-installation)
4. [Settings](#settings)
5. [Support and Donations](#support-and-donations)
6. [Contact](#contact)

---

## 📜 Description
**SleepagotchiLITE Bot** is an automated constellation resource farming bot for the SleepagotchiLITE game. Specializes in strategic constellation challenge completion using bonk and dragon epic heroes to maximize resource collection efficiency.

---

## 🌟 Key Features
- 🔄 **Multithreading** — ability to work with multiple accounts in parallel
- 🔐 **Proxy Support** — secure operation through proxy servers
- � **Smart Constellation Farming** — automated constellation challenge completion with priority-based resource targeting
- 🎯 **Specialized Hero Management** — dedicated bonk and dragon epic hero strategies with level-up automation
- 🎮 **Dynamic API Integration** — uses game API to determine optimal constellation starting points
- ⚙️ **Session-Specific Configuration** — individual settings per account for customized farming strategies
- 📊 **Statistics** — detailed session statistics tracking

---

## 🛠️ Installation

### Quick Start
1. **Download the project:**
   ```bash
   git clone https://github.com/oleksiiparf/Sleepagotchi.git sleepagotchi
   cd sleepagotchi
   ```

2. **Install dependencies:**
   - **Windows**:
     ```bash
     run.bat
     ```
   - **Linux**:
     ```bash
     run.sh
     ```

3. **Get API keys:**
   - Go to [my.telegram.org](https://my.telegram.org) and get your `API_ID` and `API_HASH`
   - Add this information to the `.env` file

4. **Run the bot:**
   ```bash
   python3 main.py --action 3  # Run the bot
   ```

### Manual Installation
1. **Linux:**
   ```bash
   sudo sh install.sh
   python3 -m venv venv
   source venv/bin/activate
   pip3 install -r requirements.txt
   cp .env-example .env
   nano .env  # Add your API_ID and API_HASH
   python3 main.py
   ```

2. **Windows:**
   ```bash
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   copy .env-example .env
   python main.py
   ```

---

## ⚙️ Settings

### Global Settings (.env file)

| Parameter                  | Default Value         | Description                                                 |
|---------------------------|----------------------|-------------------------------------------------------------|
| **API_ID**                |                      | Telegram API application ID                                 |
| **API_HASH**              |                      | Telegram API application hash                               |
| **GLOBAL_CONFIG_PATH**    | TG_FARM              | Path for configuration files. By default, uses the TG_FARM environment variable |
| **FIX_CERT**              | False                | Fix SSL certificate errors                                  |
| **SESSION_START_DELAY**   | 360                  | Delay before starting the session (seconds)               |
| **ACTION_DELAY**          | (2, 5)               | Delay between actions (min, max seconds)                   |
| **REQUEST_RETRIES**       | 3                    | Number of request retries on failure                        |
| **SLEEP_TIME**            | (600, 3600)          | Sleep time between cycles (min, max seconds)               |
| **REF_ID**                |                      | Referral ID for new accounts                               |
| **USE_PROXY**             | True                 | Use proxy                                                  |
| **SESSIONS_PER_PROXY**    | 1                    | Number of sessions per proxy                                |
| **DISABLE_PROXY_REPLACE** | False                | Disable proxy replacement on errors                         |
| **BLACKLISTED_SESSIONS**  | ""                   | Sessions that will not be used (comma-separated)           |
| **DEBUG_LOGGING**         | False                | Enable detailed logging                                     |
| **DEVICE_PARAMS**         | False                | Use custom device parameters                                 |
| **AUTO_UPDATE**           | True                 | Automatic updates                                           |
| **CHECK_UPDATE_INTERVAL** | 60                   | Update check interval (seconds)                            |

### Session-Specific Settings

Each session can have its own configuration file located in `sessions/{session_name}.env`. These settings override global defaults for individual sessions and allow fine-tuning constellation farming strategies. These settings are created automatically once the session is added.

#### Resource Farming Settings
| Parameter                  | Default Value | Description                                                 |
|---------------------------|---------------|-------------------------------------------------------------|
| **BUY_GACHA_PACKS**       | False         | Buy gacha packs with gems                                   |
| **SPEND_GACHAS**          | False         | Automatically spend gacha tokens                            |
| **GEMS_SAFE_BALANCE**     | 100000        | Safe balance of gems that cannot be spent                  |
| **PROCESS_MISSIONS**      | False         | Automatically process and claim missions                     |
| **FARM_GREEN_STONES**     | True          | Farm green stones through constellation challenges          |
| **FARM_PURPLE_STONES**    | True          | Farm purple stones through constellation challenges         |
| **FARM_GOLD**             | True          | Farm gold through constellation challenges                   |
| **FARM_GACHA**            | True          | Farm gacha tokens through constellation challenges          |
| **FARM_POINTS**           | True          | Farm points through constellation challenges                 |

#### Constellation Settings
| Parameter                  | Default Value | Description                                                 |
|---------------------------|---------------|-------------------------------------------------------------|
| **CONSTELLATION_LAST_INDEX** | None       | Constellation start index (empty = use API value, number = manual override) |

#### Bonk Hero Priority Settings (1 = highest, 5 = lowest)
| Parameter                  | Default Value | Description                                                 |
|---------------------------|---------------|-------------------------------------------------------------|
| **BONK_PRIORITY_GREEN**   | 3             | Priority for green stones challenges                        |
| **BONK_PRIORITY_PURPLE**  | 4             | Priority for purple stones challenges                       |
| **BONK_PRIORITY_GOLD**    | 1             | Priority for gold challenges                                |
| **BONK_PRIORITY_GACHA**   | 2             | Priority for gacha challenges                               |
| **BONK_PRIORITY_POINTS**  | 5             | Priority for points challenges                              |

#### Dragon Epic Hero Priority Settings (1 = highest, 5 = lowest)
| Parameter                  | Default Value | Description                                                 |
|---------------------------|---------------|-------------------------------------------------------------|
| **DRAGON_PRIORITY_GREEN** | 2             | Priority for green stones challenges                        |
| **DRAGON_PRIORITY_PURPLE**| 1             | Priority for purple stones challenges                       |
| **DRAGON_PRIORITY_GOLD**  | 3             | Priority for gold challenges                                |
| **DRAGON_PRIORITY_GACHA** | 4             | Priority for gacha challenges                               |
| **DRAGON_PRIORITY_POINTS**| 5             | Priority for points challenges                              |

### Session Environment Management

The bot includes a session environment manager (`session_env_manager.py`) that allows you to:

- **Create** individual session configurations
- **Edit** session-specific settings with an interactive menu
- **View** current session configurations
- **Manage** constellation farming priorities for each hero type

**Usage:**
```bash
python session_env_manager.py
```

This tool provides an interactive interface to configure:
- Resource farming preferences per session
- Individual hero priorities for bonk and dragon epic cards
- Constellation index settings (manual override or API-based)
- Economic settings like gem spending and gacha purchasing



## 💰 Support and Donations

Support development using cryptocurrencies:

| Currency              | Wallet Address                                                                     |
|----------------------|------------------------------------------------------------------------------------|
| Bitcoin (BTC)        |bc1qt84nyhuzcnkh2qpva93jdqa20hp49edcl94nf6| 
| Ethereum (ETH)       |0xc935e81045CAbE0B8380A284Ed93060dA212fa83| 
| TON                  |UQBlvCgM84ijBQn0-PVP3On0fFVWds5SOHilxbe33EDQgryz|
| Binance Coin         |0xc935e81045CAbE0B8380A284Ed93060dA212fa83| 
| Solana (SOL)         |3vVxkGKasJWCgoamdJiRPy6is4di72xR98CDj2UdS1BE| 
| Ripple (XRP)         |rPJzfBcU6B8SYU5M8h36zuPcLCgRcpKNB4| 
| Dogecoin (DOGE)      |DST5W1c4FFzHVhruVsa2zE6jh5dznLDkmW| 
| Polkadot (DOT)       |1US84xhUghAhrMtw2bcZh9CXN3i7T1VJB2Gdjy9hNjR3K71| 
| Litecoin (LTC)       |ltc1qcg8qesg8j4wvk9m7e74pm7aanl34y7q9rutvwu| 
| Matic                |0xc935e81045CAbE0B8380A284Ed93060dA212fa83| 
| Tron (TRX)           |TQkDWCjchCLhNsGwr4YocUHEeezsB4jVo5| 

---

## 📞 Contact

If you have questions or suggestions:
- **Telegram**: [Join our channel](https://t.me/+vpXdTJ_S3mo0ZjIy)

---

## ⚠️ Disclaimer

This software is provided "as is" without any warranties. By using this bot, you accept full responsibility for its use and any consequences that may arise.

The author is not responsible for:
- Any direct or indirect damages related to the use of the bot
- Possible violations of third-party service terms of use
- Account blocking or access restrictions

Use the bot at your own risk and in compliance with applicable laws and third-party service terms of use.

