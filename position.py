import os
import json
import asyncio
import logging
from highrise import Position, AnchorPosition
import config

logger = logging.getLogger("HighrisePosition")

POSITIONS_FILE = os.path.join(os.path.dirname(__file__), "bot_positions.json")


def load_all_positions() -> dict:
    """Load positions dict keyed by room_id from file."""
    if not os.path.exists(POSITIONS_FILE):
        return {}
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading bot positions: {e}")
        return {}


def save_all_positions(data: dict) -> None:
    """Save positions dict to JSON file."""
    try:
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving bot positions: {e}")


DEFAULT_ROOM_POSITIONS = {
    "64005ecfb3d3644258e0040e": {
        "type": "position",
        "x": 11.0,
        "y": 12.056,
        "z": 25.71,
        "facing": "FrontRight",
    }
}


def get_saved_position(room_id: str) -> Position | AnchorPosition | None:
    """Retrieve saved Position or AnchorPosition for a given room."""
    # 1. Environment variable override (locks position across redeploys)
    env_pos = getattr(config, "BOT_POSITION", None)
    if env_pos:
        try:
            parts = [p.strip() for p in env_pos.split(",")]
            if len(parts) >= 3:
                x = float(parts[0])
                y = float(parts[1])
                z = float(parts[2])
                facing = parts[3] if len(parts) > 3 else "FrontRight"
                return Position(x=x, y=y, z=z, facing=facing)
        except Exception as e:
            logger.warning(f"Error parsing BOT_POSITION env override: {e}")

    # 2. Check saved room position in JSON
    data = load_all_positions()
    room_data = data.get(room_id) or DEFAULT_ROOM_POSITIONS.get(room_id) or data.get("default")
    if not room_data:
        return None

    pos_type = room_data.get("type", "position")
    if pos_type == "position":
        try:
            return Position(
                x=float(room_data["x"]),
                y=float(room_data["y"]),
                z=float(room_data["z"]),
                facing=str(room_data.get("facing", "FrontRight")),
            )
        except Exception as e:
            logger.error(f"Failed to parse saved Position for room {room_id}: {e}")
            return None
    elif pos_type == "anchor":
        try:
            return AnchorPosition(
                entity_id=str(room_data["entity_id"]),
                anchor_ix=int(room_data["anchor_ix"]),
            )
        except Exception as e:
            logger.error(f"Failed to parse saved AnchorPosition for room {room_id}: {e}")
            return None

    return None


def get_saved_position_dict(room_id: str) -> dict | None:
    """Retrieve raw position dict for room_id."""
    data = load_all_positions()
    return data.get(room_id) or data.get("default")


def save_room_position(room_id: str, pos: Position | AnchorPosition) -> bool:
    """Save position or anchor position for a room."""
    data = load_all_positions()
    if isinstance(pos, Position):
        data[room_id] = {
            "type": "position",
            "x": round(float(pos.x), 3),
            "y": round(float(pos.y), 3),
            "z": round(float(pos.z), 3),
            "facing": str(pos.facing) if pos.facing else "FrontRight",
        }
    elif isinstance(pos, AnchorPosition):
        data[room_id] = {
            "type": "anchor",
            "entity_id": str(pos.entity_id),
            "anchor_ix": int(pos.anchor_ix),
        }
    else:
        logger.warning(f"Unrecognized position object: {type(pos)}")
        return False

    save_all_positions(data)
    logger.info(f"Saved bot position for room {room_id}: {data[room_id]}")
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import db
            asyncio.create_task(db.save_room_attribute(room_id, "saved_position", data[room_id]))
    except Exception:
        pass
    return True


def save_manual_position(room_id: str, x: float, y: float, z: float, facing: str = "FrontRight") -> Position:
    """Save a manual 3D coordinate position for a room."""
    pos = Position(x=float(x), y=float(y), z=float(z), facing=facing)
    save_room_position(room_id, pos)
    return pos


def clear_room_position(room_id: str) -> bool:
    """Clear saved position for a room."""
    data = load_all_positions()
    if room_id in data:
        del data[room_id]
        save_all_positions(data)
        logger.info(f"Cleared saved position for room {room_id}")
        return True
    return False


def format_position(pos: Position | AnchorPosition | dict) -> str:
    """Format position as a clean readable string."""
    if isinstance(pos, Position):
        return f"X: {pos.x:.2f}, Y: {pos.y:.2f}, Z: {pos.z:.2f} ({pos.facing})"
    elif isinstance(pos, AnchorPosition):
        return f"Anchor (Entity: {pos.entity_id}, Index: {pos.anchor_ix})"
    elif isinstance(pos, dict):
        if pos.get("type") == "position":
            return f"X: {pos.get('x'):.2f}, Y: {pos.get('y'):.2f}, Z: {pos.get('z'):.2f} ({pos.get('facing')})"
        elif pos.get("type") == "anchor":
            return f"Anchor (Entity: {pos.get('entity_id')}, Index: {pos.get('anchor_ix')})"
    return str(pos)


