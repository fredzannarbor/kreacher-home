#!/usr/bin/env python3
"""
Kreacher ThinQ — LG Appliance Integration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Connects to LG ThinQ Connect API to monitor and control
LG smart appliances (washer, dryer, dishwasher, range).

Setup:
  1. Go to https://thinq.developer.lge.com/
  2. Sign in with your LG ThinQ account
  3. Create a Personal Access Token (PAT)
  4. Run: python3 kreacher-thinq.py setup <YOUR_TOKEN>

Usage:
  python3 kreacher-thinq.py status           # All appliance status
  python3 kreacher-thinq.py "is the laundry done?"
  python3 kreacher-thinq.py "dryer time left"
  python3 kreacher-thinq.py devices          # List registered devices
"""

import asyncio
import json
import sys
import uuid
import time
from pathlib import Path

import aiohttp
from thinqconnect import ThinQApi

CONFIG_DIR = Path.home() / ".config" / "kreacher"
CONFIG_FILE = CONFIG_DIR / "thinq-config.json"
CACHE_FILE = CONFIG_DIR / "thinq-cache.json"
LOG_FILE = CONFIG_DIR / "kreacher.log"
COUNTRY_CODE = "US"


def log(msg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [thinq] {msg}\n")


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def save_cache(data):
    CACHE_FILE.write_text(json.dumps(data, indent=2, default=str))


def load_cache():
    try:
        return json.loads(CACHE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ─── Friendly Device Type Names ───────────────────────────────────

DEVICE_TYPE_NAMES = {
    "DEVICE_WASHER": "Washer",
    "DEVICE_DRYER": "Dryer",
    "DEVICE_DISH_WASHER": "Dishwasher",
    "DEVICE_OVEN": "Oven/Range",
    "DEVICE_COOKTOP": "Cooktop",
    "DEVICE_REFRIGERATOR": "Refrigerator",
    "DEVICE_MICROWAVE_OVEN": "Microwave",
    "DEVICE_AIR_CONDITIONER": "AC",
    "DEVICE_HOOD": "Range Hood",
    "DEVICE_ROBOT_CLEANER": "Robot Vacuum",
    "DEVICE_STYLER": "Styler",
}

STATE_NAMES = {
    "POWER_OFF": "off",
    "INITIAL": "idle",
    "PAUSE": "paused",
    "DETECTING": "detecting",
    "RUNNING": "running",
    "RINSING": "rinsing",
    "SPINNING": "spinning",
    "DRYING": "drying",
    "END": "done",
    "RESERVED": "scheduled",
    "ERROR": "error",
    "COOLING": "cooling",
    "PREHEATING": "preheating",
}


# ─── API Interaction ───────────────────────────────────────────────

async def get_devices(access_token):
    """List all LG ThinQ devices."""
    client_id = f"thinq-open-{uuid.uuid4()}"
    async with aiohttp.ClientSession() as session:
        api = ThinQApi(session, access_token, COUNTRY_CODE, client_id)
        devices = await api.async_get_device_list()
        return devices or []


async def get_device_status(access_token, device_id):
    """Get status of a specific device."""
    client_id = f"thinq-open-{uuid.uuid4()}"
    async with aiohttp.ClientSession() as session:
        api = ThinQApi(session, access_token, COUNTRY_CODE, client_id)
        status = await api.async_get_device_status(device_id)
        return status or {}


async def get_all_status(access_token):
    """Get status of all devices."""
    devices = await get_devices(access_token)
    results = []
    client_id = f"thinq-open-{uuid.uuid4()}"
    async with aiohttp.ClientSession() as session:
        api = ThinQApi(session, access_token, COUNTRY_CODE, client_id)
        for dev in devices:
            device_id = dev.get("deviceId", "")
            device_type = dev.get("deviceType", "")
            device_name = dev.get("alias", dev.get("modelName", "Unknown"))
            friendly_type = DEVICE_TYPE_NAMES.get(device_type, device_type)

            try:
                status = await api.async_get_device_status(device_id)
                results.append({
                    "id": device_id,
                    "name": device_name,
                    "type": device_type,
                    "friendly_type": friendly_type,
                    "status": status,
                })
            except Exception as e:
                results.append({
                    "id": device_id,
                    "name": device_name,
                    "type": device_type,
                    "friendly_type": friendly_type,
                    "status": {"error": str(e)},
                })
    return results


# ─── Status Formatting ─────────────────────────────────────────────

def format_appliance_status(device_info):
    """Format a device's status into a human-readable string."""
    name = device_info["name"]
    ftype = device_info["friendly_type"]
    status = device_info.get("status", {})

    if "error" in status:
        return f"{ftype} ({name}): unreachable"

    parts = []

    for resource in status if isinstance(status, list) else [status]:
        if isinstance(resource, dict):
            for key, val in resource.items():
                if isinstance(val, dict):
                    if "currentState" in val:
                        state = val["currentState"]
                        parts.append(STATE_NAMES.get(state, state.lower()))

                    remain_h = val.get("remainHour", 0)
                    remain_m = val.get("remainMinute", 0)
                    if remain_h or remain_m:
                        parts.append(f"{remain_h}h{remain_m}m left")

                    for temp_key in ["targetTemperature", "currentTemperature"]:
                        if temp_key in val:
                            label = "target" if "target" in temp_key else "current"
                            parts.append(f"{label}: {val[temp_key]}°")

                    if "doorState" in val:
                        parts.append(f"door {val['doorState'].lower()}")

                    if "remoteControlEnabled" in val:
                        if val["remoteControlEnabled"]:
                            parts.append("remote OK")

    status_str = ", ".join(parts) if parts else "no data"
    return f"{ftype} ({name}): {status_str}"


def format_all_status(devices_status):
    """Format all device statuses."""
    lines = []
    for dev in devices_status:
        lines.append(format_appliance_status(dev))
    return "\n".join(lines) if lines else "No LG devices found"


# ─── Natural Language Query ────────────────────────────────────────

def answer_query(query, devices_status):
    """Answer a natural language query about appliance status."""
    query_lower = query.lower()
    cache = {d["friendly_type"].lower(): d for d in devices_status}
    cache.update({d["name"].lower(): d for d in devices_status})

    for keyword, appliance in [
        ("wash", "Washer"), ("laundry", "Washer"),
        ("dry", "Dryer"), ("dryer", "Dryer"),
        ("dish", "Dishwasher"),
        ("oven", "Oven/Range"), ("range", "Oven/Range"), ("stove", "Oven/Range"),
        ("cook", "Cooktop"), ("fridge", "Refrigerator"), ("refrigerator", "Refrigerator"),
    ]:
        if keyword in query_lower:
            dev = cache.get(appliance.lower())
            if dev:
                return format_appliance_status(dev)

    if "done" in query_lower or "finished" in query_lower or "ready" in query_lower:
        running = []
        done = []
        for dev in devices_status:
            line = format_appliance_status(dev)
            if "running" in line or "rinsing" in line or "spinning" in line or "drying" in line:
                running.append(line)
            elif "done" in line or "idle" in line or "off" in line:
                done.append(line)
        if running:
            return "Still running:\n" + "\n".join(running)
        elif done:
            return "All done!\n" + "\n".join(done)

    if "time" in query_lower or "how long" in query_lower or "left" in query_lower:
        for dev in devices_status:
            line = format_appliance_status(dev)
            if "left" in line:
                return line

    return format_all_status(devices_status)


# ─── Setup ─────────────────────────────────────────────────────────

async def setup_token(token):
    """Verify token and discover devices."""
    print("Verifying LG ThinQ token...")
    try:
        devices = await get_devices(token)
        if not devices:
            print("Token accepted but no devices found.")
            print("Make sure your appliances are registered in the LG ThinQ app.")
            config = load_config()
            config["access_token"] = token
            save_config(config)
            return

        print(f"Found {len(devices)} device(s):")
        device_map = {}
        for dev in devices:
            device_id = dev.get("deviceId", "")
            device_type = dev.get("deviceType", "")
            device_name = dev.get("alias", dev.get("modelName", "Unknown"))
            friendly_type = DEVICE_TYPE_NAMES.get(device_type, device_type)
            print(f"  {friendly_type}: {device_name} ({device_id[:12]}...)")
            device_map[device_id] = {
                "name": device_name,
                "type": device_type,
                "friendly_type": friendly_type,
            }

        config = load_config()
        config["access_token"] = token
        config["devices"] = device_map
        save_config(config)
        print(f"\nConfig saved to {CONFIG_FILE}")
        print("Try: python3 kreacher-thinq.py status")

    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure your token is correct.")
        print("Get it from: https://thinq.developer.lge.com/")


# ─── CLI Entry Point ──────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = " ".join(sys.argv[1:])

    if cmd.startswith("setup "):
        token = cmd.split("setup ", 1)[1].strip()
        asyncio.run(setup_token(token))
        return

    config = load_config()
    token = config.get("access_token")
    if not token:
        print("No LG ThinQ token configured.")
        print("Run: python3 kreacher-thinq.py setup <YOUR_TOKEN>")
        print("Get token from: https://thinq.developer.lge.com/")
        return

    if cmd in ("devices", "list"):
        devices = asyncio.run(get_devices(token))
        if not devices:
            print("No devices found")
            return
        for dev in devices:
            device_type = dev.get("deviceType", "")
            device_name = dev.get("alias", dev.get("modelName", "Unknown"))
            friendly = DEVICE_TYPE_NAMES.get(device_type, device_type)
            print(f"  {friendly}: {device_name} (id: {dev.get('deviceId', '')[:12]}...)")
        return

    try:
        log(f"ThinQ query: {cmd}")
        devices_status = asyncio.run(get_all_status(token))

        save_cache({
            "timestamp": time.time(),
            "devices": devices_status,
        })

        if cmd in ("status", "all"):
            result = format_all_status(devices_status)
        else:
            result = answer_query(cmd, devices_status)

        log(f"ThinQ result: {result}")
        print(result)

    except Exception as e:
        log(f"ThinQ error: {e}")
        print(f"Error querying LG ThinQ: {e}")


if __name__ == "__main__":
    main()
