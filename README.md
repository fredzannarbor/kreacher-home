# Kreacher Home

Home automation via natural language, built entirely with [Claude Code](https://claude.ai/claude-code).

Control Samsung Smart TVs, LG ThinQ appliances, Mac system settings, and network printers from the command line, iMessage, or Apple Watch.

Inspired by [Andrej Karpathy's home automation setup](https://x.com/karpathy/status/1886192184808149383).

## What It Does

```
kreacher-home.py "turn off the living room tv"
kreacher-home.py "mute all tvs"
kreacher-home.py "open Netflix on bedroom"
kreacher-home.py "volume 15 bedroom"
kreacher-home.py "sleepy time"       # TVs off, Mac muted, dark mode
kreacher-home.py "party time"        # flash dark/light mode
kreacher-home.py status              # all device statuses
kreacher-home.py "is the laundry done?"  # LG ThinQ appliance query
```

## Architecture

```
┌──────────────┐    ┌───────────────────┐    ┌──────────────────┐
│  iMessage /  │───▶│ kreacher-listener │───▶│  kreacher-home   │
│  Apple Watch │    │     (bash)        │    │    (python)      │
└──────────────┘    └───────────────────┘    └──────┬───────────┘
                                                     │
                    ┌────────────────────────────────┬┴──────────────┐
                    ▼                    ▼           ▼               ▼
              Samsung TVs         Mac System    Printer        LG ThinQ
              (WSS API)          (osascript)    (HTTP)       (Cloud API)
```

### Samsung TV Control
- **Discovery**: REST API on port 8001 (`http://{ip}:8001/api/v2/`)
- **Control**: Secure WebSocket on port 8002 with token-based auth
- **Pairing**: First connection triggers Allow/Deny popup on TV, returns persistent token
- **Capabilities**: Power, volume (absolute + relative), mute, HDMI switching, app launch, navigation keys

### LG ThinQ Integration
- Cloud-only API via [`thinqconnect`](https://pypi.org/project/thinqconnect/) SDK
- Supports washer, dryer, dishwasher, oven/range, refrigerator, and more
- Natural language queries: "is the laundry done?", "dryer time left"

### iMessage / Apple Watch
- Background listener polls macOS Messages database (requires Full Disk Access)
- Also watches an iCloud Drive file for commands (works with Shortcuts)
- Responses sent back via iMessage (viewable on Apple Watch)

## Setup

### Prerequisites
- macOS (tested on macOS 15 / Apple Silicon)
- Python 3.12+ with `websockets` and optionally `thinqconnect` + `aiohttp`
- Samsung Smart TV (2019+ Tizen, e.g. Q60/Q70/Q80 series)
- Optional: LG ThinQ account + Personal Access Token

### 1. Install Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install websockets
# For LG ThinQ support:
pip install thinqconnect aiohttp
```

### 2. Configure Devices

Edit the `DEVICES` dict in `kreacher-home.py` with your network IPs:

```python
DEVICES = {
    "tv_living": {
        "ip": "192.168.1.100",       # <-- your TV's IP
        "type": "samsung_tv",
        ...
    },
}
```

Find your Samsung TVs:
```bash
# Scan your network
arp -a | grep -i "samsung\|tv"
# Verify Samsung TV API
curl http://192.168.1.100:8001/api/v2/
```

### 3. Pair with Samsung TV

Run any command targeting a TV — it will connect via WSS and the TV will show an Allow/Deny popup. Click Allow, and the auth token is saved automatically.

```bash
python3 kreacher-home.py "bedroom tv status"
# → TV shows popup → click Allow → token saved
```

### 4. (Optional) Set Up LG ThinQ

```bash
# Get a Personal Access Token from https://thinq.developer.lge.com/
python3 kreacher-thinq.py setup YOUR_TOKEN_HERE
```

### 5. (Optional) Set Up iMessage Listener

Edit the phone number in `kreacher-listener.sh`, then:

```bash
chmod +x kreacher-listener.sh
./kreacher-listener.sh
```

Requires Full Disk Access for Terminal (System Settings → Privacy & Security → Full Disk Access).

## Files

| File | Purpose |
|------|---------|
| `kreacher-home.py` | Main controller — natural language parser + device control |
| `kreacher-thinq.py` | LG ThinQ appliance integration (cloud API) |
| `kreacher-listener.sh` | Background daemon for iMessage + iCloud commands |

## How It Was Built

This entire project was built in a single Claude Code session:

1. **Network discovery** — `arp -a`, `dns-sd`, MAC OUI lookups to find controllable devices
2. **Samsung TV reverse engineering** — discovered REST API on 8001, WSS auth on 8002, token pairing flow
3. **App ID discovery** — queried `ed.installedApp.get` via WebSocket to get installed app IDs
4. **Volume control** — Samsung has no absolute volume API, so we "floor then step up" (25 vol-downs + N vol-ups)
5. **LG ThinQ** — identified appliances are cloud-only, integrated via official `thinqconnect` SDK
6. **iMessage bridge** — built on macOS Messages.db SQLite polling + AppleScript responses

## Key Learnings

- Samsung 2019+ TVs require **WSS on port 8002** (not WS on 8001) — plain WebSocket returns `ms.channel.unauthorized`
- Samsung app IDs vary by TV generation — always discover at runtime via `ed.installedApp.get`
- LG appliances have **no local API** — everything goes through LG's cloud via ThinQ Connect
- The `ssl.CERT_NONE` context is required because Samsung TVs use self-signed certificates

## License

MIT
