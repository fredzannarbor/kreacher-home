#!/usr/bin/env python3
"""
Kreacher Home Automation Controller
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Natural language home automation via Samsung TV WebSocket API,
network printer, and macOS system controls.

Built with Claude Code — reverse-engineered local network devices
and wired them into a natural language CLI + iMessage interface.

Samsung TV API:
  - REST: http://{ip}:8001/api/v2/
  - WebSocket (auth): wss://{ip}:8002/api/v2/channels/samsung.remote.control
  - Keys: KEY_POWER, KEY_VOLUP, KEY_VOLDOWN, KEY_MUTE, KEY_HOME, etc.

Usage:
  python3 kreacher-home.py "turn off the living room tv"
  python3 kreacher-home.py "mute all tvs"
  python3 kreacher-home.py "volume up bedroom"
  python3 kreacher-home.py "open Netflix on bedroom"
  python3 kreacher-home.py "dark mode"
  python3 kreacher-home.py status
"""

import json
import sys
import os
import subprocess
import time
import base64
import ssl
import asyncio
import urllib.request
import urllib.error
from pathlib import Path

import websockets

# ─── Device Registry ───────────────────────────────────────────────
#
# Edit these to match YOUR network. Run device discovery to find IPs:
#   arp -a                                    # list devices on LAN
#   curl http://<ip>:8001/api/v2/             # check for Samsung TV API
#   dns-sd -B _airplay._tcp local.            # find AirPlay devices
#

TOKEN_FILE = Path.home() / ".config" / "kreacher" / "tv-tokens.json"

