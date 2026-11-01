import os
import json
import logging
from highrise.models import Item
import config

logger = logging.getLogger("HighriseOutfit")

OUTFITS_FILE = os.path.join(os.path.dirname(__file__), "outfits.json")

# Core facial features and body flesh (Standard Highrise Starter Base)
BASE_FACE = [
    Item(type='clothing', amount=1, id='body-flesh', active_palette=27),
    Item(type='clothing', amount=1, id='eye-n_basic2018malesquaresleepy', active_palette=7),
    Item(type='clothing', amount=1, id='eyebrow-n_basic2018newbrows07', active_palette=0),
    Item(type='clothing', amount=1, id='nose-n_basic2018newnose05', active_palette=0),
    Item(type='clothing', amount=1, id='mouth-basic2018chippermouth', active_palette=-1),
    Item(type='clothing', amount=1, id='freckle-n_basic2018freckle04', active_palette=0),
]

# Curated Stylish Starter/Free Presets
PRESETS = {
    "dj": {
        "description": "Signature DJ look with yellow hoodie, shield shades, white cuffed jeans & fanny pack",
        "items": [
            Item(type='clothing', amount=1, id='hair_front-n_malenew05', active_palette=1),
            Item(type='clothing', amount=1, id='hair_back-n_malenew05', active_palette=1),
            Item(type='clothing', amount=1, id='glasses-n_room32019shieldsunglassesblack'),
            Item(type='clothing', amount=1, id='shirt-n_room32019denimjackethoodie'),
            Item(type='clothing', amount=1, id='pants-n_starteritems2019cuffedjeanswhite'),
            Item(type='clothing', amount=1, id='shoes-n_room32019socksneakersgrey'),
            Item(type='clothing', amount=1, id='bag-n_room32019fannypackblack'),
            Item(type='clothing', amount=1, id='watch-n_room32019blackwatch'),
        ]
    },
    "suit": {
        "description": "Flashy Black-Tie Suit with formal boots & black glasses",
        "items": [
            Item(type='clothing', amount=1, id='hair_front-n_malenew05', active_palette=1),
            Item(type='clothing', amount=1, id='hair_back-n_malenew05', active_palette=1),
            Item(type='clothing', amount=1, id='glasses-n_starteritems2019squareframesblack'),
            Item(type='clothing', amount=1, id='shirt-n_flashysuit'),
            Item(type='clothing', amount=1, id='pants-n_starteritems2019cuffedjeansblack'),
            Item(type='clothing', amount=1, id='shoes-n_room12019bootsblack'),
            Item(type='clothing', amount=1, id='watch-n_room32019blackwatch'),
        ]
    },
    "street": {
        "description": "Red Hypebeast Streetwear with dreads, aviator shields & high tops",
        "items": [
            Item(type='clothing', amount=1, id='hair_front-n_malenew30', active_palette=1),
            Item(type='clothing', amount=1, id='hair_back-n_malenew30', active_palette=1),
            Item(type='clothing', amount=1, id='glasses-n_room32019aviatorshieldsyellow'),
            Item(type='clothing', amount=1, id='shirt-n_room32019hoodiered'),
            Item(type='clothing', amount=1, id='pants-n_room32019baggytrackpantsred'),
            Item(type='clothing', amount=1, id='shoes-n_room12019hightopsred'),
            Item(type='clothing', amount=1, id='bag-n_room32019sidebagblack'),
        ]
    },
    "camo": {
        "description": "Tactical Stealth look with camo tech pants, black tee & sock sneakers",
        "items": [
            Item(type='clothing', amount=1, id='hair_front-n_malenew27', active_palette=1),
            Item(type='clothing', amount=1, id='hair_back-n_malenew27', active_palette=1),
            Item(type='clothing', amount=1, id='glasses-n_room32019shieldsunglassesblack'),
            Item(type='clothing', amount=1, id='shirt-n_starteritems2019maletshirtblack'),
            Item(type='clothing', amount=1, id='pants-n_room22019techpantscamo'),
            Item(type='clothing', amount=1, id='shoes-n_room32019socksneakersblack'),
            Item(type='clothing', amount=1, id='bag-n_room32019fannypackblack'),
        ]
    },
    "goth": {
        "description": "Goth Rocker with skull sweater, ripped black denim & goth necklace",
        "items": [
            Item(type='clothing', amount=1, id='hair_front-n_malenew05', active_palette=1),
            Item(type='clothing', amount=1, id='hair_back-n_malenew05', active_palette=1),
            Item(type='clothing', amount=1, id='necklace-n_12'),
            Item(type='clothing', amount=1, id='shirt-f_skullsweaterblack'),
            Item(type='clothing', amount=1, id='pants-n_room12019rippedpantsblack'),
            Item(type='clothing', amount=1, id='shoes-n_room12019bootsblack'),
        ]
    },
    "chill": {
        "description": "Summer Chill with vintage white jersey, shorts & white converse",
        "items": [
            Item(type='clothing', amount=1, id='hair_front-n_malenew25', active_palette=1),
            Item(type='clothing', amount=1, id='hair_back-n_malenew25', active_palette=1),
            Item(type='clothing', amount=1, id='shirt-n_room32019jerseywhite'),
            Item(type='clothing', amount=1, id='pants-n_starteritems2019mensshortsblack'),
            Item(type='clothing', amount=1, id='shoes-n_starteritems2018conversewhite'),
            Item(type='clothing', amount=1, id='watch-n_room32019blackwatch'),
        ]
    },
    "puffer": {
        "description": "Cozy Green Puffer Jacket with blue cuffed jeans & black sneakers",
        "items": [
            Item(type='clothing', amount=1, id='hair_front-n_malenew05', active_palette=1),
            Item(type='clothing', amount=1, id='hair_back-n_malenew05', active_palette=1),
            Item(type='clothing', amount=1, id='shirt-n_room32019malepuffyjacketgreen'),
            Item(type='clothing', amount=1, id='pants-n_starteritems2019cuffedjeansblue'),
            Item(type='clothing', amount=1, id='shoes-n_room12019sneakersblack'),
        ]
    }
}

