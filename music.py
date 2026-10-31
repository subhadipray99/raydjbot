import asyncio
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
import config

logger = logging.getLogger("MusicManager")


@dataclass
class Track:
    id: str
    title: str
    duration_str: str
    duration_sec: int
    requester: str
    thumbnail: str
    url: str

    def to_dict(self):
        return asdict(self)


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


def download_track_audio_sync(title: str, video_id: str) -> str | None:
    """Download audio file locally using yt-dlp with YouTube and SoundCloud fallback."""
    import yt_dlp
    import imageio_ffmpeg

    cache_dir = "cache"
    os.makedirs(cache_dir, exist_ok=True)
    out_file = os.path.join(cache_dir, f"{video_id}.mp3")

    if os.path.exists(out_file) and os.path.getsize(out_file) > 10000:
        return out_file

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(cache_dir, f"{video_id}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "ffmpeg_location": ffmpeg_exe,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
    }

    # 1. Try YouTube first
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            if os.path.exists(out_file) and os.path.getsize(out_file) > 10000:
                logger.info(f"Downloaded audio from YouTube for '{title}'")
                return out_file
    except Exception as e:
        logger.warning(f"YouTube download failed for '{title}': {e}. Trying SoundCloud fallback...")

    # 2. Try SoundCloud fallback
    try:
        clean_title = re.sub(r"[^\w\s-]", "", title).strip()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"scsearch1:{clean_title}"])
            if os.path.exists(out_file) and os.path.getsize(out_file) > 10000:
                logger.info(f"Downloaded audio from SoundCloud fallback for '{title}'")
                return out_file
    except Exception as e:
        logger.error(f"SoundCloud fallback download failed for '{title}': {e}")

    return None


class MusicManager:
    """Manages the room music queue and coordinates playback on the room's custom radio."""

    def __init__(self):
        self.queue: list[Track] = []
        self.current: Track | None = None
        self.started_at: float = 0.0
        self.bot = None

        self._skip_event = asyncio.Event()
        self._new_track_event = asyncio.Event()
        self._track_finished_event = asyncio.Event()
        self._runner_task: asyncio.Task | None = None
        self._download_tasks: dict[str, asyncio.Task] = {}

    def start(self, bot_instance=None):
        """Start the background queue runner and radio broadcast engine."""
        if bot_instance:
            self.bot = bot_instance
        if not self._runner_task or self._runner_task.done():
            self._runner_task = asyncio.create_task(self._queue_runner())
            logger.info("Music queue runner initialized.")

        # Initialize global audio broadcaster
        try:
            from broadcaster import broadcaster
            broadcaster.start(self.bot)
        except Exception as e:
            logger.error(f"Failed to start audio broadcaster: {e}")

    def notify_track_finished(self, track: Track):
        """Called by broadcaster when audio stream reaches natural completion."""
        if self.current == track:
            self._track_finished_event.set()

    async def ensure_track_audio_file(self, track: Track | None) -> str | None:
        """Download or retrieve local cached MP3 audio file for a track (deduplicated)."""
        if not track:
            return None
        if track.id in self._download_tasks:
            return await self._download_tasks[track.id]

        task = asyncio.create_task(
            asyncio.to_thread(download_track_audio_sync, track.title, track.id)
        )
        self._download_tasks[track.id] = task
        try:
            return await task
        finally:
            self._download_tasks.pop(track.id, None)

    async def search_and_queue(self, query: str, requester: str) -> tuple[bool, str, Track | None]:
        """Search YouTube asynchronously and enqueue."""
        loop = asyncio.get_running_loop()
        track = await loop.run_in_executor(None, search_youtube_sync, query, requester)
        if not track:
            return False, f"Could not find any songs matching '{query}'.", None

        self.queue.append(track)
        self._new_track_event.set()

        # Start downloading in background immediately so it's ready when popped
        asyncio.create_task(self.ensure_track_audio_file(track))

        pos = len(self.queue)
        if self.current is None and pos == 1:
            msg = f"🎶 Playing now: \"{track.title}\" [{track.duration_str}]"
        else:
            current_playing_text = f" (Playing: \"{self.current.title}\")" if self.current else ""
            msg = f"➕ Added to queue (#{pos}): \"{track.title}\" [{track.duration_str}]{current_playing_text}"
        return True, msg, track

    def skip(self) -> tuple[bool, str]:
        """Skip currently playing track."""
        if not self.current:
            return False, "No music is currently playing on the queue."
        skipped_title = self.current.title
        self._skip_event.set()
        return True, f"⏭️ Skipped: \"{skipped_title}\""

    def get_state(self) -> dict:
        """Get synchronized player state for frontend."""
        position = 0.0
        if self.current and self.started_at > 0:
            position = max(0.0, time.time() - self.started_at)

        base_url = config.RENDER_EXTERNAL_URL or f"http://localhost:{config.PORT}"

        return {
            "is_playing": self.current is not None,
            "position_sec": round(position, 2),
            "now_playing": self.current.to_dict() if self.current else None,
            "radio_stream_url": f"{base_url}/radio.mp3",
            "queue": [t.to_dict() for t in self.queue],
            "queue_count": len(self.queue),
        }

    async def _wait_for_skip_or_finish(self):
        """Wait for either a manual skip or natural track completion."""
        skip_task = asyncio.create_task(self._skip_event.wait())
        finish_task = asyncio.create_task(self._track_finished_event.wait())
        done, pending = await asyncio.wait(
            [skip_task, finish_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()

    async def _queue_runner(self):
        """Continuously advances tracks from queue."""
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
                            f"📻 Room Radio: {base_url}/radio.mp3"
                        )
                        await self.bot.highrise.chat(announcement)
                    except Exception as e:
                        logger.error(f"Error announcing new track in room chat: {e}")

                # Wait for manual skip, natural completion, or safety duration timeout
                self._skip_event.clear()
                self._track_finished_event.clear()
                duration = max(self.current.duration_sec, 5)

                try:
                    await asyncio.wait_for(
                        self._wait_for_skip_or_finish(),
                        timeout=duration + 10
                    )
                except asyncio.TimeoutError:
                    pass  # Duration reached naturally
                finally:
                    self.current = None

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in queue runner: {e}", exc_info=True)
                await asyncio.sleep(2)


# Global singleton music manager
music_manager = MusicManager()