def _load_tokens():
    try:
        return json.loads(TOKEN_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

DEVICES = {
    "tv_living": {
        "name": "Living Room TV",
        "aliases": ["living room", "living", "big tv", "65", "main tv"],
        "type": "samsung_tv",
        "ip": "192.168.1.100",       # <-- your TV's IP
        "model": "Samsung Smart TV",
        "token_key": "living",
    },
    "tv_bedroom": {
        "name": "Bedroom TV",
        "aliases": ["bedroom", "small tv", "43", "study", "office"],
        "type": "samsung_tv",
        "ip": "192.168.1.101",       # <-- your TV's IP
        "model": "Samsung Smart TV",
        "token_key": "bedroom",
    },
    "printer": {
        "name": "Network Printer",
        "aliases": ["printer"],
        "type": "network_printer",
        "ip": "192.168.1.102",       # <-- your printer's IP
    },
    "mac": {
        "name": "Mac",
        "aliases": ["mac", "computer", "this"],
        "type": "mac",
        "ip": "192.168.1.50",        # <-- this machine
    },
}

# Samsung remote key codes
SAMSUNG_KEYS = {
    "power": "KEY_POWER",
    "off": "KEY_POWER",
    "on": "KEY_POWER",
    "vol_up": "KEY_VOLUP",
    "vol_down": "KEY_VOLDOWN",
    "mute": "KEY_MUTE",
    "home": "KEY_HOME",
    "back": "KEY_RETURN",
    "enter": "KEY_ENTER",
    "up": "KEY_UP",
    "down": "KEY_DOWN",
    "left": "KEY_LEFT",
    "right": "KEY_RIGHT",
    "source": "KEY_SOURCE",
    "menu": "KEY_MENU",
    "play": "KEY_PLAY",
    "pause": "KEY_PAUSE",
    "stop": "KEY_STOP",
    "ch_up": "KEY_CHUP",
    "ch_down": "KEY_CHDOWN",
    "hdmi1": "KEY_HDMI1",
    "hdmi2": "KEY_HDMI2",
    "hdmi3": "KEY_HDMI3",
    "hdmi4": "KEY_HDMI4",
    "info": "KEY_INFO",
    "guide": "KEY_GUIDE",
    "sleep": "KEY_SLEEP",
    "ambient": "KEY_AMBIENT",
}

# Samsung TV App IDs (universal across Samsung Tizen TVs)
# Find your installed apps: use ed.installedApp.get via WebSocket
SAMSUNG_APPS = {
    # Streaming
    "netflix":      {"id": "3201907018807", "name": "Netflix"},
    "youtube":      {"id": "111299001912",  "name": "YouTube"},
    "youtube tv":   {"id": "3201707014489", "name": "YouTube TV"},
    "prime":        {"id": "3201512006785", "name": "Prime Video"},
    "prime video":  {"id": "3201512006785", "name": "Prime Video"},
    "amazon":       {"id": "3201512006785", "name": "Prime Video"},
    "disney":       {"id": "3201901017640", "name": "Disney+"},
    "disney+":      {"id": "3201901017640", "name": "Disney+"},
    "hulu":         {"id": "3201601007625", "name": "Hulu"},
    "hbo":          {"id": "3201601007230", "name": "HBO Max"},
    "hbo max":      {"id": "3201601007230", "name": "HBO Max"},
    "peacock":      {"id": "3202006020991", "name": "Peacock TV"},
    "paramount":    {"id": "3201710014981", "name": "Paramount+"},
    "paramount+":   {"id": "3201710014981", "name": "Paramount+"},
    "sling":        {"id": "3201707014448", "name": "Sling TV"},
    "tubi":         {"id": "3201504001965", "name": "Tubi"},
    "freevee":      {"id": "3202102022871", "name": "Freevee"},
    "pluto":        {"id": "3201808016802", "name": "Pluto TV"},
    # Music
    "spotify":      {"id": "3201606009684", "name": "Spotify"},
    "apple music":  {"id": "3201908019041", "name": "Apple Music"},
    "amazon music": {"id": "3201710014874", "name": "Amazon Music"},
    # Other
    "apple tv":     {"id": "3201807016597", "name": "Apple TV"},
    "tiktok":       {"id": "3202008021577", "name": "TikTok"},
    "browser":      {"id": "org.tizen.browser", "name": "Internet"},
    "internet":     {"id": "org.tizen.browser", "name": "Internet"},
    "espn":         {"id": "3201708014618", "name": "ESPN"},
    "pbs":          {"id": "3201809016951", "name": "PBS Video"},
}

LOG_FILE = Path.home() / ".config" / "kreacher" / "kreacher.log"


def log(msg):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


# ─── Samsung TV Control (WSS + Token Auth) ────────────────────────
#
# Samsung 2019+ TVs require secure WebSocket (port 8002) with token auth.
# On first connection, the TV shows an Allow/Deny popup. After approval,
# it returns a persistent token. Save it to TOKEN_FILE for future use.
#
# Pairing flow:
#   1. Connect to wss://{ip}:8002/api/v2/channels/samsung.remote.control
#   2. TV shows popup → user clicks "Allow"
#   3. TV sends ms.channel.connect event with token
#   4. Save token, use it for all future connections
#

SSL_CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

APP_NAME = base64.b64encode(b"KreacherHome").decode()


def samsung_get_status(ip):
    """Get TV status via REST API."""
    try:
        url = f"http://{ip}:8001/api/v2/"
        req = urllib.request.urlopen(url, timeout=3)
        data = json.loads(req.read())
        return {
            "name": data["device"]["name"],
            "power": data["device"]["PowerState"],
            "model": data["device"]["modelName"],
            "ip": ip,
        }
    except Exception:
        return {"name": "Unknown", "power": "off/unreachable", "ip": ip}


def _samsung_ws_uri(ip, token_key=None):
    """Build WSS URI with token if available."""
    tokens = _load_tokens()
    token = tokens.get(token_key, "")
    uri = f"wss://{ip}:8002/api/v2/channels/samsung.remote.control?name={APP_NAME}"
    if token:
        uri += f"&token={token}"
    return uri


def samsung_send_key(ip, key_code, token_key=None):
    """Send a remote control key to Samsung TV via secure WebSocket."""
    uri = _samsung_ws_uri(ip, token_key)

    async def _send():
        async with websockets.connect(uri, ssl=SSL_CTX, close_timeout=5, open_timeout=5) as ws:
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            if data["event"] != "ms.channel.connect":
                log(f"TV auth failed ({ip}): {data['event']}")
                return False
            payload = {
                "method": "ms.remote.control",
                "params": {
                    "Cmd": "Click",
                    "DataOfCmd": key_code,
                    "Option": "false",
                    "TypeOfRemote": "SendRemoteKey",
                },
            }
            await ws.send(json.dumps(payload))
            await asyncio.sleep(0.3)
            return True

    try:
        return asyncio.run(_send())
    except Exception as e:
        log(f"Samsung key send error ({ip}): {e}")
        return False


def samsung_send_keys(ip, key_codes, token_key=None, delay=0.3):
    """Send multiple keys in a single WebSocket session (efficient)."""
    uri = _samsung_ws_uri(ip, token_key)

    async def _send_all():
        async with websockets.connect(uri, ssl=SSL_CTX, close_timeout=10, open_timeout=5) as ws:
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            if data["event"] != "ms.channel.connect":
                log(f"TV auth failed ({ip}): {data['event']}")
                return False
            for key_code in key_codes:
                payload = {
                    "method": "ms.remote.control",
                    "params": {
                        "Cmd": "Click",
                        "DataOfCmd": key_code,
                        "Option": "false",
                        "TypeOfRemote": "SendRemoteKey",
                    },
                }
                await ws.send(json.dumps(payload))
                await asyncio.sleep(delay)
            return True

    try:
        return asyncio.run(_send_all())
    except Exception as e:
        log(f"Samsung multi-key error ({ip}): {e}")
        return False


def samsung_set_volume(ip, target, token_key=None):
    """Set volume to an absolute level by flooring then stepping up."""
    keys = ["KEY_VOLDOWN"] * 25 + ["KEY_VOLUP"] * target
    return samsung_send_keys(ip, keys, token_key=token_key, delay=0.15)


def samsung_launch_app(ip, app_id, token_key=None):
    """Launch an app on the Samsung TV."""
    uri = _samsung_ws_uri(ip, token_key)

    async def _launch():
        async with websockets.connect(uri, ssl=SSL_CTX, close_timeout=10, open_timeout=5) as ws:
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            if data["event"] != "ms.channel.connect":
                return False
            payload = {
                "method": "ms.channel.emit",
                "params": {
                    "event": "ed.apps.launch",
                    "to": "host",
                    "data": {
                        "appId": app_id,
                        "action_type": "DEEP_LINK",
                        "metaTag": "",
                    },
                },
            }
            await ws.send(json.dumps(payload))
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=5)
                resp_data = json.loads(resp)
                return resp_data.get("data") == 200
            except asyncio.TimeoutError:
                return True  # No response often means success

    try:
        return asyncio.run(_launch())
    except Exception as e:
        log(f"Samsung app launch error ({ip}): {e}")
        return False