# Aliases for convenience
PRESET_ALIASES = {
    "default": "dj",
    "formal": "suit",
    "red": "street",
    "hype": "street",
    "tactical": "camo",
    "punk": "goth",
    "rock": "goth",
    "summer": "chill",
    "casual": "chill",
    "green": "puffer",
}


def item_to_dict(item: Item) -> dict:
    return {
        "id": item.id,
        "type": getattr(item, "type", "clothing"),
        "amount": getattr(item, "amount", 1),
        "active_palette": getattr(item, "active_palette", None),
        "account_bound": getattr(item, "account_bound", False)
    }


def dict_to_item(d: dict) -> Item:
    return Item(
        type=d.get("type", "clothing"),
        amount=d.get("amount", 1),
        id=d["id"],
        account_bound=d.get("account_bound", False),
        active_palette=d.get("active_palette")
    )


def _load_data() -> dict:
    if os.path.exists(OUTFITS_FILE):
        try:
            with open(OUTFITS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading outfits file: {e}")
    return {"active": None, "custom_presets": {}}


def _save_data(data: dict) -> None:
    try:
        with open(OUTFITS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving outfits file: {e}")


def get_full_preset(name: str) -> list[Item] | None:
    """Returns the full Item list for a built-in or custom preset."""
    name_key = name.lower().strip()
    name_key = PRESET_ALIASES.get(name_key, name_key)

    if name_key in PRESETS:
        return BASE_FACE + PRESETS[name_key]["items"]

    data = _load_data()
    customs = data.get("custom_presets", {})
    if name_key in customs:
        return [dict_to_item(d) for d in customs[name_key]]

    return None


def save_custom_preset(name: str, outfit: list[Item]) -> None:
    """Saves an outfit under a custom preset name."""
    data = _load_data()
    if "custom_presets" not in data:
        data["custom_presets"] = {}
    data["custom_presets"][name.lower().strip()] = [item_to_dict(it) for it in outfit]
    _save_data(data)


def save_active_outfit(outfit: list[Item]) -> None:
    """Saves the currently equipped outfit so it can be restored on reboot."""
    data = _load_data()
    data["active"] = [item_to_dict(it) for it in outfit]
    _save_data(data)


def get_active_outfit() -> list[Item] | None:
    """Gets the previously active saved outfit if available."""
    # 1. Check environment variable override (e.g. BOT_OUTFIT=suit or dj)
    env_outfit = getattr(config, "BOT_OUTFIT", None)
    if env_outfit:
        preset = get_full_preset(env_outfit)
        if preset:
            return preset

    # 2. Check saved active outfit in JSON
    data = _load_data()
    active = data.get("active")
    if active:
        return [dict_to_item(d) for d in active]
    return None


def list_available_presets() -> dict[str, str]:
    """Returns a dict of all presets (built-in and custom) with descriptions."""
    results = {}
    for k, v in PRESETS.items():
        results[k] = v["description"]
    data = _load_data()
    for k in data.get("custom_presets", {}):
        results[k] = "Custom user-saved outfit"
    return results


def get_item_category(item_id: str) -> str:
    """Extracts Highrise category prefix from item id (e.g. 'shirt-n_...' -> 'shirt')."""
    if "-" in item_id:
        return item_id.split("-")[0]
    return item_id
