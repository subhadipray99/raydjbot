import os
import json
import time
import logging

logger = logging.getLogger("HighriseTips")

TIPS_FILE = os.path.join(os.path.dirname(__file__), "tips.json")


def load_all_tips() -> dict:
    """Load tips dict from file."""
    if not os.path.exists(TIPS_FILE):
        return {}
    try:
        with open(TIPS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading tips file: {e}")
        return {}


def save_all_tips(data: dict) -> None:
    """Save tips dict to file."""
    try:
        with open(TIPS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving tips file: {e}")


def get_room_data(room_id: str) -> dict:
    """Get the tips dictionary for a room."""
    data = load_all_tips()
    room = data.get(room_id) or data.get("default") or {}
    if "tippers" not in room:
        room["tippers"] = {}
    return room


def record_tip(
    room_id: str,
    user_id: str,
    username: str,
    amount: int,
    currency_type: str = "gold"
) -> tuple[bool, dict]:
    """
    Record a tip from a user in a room.
    Returns (is_new_top_tipper, top_tipper_dict).
    """
    data = load_all_tips()
    if room_id not in data:
        data[room_id] = {"tippers": {}, "top_tipper": None}

    room = data[room_id]
    tippers = room.setdefault("tippers", {})
    prev_top = room.get("top_tipper")
    prev_top_id = prev_top.get("user_id") if isinstance(prev_top, dict) else None

    # Update tipper profile
    cur_lower = currency_type.lower()
    if user_id not in tippers:
        tippers[user_id] = {
            "user_id": user_id,
            "username": username,
            "total_gold": 0,
            "total_bubbles": 0,
            "tip_count": 0,
            "last_tipped_at": int(time.time()),
        }

    user_stats = tippers[user_id]
    user_stats["username"] = username  # Keep freshest username
    user_stats["tip_count"] = user_stats.get("tip_count", 0) + 1
    user_stats["last_tipped_at"] = int(time.time())

    if cur_lower == "gold":
        user_stats["total_gold"] = user_stats.get("total_gold", 0) + amount
    elif cur_lower == "bubbles":
        user_stats["total_bubbles"] = user_stats.get("total_bubbles", 0) + amount
    else:
        # Default or custom currency
        user_stats["total_gold"] = user_stats.get("total_gold", 0) + amount

    # Determine current #1 top tipper (ranked primarily by gold, then bubbles)
    sorted_tippers = sorted(
        tippers.values(),
        key=lambda x: (x.get("total_gold", 0), x.get("total_bubbles", 0)),
        reverse=True
    )

    new_top = sorted_tippers[0] if sorted_tippers else None
    room["top_tipper"] = {
        "user_id": new_top["user_id"],
        "username": new_top["username"],
        "total_gold": new_top.get("total_gold", 0),
        "total_bubbles": new_top.get("total_bubbles", 0),
    } if new_top else None

    save_all_tips(data)

    is_new_top = (new_top is not None and new_top["user_id"] != prev_top_id)
    return is_new_top, room["top_tipper"]


def get_top_tipper(room_id: str) -> dict | None:
    """Retrieve the top tipper dict for a room."""
    data = load_all_tips()
    room = data.get(room_id) or data.get("default") or {}
    return room.get("top_tipper")


def format_top_tipper(room_id: str) -> str:
    """Format top tipper for whisper/chat display."""
    top = get_top_tipper(room_id)
    if not top or not top.get("username"):
        return "Top tipper: None yet"
    name = top.get("username", "Unknown")
    gold = top.get("total_gold", 0)
    if gold > 0:
        return f"Top tipper: {name} ({gold} gold)"
    return f"Top tipper: {name}"


def get_top_tippers_list(room_id: str, limit: int = 5) -> list[dict]:
    """Get sorted list of top tippers for a room."""
    data = load_all_tips()
    room = data.get(room_id) or data.get("default") or {}
    tippers = list(room.get("tippers", {}).values())
    tippers.sort(
        key=lambda x: (x.get("total_gold", 0), x.get("total_bubbles", 0)),
        reverse=True
    )
    return tippers[:limit]


def set_manual_top_tipper(room_id: str, username: str, gold: int = 100) -> dict:
    """Manually configure or seed the top tipper."""
    clean_user = username.lstrip("@").strip()
    data = load_all_tips()
    if room_id not in data:
        data[room_id] = {"tippers": {}, "top_tipper": None}

    manual_id = f"manual_{clean_user.lower()}"
    top_entry = {
        "user_id": manual_id,
        "username": clean_user,
        "total_gold": int(gold),
        "total_bubbles": 0,
    }
    data[room_id]["top_tipper"] = top_entry
    data[room_id]["tippers"][manual_id] = {
        "user_id": manual_id,
        "username": clean_user,
        "total_gold": int(gold),
        "total_bubbles": 0,
        "tip_count": 1,
        "last_tipped_at": int(time.time()),
    }
    save_all_tips(data)
    return top_entry


def clear_room_tips(room_id: str) -> bool:
    """Clear all tips for a room."""
    data = load_all_tips()
    if room_id in data:
        del data[room_id]
        save_all_tips(data)
        return True
    return False
