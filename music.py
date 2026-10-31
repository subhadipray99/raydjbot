import asyncio
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
import config

logger = logging.getLogger("MusicManager")

# Curated 24/7 internet radio stations that stream crystal-clear audio into Highrise rooms
RADIO_STATIONS = {
    "dance": {
        "name": "Dance Wave (EDM / Club / Electronic)",
        "url": "https://dancewave.online/dance.mp3",
    },
    "lofi": {
        "name": "SomaFM Lofi Hip-Hop (Beats to Relax)",
        "url": "https://ice1.somafm.com/lofi-128-mp3",
    },
    "pop": {
        "name": "Antenne Bayern (Top 40 / Pop Hits)",
        "url": "https://mp3channels.webradio.antenne.de/antenne-bayern.mp3",
    },
    "chill": {
        "name": "SomaFM Groove Salad (Ambient / Lounge)",
        "url": "https://ice2.somafm.com/groovesalad-128-mp3",
    },
    "hiphop": {
        "name": "Hot 108 Jamz (Hip-Hop & R&B)",
        "url": "https://s2.viastreaming.net:7200/;",
    },
    "ibiza": {
        "name": "Ibiza Global Radio (Deep House & Techno)",
        "url": "https://ibizaglobalradio.streaming-pro.com:8000/ibizaglobalradio.mp3",
    },
}


@dataclass
class Track:
    id: str
    title: str
    duration_str: str
    duration_sec: int
    requester: str
    thumbnail: str
    url: str
    stream_url: str | None = None
    http_headers: dict | None = None

    def to_dict(self):
        d = asdict(self)
        d.pop("http_headers", None)
        return d


def parse_duration_str(dur_str: str) -> int:
    """Convert mm:ss or hh:mm:ss string to total seconds."""
    parts = dur_str.strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        pass
    return 180  # Default fallback 3 minutes


