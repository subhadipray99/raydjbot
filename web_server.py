import logging
import time
from aiohttp import web
import config

logger = logging.getLogger("WebServer")
start_time = time.time()


def get_uptime() -> str:
    elapsed_seconds = int(time.time() - start_time)
    hours, remainder = divmod(elapsed_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes}m {seconds}s"


async def handle_root(request: web.Request) -> web.Response:
    """Status endpoint for root URL."""
    return web.json_response({
        "status": "online",
        "service": "Highrise Bot Web Service",
        "uptime": get_uptime(),
        "room_configured": bool(config.ROOM_ID),
        "keepalive_message": "Ping received! Service kept alive."
    })


async def handle_health(request: web.Request) -> web.Response:
    """Lightweight 200 OK endpoint for cron-job / UptimeRobot / Render health checks."""
    return web.Response(text="OK", status=200)


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_health)
    return app


async def start_web_server(port: int) -> web.AppRunner:
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"🌐 Keep-alive Web Server listening on http://0.0.0.0:{port}")
    return runner