def _match_app(text):
    """Match natural language text to a Samsung app."""
    text_lower = text.lower()
    for key, app in SAMSUNG_APPS.items():
        if key in text_lower:
            return app
    return None


# ─── Mac System Controls ───────────────────────────────────────────

def mac_dark_mode(enable=True):
    mode = "true" if enable else "false"
    subprocess.run([
        "osascript", "-e",
        f'tell application "System Events" to tell appearance preferences to set dark mode to {mode}'
    ])
    return True


def mac_volume(level=None, mute=None):
    if mute is not None:
        subprocess.run(["osascript", "-e", f"set volume output muted {'true' if mute else 'false'}"])
    if level is not None:
        subprocess.run(["osascript", "-e", f"set volume output volume {level}"])
    return True


def mac_brightness(level):
    """Set display brightness (0.0-1.0)."""
    try:
        subprocess.run([
            "osascript", "-e",
            f'tell application "System Events" to tell process "Control Center" to set value of slider 1 of group 1 to {level}'
        ], timeout=5)
        return True
    except Exception:
        return False


def mac_sleep():
    subprocess.run(["pmset", "sleepnow"])
    return True


def mac_do_not_disturb(enable=True):
    subprocess.run(["shortcuts", "run", "Toggle Do Not Disturb"], timeout=10, capture_output=True)
    return True


# ─── Printer Controls ──────────────────────────────────────────────

def printer_status(ip):
    try:
        url = f"http://{ip}/general/status.html"
        req = urllib.request.urlopen(url, timeout=3)
        html = req.read().decode("iso-8859-1")
        status = "online"
        if "Replace Toner" in html:
            status = "needs toner"
        if "Sleep" in html:
            status = "sleeping"
        return status
    except Exception:
        return "offline"


# ─── Natural Language Command Parser ───────────────────────────────

def resolve_devices(text):
    """Find which devices the command targets."""
    text_lower = text.lower()
    matched = []

    if "all tv" in text_lower or "every tv" in text_lower:
        matched = [d for d in DEVICES.values() if d["type"] == "samsung_tv"]
        return matched
    if "all" in text_lower or "everything" in text_lower:
        return list(DEVICES.values())

    for dev_id, dev in DEVICES.items():
        for alias in dev["aliases"]:
            if alias in text_lower:
                matched.append(dev)
                break

    tv_words = ["tv", "volume", "mute", "channel", "hdmi", "source"]
    if not matched and any(w in text_lower for w in tv_words):
        matched = [d for d in DEVICES.values() if d["type"] == "samsung_tv"]

    return matched


import re

