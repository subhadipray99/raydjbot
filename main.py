import asyncio
import logging
import sys
import os
import time

# Ensure UTF-8 output encoding across Windows and Linux
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from highrise.__main__ import bot_runner
from bot import HighriseBot
from bot_status import BotStatus
from web_server import start_web_server
from music import music_manager
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("Main")


async def run_bot_with_reconnect(room_id: str, api_token: str):
    """Run Highrise bot runner in a resilient auto-reconnect loop with fresh instance per session."""
    backoff = 5
    while True:
        bot = None
        start_conn_time = time.time()
        BotStatus.last_connect_attempt = start_conn_time
        BotStatus.reconnect_count += 1
        
        try:
            logger.info(f"Connecting bot to Highrise Room ID: {room_id} (Session #{BotStatus.reconnect_count})...")
            # Always instantiate fresh HighriseBot to prevent stale sockets or zombie task callbacks
            bot = HighriseBot()
            music_manager.start(bot)
            
            await bot_runner(bot, room_id, api_token)
            
            # If bot was connected for more than 45 seconds, consider it a stable session
            duration = time.time() - start_conn_time
            if duration > 45:
                logger.info(f"Highrise bot session closed after {int(duration)}s. Reconnecting in 5s...")
                backoff = 5
            else:
                backoff = min(backoff * 2, 45)
                logger.warning(
                    f"Highrise bot disconnected rapidly after {duration:.1f}s. "
                    f"Backing off for {backoff}s to prevent rate-limiting or deployment flapping..."
                )
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Bot execution cancelled.")
            BotStatus.is_connected = False
            break
        except Exception as e:
            BotStatus.is_connected = False
            BotStatus.last_disconnect_reason = str(e)
            logger.error(f"Error in bot connection: {e}. Retrying in {backoff}s...", exc_info=True)
            backoff = min(backoff * 2, 60)
        finally:
            BotStatus.is_connected = False
            if bot and hasattr(bot, "user_emote_loops"):
                try:
                    for t in list(bot.user_emote_loops.values()):
                        if not t.done():
                            t.cancel()
                    bot.user_emote_loops.clear()
                except Exception:
                    pass
        
        await asyncio.sleep(backoff)


async def main():
    # 1. Resolve ROOM_ID and API_TOKEN (CLI arguments override env vars)
    room_id = config.ROOM_ID
    api_token = config.API_TOKEN

    if len(sys.argv) >= 3:
        room_id = sys.argv[1].strip()
        api_token = sys.argv[2].strip()
        config.ROOM_ID = room_id
        config.API_TOKEN = api_token

    # 2. Start the 24/7 Keep-Alive Web Server & Pinger Mesh (Mandatory for Render free tier)
    port = config.PORT
    web_runner = await start_web_server(port)

    # 2b. Automatically register this bot in Supabase if running on Render with credentials
    ext_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/") or config.RENDER_EXTERNAL_URL
    if room_id and ext_url and "localhost" not in ext_url:
        try:
            import db
            bot_record = {
                "room_id": room_id,
                "room_owner": config.ROOM_OWNER or "owner",
                "room_title": config.ROOM_TITLE,
                "render_service_name": os.getenv("RENDER_SERVICE_NAME", ""),
                "render_service_url": ext_url,
                "radio_stream_url": f"{ext_url}/radio.mp3",
                "health_url": f"{ext_url}/health",
                "is_active": True,
            }
            asyncio.create_task(db.register_bot_in_db(bot_record))
            logger.info(f"[DB] Auto-registered bot {ext_url} in Supabase keep-alive registry.")
        except Exception as dbe:
            logger.debug(f"[DB] Auto-registration notice: {dbe}")

    # 3. Check credentials
    if not room_id or not api_token:
        logger.warning(
            "⚠️ ROOM_ID or API_TOKEN is missing! "
            "Web server is active on port %s for health checks, "
            "but Highrise Bot cannot connect until credentials are provided. "
            "Set ROOM_ID and API_TOKEN in .env or Render Environment Variables.",
            port,
        )
        # Keep web server alive indefinitely so Render health checks don't fail immediately
        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await web_runner.cleanup()
        return

    # 4. Launch Highrise Bot auto-reconnect loop concurrently
    bot_task = asyncio.create_task(run_bot_with_reconnect(room_id, api_token))

    try:
        await bot_task
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down...")
    finally:
        bot_task.cancel()
        await web_runner.cleanup()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Process terminated by user.")
