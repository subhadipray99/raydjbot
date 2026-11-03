"""bot_status.py - Global state tracking for Highrise Bot connection and 24/7 keep-alive status."""
import time
from typing import Optional


class BotStatus:
    is_connected: bool = False
    bot_id: Optional[str] = None
    connected_at: Optional[float] = None
    last_disconnect_reason: Optional[str] = None
    last_connect_attempt: Optional[float] = None
    reconnect_count: int = 0
    last_self_ping_time: Optional[float] = None
    last_mesh_ping_time: Optional[float] = None
    mesh_nodes_count: int = 0

    @classmethod
    def get_summary(cls) -> dict:
        uptime_seconds = int(time.time() - cls.connected_at) if cls.is_connected and cls.connected_at else 0
        hours, rem = divmod(uptime_seconds, 3600)
        mins, secs = divmod(rem, 60)
        return {
            "is_connected": cls.is_connected,
            "bot_id": cls.bot_id,
            "connection_uptime": f"{hours}h {mins}m {secs}s" if cls.is_connected else "Disconnected",
            "reconnect_count": cls.reconnect_count,
            "last_self_ping": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(cls.last_self_ping_time)) if cls.last_self_ping_time else None,
            "last_mesh_ping": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(cls.last_mesh_ping_time)) if cls.last_mesh_ping_time else None,
            "mesh_nodes_count": cls.mesh_nodes_count,
        }
