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


def clean_search_query(title: str) -> str:
    """Clean video title to produce a high-accuracy search term for audio sources."""
    # Remove bracketed text like [Official Video], (Audio), (Lyrics), (Remix) etc.
    s = re.sub(r"\[.*?\]|\(.*?\)", "", title)
    # Remove common video buzzwords
    s = re.sub(r"(?i)\b(official\s+video|official\s+audio|music\s+video|lyric\s+video|lyrics|full\s+song|visualizer|audio|hd|4k)\b", "", s)
    # If title has multiple pipe-delimited segments, keep only the first two (Song + Artist / Movie)
    if "|" in s:
        parts = [p.strip() for p in s.split("|") if p.strip()]
        s = " ".join(parts[:2])
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s or title


def download_track_audio_sync(title: str, video_id: str) -> str | None:
    """Download audio locally using multi-tier resilient strategy (YouTube default -> embedded -> SoundCloud fallback)."""
    import yt_dlp
    import imageio_ffmpeg

    cache_dir = "cache"
    os.makedirs(cache_dir, exist_ok=True)
    out_file = os.path.join(cache_dir, f"{video_id}.mp3")

    if os.path.exists(out_file) and os.path.getsize(out_file) > 10000:
        return out_file

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    # Tier 1: YouTube using modern yt-dlp default extractor (uses visionos/tv_samsung)
    try:
        ydl_opts_yt = {
            "format": "18/140/251/bestaudio/best",
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
        with yt_dlp.YoutubeDL(ydl_opts_yt) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            if os.path.exists(out_file) and os.path.getsize(out_file) > 10000:
                logger.info(f"Downloaded audio from YouTube for '{title}' ({video_id}) via default client")
                return out_file
    except Exception as e:
        logger.warning(f"YouTube default download failed for '{title}': {e}")

    # Tier 2: YouTube with web_embedded/tv_samsung client
    try:
        ydl_opts_embed = {
            "format": "18/140/bestaudio/best",
            "outtmpl": os.path.join(cache_dir, f"{video_id}.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "ffmpeg_location": ffmpeg_exe,
            "extractor_args": {
                "youtube": {
                    "player_client": ["web_embedded", "tv_samsung"]
                }
            },
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }],
        }
        with yt_dlp.YoutubeDL(ydl_opts_embed) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            if os.path.exists(out_file) and os.path.getsize(out_file) > 10000:
                logger.info(f"Downloaded audio from YouTube for '{title}' ({video_id}) via embedded client")
                return out_file
    except Exception as e:
        logger.warning(f"YouTube embedded download failed for '{title}': {e}")

    # Tier 3: SoundCloud search fallback (bypasses YouTube datacenter bot challenges completely!)
    clean_query = clean_search_query(title)
    logger.info(f"Attempting resilient SoundCloud search fallback for '{clean_query}'...")
    try:
        ydl_opts_sc = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(cache_dir, f"{video_id}.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "ffmpeg_location": ffmpeg_exe,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }],
        }
        with yt_dlp.YoutubeDL(ydl_opts_sc) as ydl:
            info = ydl.extract_info(f"scsearch5:{clean_query}", download=False)
            entries = [e for e in (info.get("entries") or []) if e]
            # Prioritize full non-preview tracks (> 60s) before short previews
            entries.sort(key=lambda x: (
                1 if any("preview" in f.get("format_id", "") for f in x.get("formats", [])) else 0,
                -(x.get("duration") or 0)
            ))
            for e in entries:
                target_url = e.get("webpage_url") or e.get("url")
                if not target_url:
                    continue
                dur = e.get("duration") or 0
                try:
                    logger.info(f"Trying SoundCloud candidate: '{e.get('title')}' ({dur:.0f}s)")
                    ydl.download([target_url])
                    if os.path.exists(out_file) and os.path.getsize(out_file) > 10000:
                        logger.info(f"Successfully downloaded audio from SoundCloud for '{clean_query}' ({video_id})")
                        return out_file
                except Exception as candidate_err:
                    logger.debug(f"Candidate '{e.get('title')}' failed: {candidate_err}. Trying next candidate...")

    except Exception as e:
        logger.warning(f"SoundCloud fallback search failed for '{clean_query}': {e}")

    logger.error(f"Failed to download audio for '{title}' ({video_id}) across all available sources.")
    return None


class MusicManager:
    """Manages the room music queue for the 24/7 custom live radio."""

    def __init__(self):
        self.queue: list[Track] = []
        self.current: Track | None = None
        self.started_at: float = 0.0
        self.bot = None

        self._new_track_event = asyncio.Event()
        self._download_tasks: dict[str, asyncio.Task] = {}

    def start(self, bot_instance=None):
        """Start the 24/7 radio broadcast engine."""
        if bot_instance:
            self.bot = bot_instance
        try:
            from broadcaster import broadcaster
            broadcaster.start(self.bot)
            logger.info("24/7 Live Radio engine started.")
        except Exception as e:
            logger.error(f"Failed to start audio broadcaster: {e}")

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

        # Start downloading in background immediately so it is ready when popped
        asyncio.create_task(self.ensure_track_audio_file(track))

        pos = len(self.queue)
        if self.current is None and pos == 1:
            msg = f"🎶 Playing now: \"{track.title}\" [{track.duration_str}]"
        else:
            current_playing_text = f" (Playing: \"{self.current.title}\")" if self.current else ""
            msg = f"➕ Added to queue (#{pos}): \"{track.title}\" [{track.duration_str}]{current_playing_text}"
        return True, msg, track

    def skip(self) -> tuple[bool, str]:
        """Skip currently playing track immediately."""
        from broadcaster import broadcaster
        return broadcaster.skip_current()

    def get_state(self) -> dict:
        """Get synchronized player state for frontend."""
        position = 0.0
        if self.current and self.started_at > 0:
            position = max(0.0, time.time() - self.started_at)

        base_url = config.RENDER_EXTERNAL_URL or f"http://localhost:{config.PORT}"

        return {
            "is_playing": self.current is not None and self.started_at > 0,
            "is_loading": self.current is not None and self.started_at == 0,
            "position_sec": round(position, 2),
            "now_playing": self.current.to_dict() if self.current else None,
            "radio_stream_url": f"{base_url}/radio.mp3",
            "queue": [t.to_dict() for t in self.queue],
            "queue_count": len(self.queue),
        }


# Global singleton music manager
music_manager = MusicManager()