# =====================================================================
# FLOOR SPAWN MANAGEMENT (Multi-Floor Teleportation System)
# =====================================================================

FLOORS_FILE = os.path.join(os.path.dirname(__file__), "floors.json")


def load_all_floors() -> dict:
    """Load all floor spawn configurations keyed by room_id."""
    if not os.path.exists(FLOORS_FILE):
        return {}
    try:
        with open(FLOORS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading floors: {e}")
        return {}


def save_all_floors(data: dict) -> None:
    """Save all floor spawn configurations to file."""
    try:
        with open(FLOORS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving floors: {e}")


def normalize_floor_key(name: str) -> str:
    """Normalize floor input strings like 'f1', 'floor1', 'floor 2', 'ground', 'g' to standard keys."""
    raw = str(name).strip().lower().lstrip("!/ ")
    cleaned = raw.replace(" ", "").replace("_", "").replace("-", "")
    if cleaned in ("1", "f1", "floor1", "ground", "g", "first", "floorone"):
        return "1"
    if cleaned in ("2", "f2", "floor2", "second", "floortwo"):
        return "2"
    if cleaned in ("3", "f3", "floor3", "third", "floorthree"):
        return "3"
    if cleaned in ("4", "f4", "floor4", "fourth", "floorfour"):
        return "4"
    if cleaned in ("5", "f5", "floor5", "fifth", "floorfive"):
        return "5"
    if cleaned.startswith("f") and cleaned[1:].isdigit():
        return cleaned[1:]
    if cleaned.startswith("floor") and cleaned[5:].isdigit():
        return cleaned[5:]
    return raw


def format_floor_name(floor_key: str) -> str:
    """Get user-friendly display name for a floor."""
    if floor_key == "1":
        return "Floor 1 (Ground Floor)"
    elif floor_key.isdigit():
        return f"Floor {floor_key}"
    return floor_key.capitalize()


def get_all_floors(room_id: str) -> dict[str, dict]:
    """Get all saved floors for a room."""
    data = load_all_floors()
    return data.get(room_id) or data.get("default") or {}


def get_floor_position(room_id: str, floor_key: str) -> Position | None:
    """Retrieve the saved Position for a specific floor in a room."""
    norm_key = normalize_floor_key(floor_key)
    room_floors = get_all_floors(room_id)
    floor_data = room_floors.get(norm_key)
    if not floor_data:
        return None

    try:
        return Position(
            x=float(floor_data["x"]),
            y=float(floor_data["y"]),
            z=float(floor_data["z"]),
            facing=str(floor_data.get("facing", "FrontRight")),
        )
    except Exception as e:
        logger.error(f"Failed to parse floor position for room {room_id}, floor {norm_key}: {e}")
        return None


def save_floor_position(room_id: str, floor_key: str, pos: Position) -> bool:
    """Save a Position as the spawn point for a floor."""
    if not isinstance(pos, Position):
        logger.warning(f"Floor spawn must be a Position object, got {type(pos)}")
        return False

    norm_key = normalize_floor_key(floor_key)
    data = load_all_floors()
    if room_id not in data:
        data[room_id] = {}

    data[room_id][norm_key] = {
        "type": "position",
        "x": round(float(pos.x), 3),
        "y": round(float(pos.y), 3),
        "z": round(float(pos.z), 3),
        "facing": str(pos.facing) if pos.facing else "FrontRight",
    }
    save_all_floors(data)
    logger.info(f"Saved floor {norm_key} for room {room_id}: {data[room_id][norm_key]}")
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import db
            asyncio.create_task(db.save_room_attribute(room_id, "floors", data[room_id]))
    except Exception:
        pass
    return True


def save_manual_floor_position(
    room_id: str, floor_key: str, x: float, y: float, z: float, facing: str = "FrontRight"
) -> Position:
    """Save manual coordinates as a floor spawn."""
    pos = Position(x=float(x), y=float(y), z=float(z), facing=facing)
    save_floor_position(room_id, floor_key, pos)
    return pos


def delete_floor_position(room_id: str, floor_key: str) -> bool:
    """Delete a floor spawn point."""
    norm_key = normalize_floor_key(floor_key)
    data = load_all_floors()
    if room_id in data and norm_key in data[room_id]:
        del data[room_id][norm_key]
        save_all_floors(data)
        logger.info(f"Deleted floor {norm_key} for room {room_id}")
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import db
                asyncio.create_task(db.save_room_attribute(room_id, "floors", data[room_id]))
        except Exception:
            pass
        return True
    return False
