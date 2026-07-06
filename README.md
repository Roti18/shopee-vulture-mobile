# Shopee Vulture

An Android ADB automation bot for monitoring and purchasing products on Shopee. Runs 24/7 on a VPS with a connected Android device (USB or WiFi ADB).

---

## Features

### Core
- **Auto monitoring** — Opens product URL, checks stock via variant popup, loops at configurable interval
- **Auto checkout** — When stock meets threshold, selects variant, sets quantity, taps "Buat Pesanan", verifies order
- **Monitor Mode** — Check stock only, send Telegram notification when available, never checkout
- **Telegram Remote Control** — Full command set via Telegram bot

### Reliability
- **Event-driven architecture** — Clean separation of concerns via EventBus pub/sub
- **State Machine** — Explicit handler per state, no nested if statements
- **5-Level Tiered Recovery** — From soft retry to ADB reconnect, force-stop app, restart ADB server, and panic
- **Watchdog** — Detects frozen state machine, triggers recovery automatically
- **Blackout Mode** — Scheduled idle window, screen off during blackout, saves/restores previous mode
- **Cooldown** — Configurable cooldown after successful purchase, persists across restart
- **Graceful Shutdown** — Saves runtime state to SQLite on stop/signal

### Persistence
- **SQLite** — Runtime settings, product config, statistics, state persistence across restarts
- **Daily Reports** — Aggregated statistics per day, sent to Telegram at midnight
- **Heartbeat** — Periodic status update (30 min) to Telegram with ADB connectivity, CPU/RAM, loop counts

### Docker Ready
- **Dockerfile** — Python 3.12-slim with ADB, healthcheck, non-root user
- **docker-compose** — Volume mounts for data/logs/screenshots, resource limits (512MB RAM, 0.5 CPU), auto-restart

---

## Requirements

- Android device with USB debugging enabled (or WiFi ADB)
- ADB connected to host (USB or `adb connect <ip>:5555`)
- Shopee Indonesia app installed on device
- Telegram Bot Token (from @BotFather)
- Python 3.12+
- Docker (optional, for containerized deployment)

---

## Quick Start

### 1. Clone & Setup

```bash
git clone https://github.com/yourusername/shopee-vulture-mobile.git
cd shopee-vulture-mobile
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp data/.env.example data/.env
# Edit data/.env with your credentials:
#   TELEGRAM_TOKEN=your_bot_token
#   TELEGRAM_CHAT_ID=your_chat_id
#   ADB_DEVICE_SERIAL= (USB) or ADB_WIFI_HOST=192.168.1.100:5555
```

### 3. Set Product

```bash
# Via Telegram after bot is running:
/setproduct https://shopee.co.id/product-url
/setvariant matcha latte 50ml   # optional, target specific variant
```

Or edit `data/config.json` before first run.

### 4. Run

```bash
python -m bot.main
```

Then via Telegram:
- `/start` — Begin monitoring + auto checkout
- `/monitor` — Monitor mode (notify only, no checkout)
- `/status` — Show bot status
- `/stop` — Stop bot

### Docker

```bash
docker compose up -d --build
```

---

## Telegram Commands

| Command | Description |
|---------|-------------|
| **Control** | |
| `/start` | Start bot (monitoring + auto checkout) |
| `/stop` | Stop bot |
| `/pause` | Pause temporarily |
| `/resume` | Resume from pause |
| `/monitor` | Toggle monitor mode (notify only, no checkout) |
| `/status` | Show full bot status |
| `/help` | Show command list |
| **Product** | |
| `/setproduct <url>` | Set Shopee product URL |
| `/product` | Show active product details |
| `/reloadproduct` | Reload product from database |
| `/setvariant <name>` | Set target variant |
| `/setpayment <method>` | Set payment method |
| **Stock Settings** | |
| `/target <n>` | Minimum stock to trigger checkout |
| `/qty <n>` | Purchase quantity per order |
| `/stockmode <any\|minimum>` | Stock verification mode |
| `/restocklimit <n>` | Max purchases per session |
| **Schedule** | |
| `/interval <seconds>` | Check interval between cycles |
| `/cooldown <2h\|30m\|0>` | Cooldown after successful purchase |
| `/sleepafter <on\|off>` | Turn off screen after success |
| `/blackout <HH:MM-HH:MM>` | Scheduled blackout window |
| `/blackout off` | Disable blackout |
| `/blackout status` | Check blackout status |
| **Emergency** | |
| `/panic` | Emergency stop |

---

## Architecture

