import os
from dotenv import load_dotenv

# Load variables from .env file if present
load_dotenv()

ROOM_ID = os.getenv("ROOM_ID", "").strip()
API_TOKEN = os.getenv("API_TOKEN", "").strip()
PORT = int(os.getenv("PORT", 8080))
BOT_PREFIX = os.getenv("BOT_PREFIX", "!").strip()
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
WELCOME_MESSAGE = os.getenv(
    "WELCOME_MESSAGE",
    "Welcome to the room, @{username}! Type !help for commands."
)

# 24/7 Autoplay Radio settings
AUTOPLAY_ENABLED = os.getenv("AUTOPLAY_ENABLED", "true").lower() in ("true", "1", "yes")
AUTOPLAY_INSTANT_TAKEOVER = os.getenv("AUTOPLAY_INSTANT_TAKEOVER", "true").lower() in ("true", "1", "yes")
AUTOPLAY_CHAT_ANNOUNCEMENTS = os.getenv("AUTOPLAY_CHAT_ANNOUNCEMENTS", "true").lower() in ("true", "1", "yes")
