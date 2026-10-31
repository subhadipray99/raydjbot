import os
from dotenv import load_dotenv

# Load variables from .env file if present
load_dotenv()

ROOM_ID = os.getenv("ROOM_ID", "").strip()
API_TOKEN = os.getenv("API_TOKEN", "").strip()
PORT = int(os.getenv("PORT", 8080))
BOT_PREFIX = os.getenv("BOT_PREFIX", "!").strip()
WELCOME_MESSAGE = os.getenv(
    "WELCOME_MESSAGE",
    "Welcome to the room, @{username}! Type !help for commands."
)