```
bot/
├── main.py              # Entry point, wires all components
├── config.py            # AppConfig: .env + SQLite runtime settings
├── models/              # Data models and enums
│   ├── enums.py         # BotMode, WorkflowState, ScreenType, RecoveryLevel
│   ├── product.py       # ProductConfig dataclass
│   └── bot_state.py     # BotRuntimeState, BotStats, WatchdogMetrics
├── adb/                 # Android Debug Bridge interface
│   ├── client.py        # Async ADB commands (tap, swipe, open_url, etc.)
│   ├── dumper.py        # uiautomator dump and XML parsing
│   ├── screencap.py     # Screenshot capture
│   └── xml_cache.py     # XML cache with TTL to avoid redundant dumps
├── events/              # Event-driven architecture
│   ├── bus.py           # Pub/sub event bus
│   ├── events.py        # All event dataclasses
│   └── handlers.py      # Log and stats event handlers
├── ui/                  # UI element selectors (7-level priority chain)
│   ├── base_selectors.py
│   ├── product_selectors.py
│   ├── variant_selectors.py
│   └── checkout_selectors.py
├── parser/              # XML parsers for screen detection
│   ├── base_parser.py   # 7-level element resolution engine
│   ├── product_parser.py
│   ├── variant_parser.py
│   └── checkout_parser.py
├── actions/             # High-level UI actions
│   ├── product_actions.py
│   ├── variant_actions.py
│   └── checkout_actions.py
├── workflow/            # State machine workflow handlers
│   ├── state_machine.py # StateMachine engine
│   ├── open_product.py  # Open URL + tap voucher (merged with BUY_VOUCHER)
│   ├── check_variant.py # Stock check + variant selection (supports monitor mode)
│   ├── buy_now.py       # Transition to checkout page
│   ├── checkout.py      # Tap "Buat Pesanan" with rapid tap loop
│   ├── verify_payment.py
│   ├── create_order.py  # Screenshot + Telegram notification
│   └── cooldown.py      # Wait period after successful order
├── recovery/            # Tiered recovery system
│   └── recovery.py      # 5 levels: soft retry → ADB reconnect → force-stop → restart ADB → panic
├── scheduler/           # Background schedulers
│   ├── heartbeat.py     # Periodic status to Telegram (30 min)
│   ├── loop_scheduler.py
│   ├── blackout_scheduler.py
│   ├── daily_report.py  # Midnight report from DB statistics
│   └── watchdog.py      # Frozen detection and recovery trigger
├── storage/             # SQLite persistence layer
│   ├── database.py      # Async SQLite with WAL mode
│   └── repositories.py  # Settings, Product, Runtime, Statistics repositories
├── telegram/            # Telegram integration
│   ├── bot.py           # Bot setup with auth guard
│   ├── commands.py      # 20+ command handlers
│   └── notifier.py      # Event-driven message sender
├── utils/               # Utilities
│   ├── logger.py        # JSON stdout + human-readable file logger
│   ├── system_info.py   # CPU/RAM usage
│   └── health.py        # Docker healthcheck file writer
└── tests/               # Tests
    ├── mock_adb.py
    ├── test_event_bus.py
    ├── test_parsers.py
    └── fixtures/         # XML test data
```

---

## State Machine Flow

```
IDLE/STOPPED → OPEN_PRODUCT (open URL + poll voucher button)
    → CHECK_VARIANT (parse stock from variant popup)
        ├── Stok >= threshold → emit alert → tap variant → tap submit → CHECKOUT
        │   └── (MONITOR mode → close popup → loop)
        ├── Stok < threshold → close popup → loop
        └── Popup not detected → RECOVERY
    → CHECKOUT (verify checkout page, tap "Buat Pesanan", verify payment page)
    → VERIFY_PAYMENT (wait for order result)
    → CREATE_ORDER (screenshot, Telegram notification)
        ├── sleep_after_success on → PAUSED
        ├── restock limit reached → COOLDOWN
        └── else → OPEN_PRODUCT (continue monitoring)
```

Any failure in any state redirects to:
```
→ RECOVERY (5-tier: soft retry → ADB reconnect → force-stop → restart ADB server → panic)
    → OPEN_PRODUCT (resume)
```

---

## Configuration

`data/.env` (secrets, never committed):
- `TELEGRAM_TOKEN` — Bot token from @BotFather
- `TELEGRAM_CHAT_ID` — Your Telegram chat ID
- `ADB_DEVICE_SERIAL` — USB device serial (from `adb devices`)
- `ADB_WIFI_HOST` — WiFi ADB host:port (e.g. `192.168.1.100:5555`)

`data/config.json` (optional seed, loaded once on first run):
- `product.url` — Shopee product URL
- `product.variant` — Target variant name
- `product.minimum_stock` — Minimum stock threshold
- `product.purchase_quantity` — Items per purchase
- `product.restock_limit` — Max purchases per cooldown session

All runtime settings can be changed via Telegram commands while bot is running. Changes persist to SQLite.

---

## License

MIT