def _parse_volume_target(text):
    """Extract absolute volume target from text like 'volume to 15', 'vol 20'."""
    patterns = [
        r'volume?\s+(?:to|at)\s+(\d+)',
        r'set\s+volume?\s+(?:to\s+)?(\d+)',
        r'volume?\s+(\d+)\b',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            val = int(m.group(1))
            if 0 <= val <= 100:
                return val
    return None


def _is_appliance_query(text):
    """Check if the query is about LG ThinQ appliances."""
    keywords = [
        "wash", "laundry", "dryer", "dry", "dish", "dishwasher",
        "oven", "range", "stove", "cook", "fridge", "refrigerator",
        "appliance", "thinq", "lg",
    ]
    return any(k in text.lower() for k in keywords)


def _query_thinq(query):
    """Query LG ThinQ appliances via kreacher-thinq.py."""
    try:
        thinq_path = Path(__file__).parent / "kreacher-thinq.py"
        result = subprocess.run(
            [sys.executable, str(thinq_path), query],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout.strip()
        if "No LG ThinQ token" in output:
            return "LG ThinQ not set up. Run: kreacher-thinq.py setup <TOKEN>"
        return output if output else "No response from LG ThinQ"
    except subprocess.TimeoutExpired:
        return "LG ThinQ query timed out"
    except Exception as e:
        return f"LG ThinQ error: {e}"


def parse_and_execute(text):
    """Parse natural language command and execute it."""
    text_lower = text.lower().strip()
    results = []

    # ─── Status query ───
    if text_lower in ("status", "devices", "what's on", "whats on", "list"):
        for dev_id, dev in DEVICES.items():
            if dev["type"] == "samsung_tv":
                st = samsung_get_status(dev["ip"])
                results.append(f"{dev['name']}: {st['power']}")
            elif dev["type"] == "network_printer":
                st = printer_status(dev["ip"])
                results.append(f"{dev['name']}: {st}")
            elif dev["type"] == "mac":
                results.append(f"{dev['name']}: active")
        thinq_status = _query_thinq("status")
        if thinq_status and "not set up" not in thinq_status.lower():
            results.append("--- LG Appliances ---")
            results.append(thinq_status)
        return "\n".join(results)

    # ─── LG Appliance queries ───
    if _is_appliance_query(text_lower):
        return _query_thinq(text)

    # ─── App launch commands ───
    app_triggers = ["open ", "launch ", "start ", "play ", "put on ", "watch "]
    if any(text_lower.startswith(t) for t in app_triggers) or any(t in text_lower for t in app_triggers):
        app = _match_app(text_lower)
        if app:
            devices = resolve_devices(text)
            if not devices:
                devices = [d for d in DEVICES.values() if d["type"] == "samsung_tv"]
            results = []
            for dev in devices:
                if dev["type"] == "samsung_tv":
                    ok = samsung_launch_app(dev["ip"], app["id"], dev.get("token_key"))
                    results.append(f"{dev['name']}: {'launching ' + app['name'] if ok else 'failed'}")
            return "\n".join(results) if results else f"No TV found to launch {app['name']}"

    # ─── Mac system commands ───
    if "dark mode" in text_lower or "night mode" in text_lower:
        mac_dark_mode(True)
        return "Dark mode enabled"

    if "light mode" in text_lower:
        mac_dark_mode(False)
        return "Light mode enabled"

    if "sleepy time" in text_lower or "goodnight" in text_lower or "good night" in text_lower:
        for dev in DEVICES.values():
            if dev["type"] == "samsung_tv":
                st = samsung_get_status(dev["ip"])
                if st["power"] == "on":
                    samsung_send_key(dev["ip"], "KEY_POWER", dev.get("token_key"))
        mac_volume(mute=True)
        mac_dark_mode(True)
        return "Goodnight! TVs off, Mac muted, dark mode on."

    if "wake up" in text_lower or "good morning" in text_lower or "morning" == text_lower:
        mac_dark_mode(False)
        mac_volume(level=50, mute=False)
        return "Good morning! Light mode, volume at 50%."

    if "do not disturb" in text_lower or "dnd" in text_lower or "focus" in text_lower:
        mac_do_not_disturb()
        return "Toggled Do Not Disturb"

    if "mac sleep" in text_lower or "sleep mac" in text_lower:
        mac_sleep()
        return "Mac going to sleep..."

    # ─── Party mode! ───
    if "party" in text_lower:
        for _ in range(5):
            mac_dark_mode(True)
            time.sleep(0.3)
            mac_dark_mode(False)
            time.sleep(0.3)
        mac_dark_mode(False)
        return "Party mode complete! (dark/light flash x5)"

    # ─── TV commands ───
    devices = resolve_devices(text)

    if not devices:
        if "mute" in text_lower and ("mac" in text_lower or "computer" in text_lower):
            mac_volume(mute=True)
            return "Mac muted"
        if "unmute" in text_lower and ("mac" in text_lower or "computer" in text_lower):
            mac_volume(mute=False)
            return "Mac unmuted"
        return "No device matched. Try: 'living room tv off', 'mute all tvs', 'status', 'sleepy time'"

    for dev in devices:
        if dev["type"] == "samsung_tv":
            ip = dev["ip"]
            tk = dev.get("token_key")

            if any(w in text_lower for w in ["off", "turn off", "shut off", "power off", "shutdown"]):
                st = samsung_get_status(ip)
                if st["power"] == "on":
                    ok = samsung_send_key(ip, "KEY_POWER", tk)
                    results.append(f"{dev['name']}: {'powering off' if ok else 'failed'}")
                else:
                    results.append(f"{dev['name']}: already off")

            elif any(w in text_lower for w in ["turn on", "power on", "switch on"]):
                ok = samsung_send_key(ip, "KEY_POWER", tk)
                results.append(f"{dev['name']}: {'powering on' if ok else 'failed (try WoL)'}")

            elif _parse_volume_target(text_lower) is not None:
                target = _parse_volume_target(text_lower)
                ok = samsung_set_volume(ip, target, tk)
                results.append(f"{dev['name']}: {'volume set to ' + str(target) if ok else 'failed'}")

            elif "volume up" in text_lower or "vol up" in text_lower or "louder" in text_lower:
                keys = ["KEY_VOLUP"] * 5
                samsung_send_keys(ip, keys, tk, delay=0.15)
                results.append(f"{dev['name']}: volume up 5")

            elif "volume down" in text_lower or "vol down" in text_lower or "quieter" in text_lower or "softer" in text_lower:
                keys = ["KEY_VOLDOWN"] * 5
                samsung_send_keys(ip, keys, tk, delay=0.15)
                results.append(f"{dev['name']}: volume down 5")

            elif "unmute" in text_lower:
                samsung_send_key(ip, "KEY_MUTE", tk)
                results.append(f"{dev['name']}: unmuted")
            elif "mute" in text_lower:
                samsung_send_key(ip, "KEY_MUTE", tk)
                results.append(f"{dev['name']}: muted")

            elif "hdmi" in text_lower:
                for i in range(1, 5):
                    if f"hdmi{i}" in text_lower or f"hdmi {i}" in text_lower:
                        samsung_send_key(ip, f"KEY_HDMI{i}", tk)
                        results.append(f"{dev['name']}: switched to HDMI {i}")
                        break
                else:
                    samsung_send_key(ip, "KEY_SOURCE", tk)
                    results.append(f"{dev['name']}: source menu opened")

            elif "source" in text_lower or "input" in text_lower:
                samsung_send_key(ip, "KEY_SOURCE", tk)
                results.append(f"{dev['name']}: source menu opened")

            elif "home" in text_lower or "smart hub" in text_lower or "menu" in text_lower:
                samsung_send_key(ip, "KEY_HOME", tk)
                results.append(f"{dev['name']}: home screen")

            elif "pause" in text_lower:
                samsung_send_key(ip, "KEY_PAUSE", tk)
                results.append(f"{dev['name']}: paused")
            elif "play" in text_lower or "resume" in text_lower:
                samsung_send_key(ip, "KEY_PLAY", tk)
                results.append(f"{dev['name']}: playing")

            elif "channel up" in text_lower or "ch up" in text_lower:
                samsung_send_key(ip, "KEY_CHUP", tk)
                results.append(f"{dev['name']}: channel up")
            elif "channel down" in text_lower or "ch down" in text_lower:
                samsung_send_key(ip, "KEY_CHDOWN", tk)
                results.append(f"{dev['name']}: channel down")

            else:
                app = _match_app(text_lower)
                if app:
                    ok = samsung_launch_app(ip, app["id"], tk)
                    results.append(f"{dev['name']}: {'launching ' + app['name'] if ok else 'failed to launch ' + app['name']}")
                else:
                    st = samsung_get_status(ip)
                    results.append(f"{dev['name']}: {st['power']} (command not understood)")

    return "\n".join(results) if results else "Command not understood. Try: off, on, mute, volume up/down, hdmi 1-4, status"


# ─── Main ──────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nConfigured devices:")
        for dev_id, dev in DEVICES.items():
            if dev["type"] == "samsung_tv":
                st = samsung_get_status(dev["ip"])
                print(f"  {dev['name']} ({dev['ip']}): {st['power']}")
            elif dev["type"] == "network_printer":
                st = printer_status(dev["ip"])
                print(f"  {dev['name']} ({dev['ip']}): {st}")
            else:
                print(f"  {dev['name']} ({dev['ip']})")
        return

    command = " ".join(sys.argv[1:])
    log(f"Command: {command}")
    result = parse_and_execute(command)
    log(f"Result: {result}")
    print(result)


if __name__ == "__main__":
    main()
