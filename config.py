import os
from dotenv import load_dotenv

# Load variables from .env file if present
load_dotenv()

_DEFAULT_ROOM_ID = "64005ecfb3d3644258e0040e"
_env_room = os.getenv("ROOM_ID", "").strip()
ROOM_ID = _DEFAULT_ROOM_ID if (not _env_room or _env_room == "6582edc828331440e912b112") else _env_room
API_TOKEN = os.getenv("API_TOKEN", "").strip()
PORT = int(os.getenv("PORT", 8080))
BOT_PREFIX = os.getenv("BOT_PREFIX", "!").strip()
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
CUSTOM_DOMAIN = os.getenv("CUSTOM_DOMAIN", "https://zevray.com").strip().rstrip("/")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ahvvanhxbqtcbtuwfxtm.supabase.co").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()
WELCOME_MESSAGE = os.getenv(
    "WELCOME_MESSAGE",
    "Welcome to the room, @{username}! Type !help for commands."
)

# 24/7 Autoplay Radio settings
AUTOPLAY_ENABLED = os.getenv("AUTOPLAY_ENABLED", "true").lower() in ("true", "1", "yes")
AUTOPLAY_INSTANT_TAKEOVER = os.getenv("AUTOPLAY_INSTANT_TAKEOVER", "true").lower() in ("true", "1", "yes")
AUTOPLAY_CHAT_ANNOUNCEMENTS = os.getenv("AUTOPLAY_CHAT_ANNOUNCEMENTS", "true").lower() in ("true", "1", "yes")

# Permanent Bot Position and Outfit overrides (optional)
BOT_POSITION = os.getenv("BOT_POSITION", "").strip()
BOT_OUTFIT = os.getenv("BOT_OUTFIT", "").strip()

# Room Owner credentials for admin commands
ROOM_OWNER = os.getenv("ROOM_OWNER", "shuvodip").strip()
ROOM_OWNER_ID = os.getenv("ROOM_OWNER_ID", "63f3bbdf56dab79de656aea7").strip()

# White-Label Customization
ROOM_TITLE = os.getenv("ROOM_TITLE", "YouTube").strip()
ROOM_CREDITS = os.getenv("ROOM_CREDITS", f"Created and maintained by @{ROOM_OWNER}").strip()
BOT_DISPLAY_NAME = os.getenv("BOT_DISPLAY_NAME", "RayDJBot").strip()

# Razorpay Credentials (One-time payment orders)
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()

# Confirmed Pricing Configuration
PRICE_WEEKLY_INR = int(os.getenv("PRICE_WEEKLY_INR", "78"))
PRICE_MONTHLY_INR = int(os.getenv("PRICE_MONTHLY_INR", "479"))

PRICE_WEEKLY_USD = float(os.getenv("PRICE_WEEKLY_USD", "1.30"))
PRICE_MONTHLY_USD = float(os.getenv("PRICE_MONTHLY_USD", "5.70"))

