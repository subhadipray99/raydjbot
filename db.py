import os
import json
import logging
import asyncio
import aiohttp
from typing import Any, Optional
import config

logger = logging.getLogger("Database")

# Supabase Credentials (loaded from environment or .env)
def get_supabase_url() -> str:
    return (config.SUPABASE_URL or os.getenv("SUPABASE_URL", "https://ahvvanhxbqtcbtuwfxtm.supabase.co")).rstrip("/")

def get_supabase_key() -> str:
    return config.SUPABASE_KEY or os.getenv("SUPABASE_KEY", "")

def get_headers() -> dict:
    key = get_supabase_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation,resolution=merge-duplicates",
    }


async def _get(endpoint: str, params: Optional[dict] = None) -> Optional[Any]:
    """Execute asynchronous GET request to Supabase REST API."""
    url_base = get_supabase_url()
    key = get_supabase_key()
    if not url_base or not key:
        return None
    url = f"{url_base}/rest/v1/{endpoint}"
    headers = get_headers()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 404:
                    logger.debug(f"[DB] Endpoint or table {endpoint} not found (PGRST205).")
                    return None
                else:
                    err_txt = await resp.text()
                    logger.warning(f"[DB] GET {endpoint} failed ({resp.status}): {err_txt}")
                    return None
    except Exception as e:
        logger.warning(f"[DB] Network exception during GET {endpoint}: {e}")
        return None


async def _upsert(table: str, data: dict) -> bool:
    """Execute asynchronous Upsert request to Supabase REST API."""
    url_base = get_supabase_url()
    key = get_supabase_key()
    if not url_base or not key:
        return False
    url = f"{url_base}/rest/v1/{table}"
    headers = get_headers()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status in (200, 201):
                    return True
                err_txt = await resp.text()
                logger.warning(f"[DB] Upsert to {table} failed ({resp.status}): {err_txt}")
                return False
    except Exception as e:
        logger.warning(f"[DB] Network exception during upsert to {table}: {e}")
        return False


# ---------------- ROOM STATE PERSISTENCE ----------------

async def load_room_state(room_id: str) -> Optional[dict]:
    """Fetch persistent room state (position, floors, outfit, welcome) from Supabase."""
    res = await _get("room_state", params={"room_id": f"eq.{room_id}", "select": "*"})
    if res and isinstance(res, list) and len(res) > 0:
        logger.info(f"[DB] Successfully hydrated room state for {room_id} from Supabase.")
        return res[0]
    return None


async def save_room_attribute(room_id: str, field: str, value: Any) -> bool:
    """Save or update a specific attribute of room_state in Supabase."""
    payload = {
        "room_id": room_id,
        field: value,
        "updated_at": "now()",
    }
    success = await _upsert("room_state", payload)
    if success:
        logger.debug(f"[DB] Saved {field} for room {room_id} to Supabase.")
    return success


# ---------------- BOTS REGISTRY PERSISTENCE ----------------

async def register_bot_in_db(bot_data: dict) -> bool:
    """Record or update deployed bot in Supabase bots table without duplicating rows."""
    srv_name = bot_data.get("render_service_name")
    room_id = bot_data.get("room_id")
    existing = None
    if srv_name:
        existing = await _get("bots", params={"render_service_name": f"eq.{srv_name}", "select": "id"})
    if not existing and room_id:
        existing = await _get("bots", params={"room_id": f"eq.{room_id}", "select": "id"})

    if existing and isinstance(existing, list) and len(existing) > 0:
        bot_data["id"] = existing[0]["id"]
        bot_data["updated_at"] = "now()"

    return await _upsert("bots", bot_data)


async def get_all_active_bots_from_db() -> list[str]:
    """Retrieve all active bot health URLs from Supabase for 10-minute master keep-alive pings."""
    res = await _get("bots", params={"is_active": "eq.true", "select": "render_service_url,health_url"})
    if res and isinstance(res, list):
        urls = []
        for row in res:
            url = row.get("health_url") or (row.get("render_service_url", "") + "/health")
            if url:
                urls.append(url)
        return urls
    return []


async def get_user_bots_from_db(user_id: str) -> list[dict]:
    """Retrieve all bots deployed by a specific user from Supabase."""
    res = await _get("bots", params={"user_id": f"eq.{user_id}", "select": "*", "order": "created_at.desc"})
    if res and isinstance(res, list):
        return res
    return []

# ---------------- USER CONFIG / SETTINGS PERSISTENCE ----------------

async def get_user_config(user_id: str) -> Optional[dict]:
    """Retrieve saved bot deploy credentials and config for a user from Supabase."""
    res = await _get("profiles", params={"id": f"eq.{user_id}", "select": "saved_config"})
    if res and isinstance(res, list) and len(res) > 0:
        return res[0].get("saved_config")
    return None


async def save_user_config(user_id: str, config_data: dict) -> bool:
    """Save or update bot deploy credentials and config for a user in Supabase."""
    payload = {
        "id": user_id,
        "saved_config": config_data,
    }
    return await _upsert("profiles", payload)

