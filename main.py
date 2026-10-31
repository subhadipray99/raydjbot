import asyncio
import logging
import sys
import os
from highrise.__main__ import bot_runner
from bot import HighriseBot
from web_server import start_web_server
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("Main")


async def run_bot_with_reconnect(bot: HighriseBot, room_id: str, api_token: str):
    """Run Highrise bot runner in a resilient auto-reconnect loop."""
    backoff = 5
    while True:
        try:
            logger.info(f"Connecting bot to Highrise Room ID: {room_id}...")
            await bot_runner(bot, room_id, api_token)
            logger.warning("Bot connection closed cleanly. Reconnecting in 5s...")
            backoff = 5
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Bot execution cancelled.")
            break
        except Exception as e:
            logger.error(f"Error in bot connection: {e}. Retrying in {backoff}s...", exc_info=False)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        else:
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

    # 2. Start the keep-alive Web Server (Mandatory for Render free tier)
    port = config.PORT
    web_runner = await start_web_server(port)

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

    # 4. Instantiate and launch Highrise Bot concurrently
    bot = HighriseBot()
    bot_task = asyncio.create_task(run_bot_with_reconnect(bot, room_id, api_token))

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