def search_youtube_sync(query: str, requester: str = "Anonymous") -> Track | None:
    """Search YouTube for a query or video ID."""
    query = query.strip()
    video_id = None

    # Check if query is a direct YouTube URL
    url_match = re.search(r"(?:v=|\/|youtu\.be\/)([0-9A-Za-z_-]{11})", query)
    if url_match:
        video_id = url_match.group(1)
        search_url = f"https://www.youtube.com/watch?v={video_id}"
    else:
        query_encoded = urllib.parse.quote_plus(query)
        search_url = f"https://www.youtube.com/results?search_query={query_encoded}"

    req = urllib.request.Request(
        search_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception as e:
        logger.error(f"Network error while querying YouTube: {e}")
        return None

    # Search results page extraction
    match = re.search(r"var ytInitialData = ({.*?});</script>", html)
    if match:
        try:
            data = json.loads(match.group(1))
            contents = data["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"]["sectionListRenderer"]["contents"]
            for section in contents:
                if "itemSectionRenderer" in section:
                    for item in section["itemSectionRenderer"]["contents"]:
                        if "videoRenderer" in item:
                            vr = item["videoRenderer"]
                            v_id = vr["videoId"]
                            title = vr["title"]["runs"][0]["text"]
                            dur_str = vr.get("lengthText", {}).get("simpleText", "3:00")
                            dur_sec = parse_duration_str(dur_str)
                            thumb = f"https://img.youtube.com/vi/{v_id}/hqdefault.jpg"
                            return Track(
                                id=v_id,
                                title=title,
                                duration_str=dur_str,
                                duration_sec=dur_sec,
                                requester=requester,
                                thumbnail=thumb,
                                url=f"https://www.youtube.com/watch?v={v_id}",
                            )
        except Exception as e:
            logger.debug(f"Error parsing search results json: {e}")

    # Fallback if searching for a single video watch page
    title_match = re.search(r"<title>(.*?)(?: - YouTube)?</title>", html)
    found_title = title_match.group(1) if title_match else query
    if video_id:
        return Track(
            id=video_id,
            title=found_title,
            duration_str="3:30",
            duration_sec=210,
            requester=requester,
            thumbnail=f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
            url=f"https://www.youtube.com/watch?v={video_id}",
        )

    return None


class MusicManager:
    def __init__(self):
        self.queue: list[Track] = []
        self.current: Track | None = None
        self.started_at: float = 0.0
        self.bot = None

        self.current_station_key: str = "dance"
        self._skip_event = asyncio.Event()
        self._new_track_event = asyncio.Event()
        self._runner_task: asyncio.Task | None = None

    def start(self, bot_instance=None):
        """Start the background queue runner."""
        if bot_instance:
            self.bot = bot_instance
        if not self._runner_task or self._runner_task.done():
            self._runner_task = asyncio.create_task(self._queue_runner())
            logger.info("Music queue runner initialized.")

    def set_station(self, key: str) -> tuple[bool, str]:
        """Change the active 24/7 radio station stream."""
        k = key.lower().strip()
        if k in RADIO_STATIONS:
            self.current_station_key = k
            station_name = RADIO_STATIONS[k]["name"]
            logger.info(f"Radio station changed to: {station_name}")
            return True, f"📻 Highrise Room Radio switched to: {station_name}"
        available = ", ".join(RADIO_STATIONS.keys())
        return False, f"Unknown station '{key}'. Available stations: {available}"

    def get_current_station_url(self) -> str:
        """Get the URL of the currently active 24/7 radio station."""
        return RADIO_STATIONS.get(self.current_station_key, RADIO_STATIONS["dance"])["url"]

    async def search_and_queue(self, query: str, requester: str) -> tuple[bool, str, Track | None]:
        """Search YouTube asynchronously and enqueue for Web Jukebox."""
        loop = asyncio.get_running_loop()
        track = await loop.run_in_executor(None, search_youtube_sync, query, requester)
        if not track:
            return False, f"Could not find any songs matching '{query}'.", None

        self.queue.append(track)
        self._new_track_event.set()

        pos = len(self.queue)
        if self.current is None and pos == 1:
            msg = f"🎶 Queued now: \"{track.title}\" [{track.duration_str}]"
        else:
            current_playing_text = f" (Playing: \"{self.current.title}\")" if self.current else ""
            msg = f"➕ Added to queue (#{pos}): \"{track.title}\" [{track.duration_str}]{current_playing_text}"
        return True, msg, track

    def skip(self) -> tuple[bool, str]:
        """Skip currently playing track on Web Jukebox."""
        if not self.current:
            return False, "No music is currently playing on the jukebox."
        skipped_title = self.current.title
        self._skip_event.set()
        return True, f"⏭️ Skipped: \"{skipped_title}\""

    def get_state(self) -> dict:
        """Get synchronized player state for frontend."""
        position = 0.0
        if self.current and self.started_at > 0:
            position = max(0.0, time.time() - self.started_at)

        base_url = config.RENDER_EXTERNAL_URL or f"http://localhost:{config.PORT}"
        station_info = RADIO_STATIONS.get(self.current_station_key, RADIO_STATIONS["dance"])

        return {
            "is_playing": self.current is not None,
            "position_sec": round(position, 2),
            "now_playing": self.current.to_dict() if self.current else None,
            "current_station": {
                "key": self.current_station_key,
                "name": station_info["name"],
                "stream_url": f"{base_url}/radio.mp3",
            },
            "queue": [t.to_dict() for t in self.queue],
            "queue_count": len(self.queue),
        }

    async def _queue_runner(self):
        """Continuously advances tracks from queue for Web Jukebox."""
        while True:
            try:
                if not self.queue:
                    self.current = None
                    self.started_at = 0.0
                    self._new_track_event.clear()
                    await self._new_track_event.wait()
                    continue

                self.current = self.queue.pop(0)
                self.started_at = time.time()
                logger.info(f"[Music] Now Playing: {self.current.title} (Requested by @{self.current.requester})")

                # Announce in Highrise room
                if self.bot and hasattr(self.bot, "highrise") and self.bot.highrise:
                    try:
                        base_url = config.RENDER_EXTERNAL_URL or f"http://localhost:{config.PORT}"
                        announcement = (
                            f"🎶 Now Playing: \"{self.current.title}\" [{self.current.duration_str}]\n"
                            f"👤 Requested by: @{self.current.requester}\n"
                            f"📻 Web Jukebox: {base_url}/player"
                        )
                        await self.bot.highrise.chat(announcement)
                    except Exception as e:
                        logger.error(f"Error announcing new track in room chat: {e}")

                # Wait for track duration or skip event
                self._skip_event.clear()
                duration = max(self.current.duration_sec, 5)
                try:
                    await asyncio.wait_for(self._skip_event.wait(), timeout=duration)
                except asyncio.TimeoutError:
                    pass  # Song duration reached naturally

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in queue runner: {e}", exc_info=True)
                await asyncio.sleep(2)


# Global singleton music manager
music_manager = MusicManager()
