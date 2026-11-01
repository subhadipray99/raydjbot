import os
import json
import logging
from highrise import Position, AnchorPosition

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


def get_saved_position(room_id: str) -> Position | AnchorPosition | None:
    """Retrieve saved Position or AnchorPosition for a given room."""
    data = load_all_positions()
    room_data = data.get(room_id) or data.get("default")
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
