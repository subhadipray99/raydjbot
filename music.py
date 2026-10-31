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
    query: str = ""

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
                                query=query,
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
            query=query,
        )

    return None


def clean_search_query(title: str) -> str:
    """Clean video title to produce a high-accuracy search term for audio sources."""
    # Remove bracketed text like [Official Video], (Audio), (Lyrics), (Remix) etc.
    s = re.sub(r"\[.*?\]|\(.*?\)", "", title)
    # Remove common video buzzwords and record label tags
    s = re.sub(
        r"(?i)\b(official\s+video|official\s+audio|music\s+video|lyric\s+video|lyrical\s+video|lyrics|full\s+song|full\s+video|visualizer|audio|hd|4k|svf|t-series|tseries|zee\s+music\s+company|sony\s+music|vevo)\b",
        "",
        s,
    )
    # If title has multiple pipe-delimited segments, the first segment is usually the primary song title
    if "|" in s:
        parts = [p.strip() for p in s.split("|") if p.strip()]
        if parts:
            s = parts[0]
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s or title


def rank_soundcloud_candidates(entries: list, search_term: str, target_dur: int = 0) -> list:
    """Filter and rank SoundCloud search candidates by word overlap, duration match, and natural relevance."""
    stop_words = {"the", "a", "an", "of", "in", "and", "or", "ft", "feat", "to", "is", "song", "video"}
    raw_q_words = re.findall(r"\w+", search_term.lower())
    q_words = [w for w in raw_q_words if w not in stop_words] or raw_q_words
    q_set = set(q_words)

    # Detect if user specifically asked for a remix, mashup, lofi, or cover
    user_wants_remix = any(
        re.search(r"\b" + re.escape(w) + r"\b", search_term.lower())
        for w in ["remix", "mashup", "lofi", "slowed", "reverb", "mix", "cover", "acoustic", "version", "dj"]
    )
    remix_indicators = [
        "remix", "mashup", "lofi", "lo-fi", "slowed", "reverb", "mix", "cover",
        "dj", "re-mix", "acoustic", "version", "tribute", "edit", "flip", "bollytech"
    ]

    valid = []
    fallback = []
    for rank, e in enumerate(entries):
        if not e:
            continue
        title = e.get("title") or ""
        dur = e.get("duration") or 0
        formats = e.get("formats") or []

        # Filter out 30s previews
        if dur > 0 and dur < 45:
            continue
        if formats and all("preview" in str(f.get("format_id", "")).lower() for f in formats):
            continue
        # Filter out multi-hour loops or full movie jukeboxes if the target is a normal track
        if target_dur > 0 and target_dur < 450 and dur > 900:
            continue

        is_unwanted_remix = (not user_wants_remix) and any(
            re.search(r"\b" + re.escape(w) + r"\b", title.lower())
            for w in remix_indicators
        )

        c_words = set(re.findall(r"\w+", title.lower()))
        matched = q_set.intersection(c_words)
        overlap = len(matched) / max(1, len(q_set))

        # Discard if missing majority of query words
        if len(q_set) > 1 and overlap < 0.5:
            continue
        if len(q_set) == 1 and overlap < 1.0:
            continue

        dur_diff = abs(dur - target_dur) if target_dur > 0 else 0
        # Score priority:
        # 1. Higher word overlap (-overlap)
        # 2. Duration close to YouTube video duration (dur_diff > 45, dur_diff)
        # 3. Natural SoundCloud search rank (preserves engine relevance)
        score = (-round(overlap, 2), dur_diff > 45, dur_diff, rank)
        
        if not is_unwanted_remix:
            valid.append((score, e))
        else:
            fallback.append((score, e))

    if valid:
        valid.sort(key=lambda x: x[0])
        return [x[1] for x in valid]
    if fallback:
        fallback.sort(key=lambda x: x[0])
        return [x[1] for x in fallback]
    return []


def search_jiosaavn_sync(query: str) -> str | None:
    """Search JioSaavn for official licensed studio tracks."""
    import urllib.parse
    import urllib.request
    try:
        encoded_query = urllib.parse.quote_plus(query.strip())
        url = f"https://www.jiosaavn.com/api.php?__call=autocomplete.get&_format=json&_marker=0&cc=in&includeMetaTags=1&query={encoded_query}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=5) as res:
            data = json.loads(res.read().decode("utf-8", errors="ignore"))
            songs = data.get("songs", {}).get("data", [])
            for song in songs:
                song_url = song.get("url")
                if song_url and "jiosaavn.com/song/" in song_url:
                    return song_url
    except Exception as e:
        logger.debug(f"JioSaavn search lookup error: {e}")
    return None


def download_track_audio_sync(
    title: str, video_id: str, query: str = "", target_duration: int = 0
) -> str | None:
    """Download audio locally using multi-tier resilient strategy (YouTube -> JioSaavn official -> SoundCloud fallback)."""
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
            "retries": 0,
            "extractor_retries": 0,
            "socket_timeout": 4,
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
                logger.info(f"Downloaded audio from YouTube for '{title}' ({video_id})")
                return out_file
    except Exception as e:
        logger.warning(f"YouTube download failed for '{title}': {e}")

    # Determine the cleanest search term:
    is_url = bool(re.search(r"https?://|(?:v=|\/|youtu\.be\/)[0-9A-Za-z_-]{11}", query))
    if query and not is_url:
        search_term = query.strip()
    else:
        search_term = clean_search_query(title)

    # Tier 2: JioSaavn Official Licensed Studio Audio (guaranteed official track, no remix/cover, unblocked in datacenters)
    saavn_url = search_jiosaavn_sync(search_term)
    if not saavn_url and search_term != title:
        saavn_url = search_jiosaavn_sync(clean_search_query(title))

    if saavn_url:
        logger.info(f"Attempting official JioSaavn download for '{search_term}' ({saavn_url})...")
        try:
            ydl_opts_saavn = {
                "format": "bestaudio/best",
                "outtmpl": os.path.join(cache_dir, f"{video_id}.%(ext)s"),
                "quiet": True,
                "no_warnings": True,
                "ignoreerrors": True,
                "retries": 2,
                "socket_timeout": 8,
                "ffmpeg_location": ffmpeg_exe,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "128",
                }],
            }
            with yt_dlp.YoutubeDL(ydl_opts_saavn) as ydl:
                ydl.download([saavn_url])
                if os.path.exists(out_file) and os.path.getsize(out_file) > 10000:
                    logger.info(f"Successfully downloaded official licensed audio from JioSaavn for '{search_term}' ({video_id})")
                    return out_file
        except Exception as saavn_err:
            logger.warning(f"JioSaavn download failed for '{search_term}': {saavn_err}")

    # Tier 3: Resilient SoundCloud search fallback (for indie tracks not on official services)
    logger.info(f"Attempting resilient SoundCloud search fallback for '{search_term}'...")
    try:
        ydl_opts_sc = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(cache_dir, f"{video_id}.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "retries": 2,
            "socket_timeout": 8,
            "ffmpeg_location": ffmpeg_exe,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }],
        }
        with yt_dlp.YoutubeDL(ydl_opts_sc) as ydl:
            info = ydl.extract_info(f"scsearch8:{search_term}", download=False)
            raw_entries = info.get("entries") or []
            candidates = rank_soundcloud_candidates(raw_entries, search_term, target_duration)
            for e in candidates:
                target_url = e.get("webpage_url") or e.get("url")
                if not target_url:
                    continue
                dur = e.get("duration") or 0
                try:
                    logger.info(f"Trying SoundCloud candidate: '{e.get('title')}' ({dur:.0f}s)")
                    ydl.download([target_url])
                    if os.path.exists(out_file) and os.path.getsize(out_file) > 10000:
                        logger.info(f"Successfully downloaded audio from SoundCloud for '{search_term}' ({video_id})")
                        return out_file
                except Exception as candidate_err:
                    logger.debug(f"Candidate '{e.get('title')}' failed: {candidate_err}. Trying next candidate...")

    except Exception as e:
        logger.warning(f"SoundCloud fallback search failed for '{search_term}': {e}")

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
        self._queue_updated_event = asyncio.Event()
        self._download_tasks: dict[str, asyncio.Task] = {}
        self._prefetch_task: asyncio.Task | None = None

    def start(self, bot_instance=None):
        """Start the 24/7 radio broadcast engine and top-3 prefetch worker."""
        if bot_instance:
            self.bot = bot_instance
        try:
            from broadcaster import broadcaster
            broadcaster.start(self.bot)
            logger.info("24/7 Live Radio engine started.")
        except Exception as e:
            logger.error(f"Failed to start audio broadcaster: {e}")

        if not self._prefetch_task or self._prefetch_task.done():
            self._prefetch_task = asyncio.create_task(self._prefetch_worker())
            logger.info("Top-3 queue background prefetcher started.")

    def is_track_cached(self, track: Track | None) -> bool:
        """Check if audio file for a track is already cached on disk."""
        if not track:
            return False
        f = os.path.join("cache", f"{track.id}.mp3")
        return os.path.exists(f) and os.path.getsize(f) > 10000

    def _clean_old_cache(self, keep_limit: int = 25):
        """Keep the cache directory clean by removing tracks that are no longer active or queued."""
        try:
            cache_dir = "cache"
            if not os.path.exists(cache_dir):
                return
            active_ids = set()
            if self.current:
                active_ids.add(f"{self.current.id}.mp3")
            for t in self.queue:
                active_ids.add(f"{t.id}.mp3")

            files = []
            for f in os.listdir(cache_dir):
                if f.endswith(".mp3"):
                    p = os.path.join(cache_dir, f)
                    files.append((p, os.path.getmtime(p), f))

            if len(files) > keep_limit:
                files.sort(key=lambda x: x[1])  # oldest first
                for p, mtime, f in files:
                    if f not in active_ids:
                        try:
                            os.remove(p)
                            logger.info(f"Cleaned up old cached audio: {f}")
                        except Exception:
                            pass
                        if len([x for x in os.listdir(cache_dir) if x.endswith(".mp3")]) <= keep_limit:
                            break
        except Exception as e:
            logger.debug(f"Cache cleanup error: {e}")

    async def _prefetch_worker(self):
        """Continuously pre-downloads top 3 songs in queue to disk so playback transitions instantly."""
        while True:
            try:
                # Inspect top 3 queued tracks
                top_tracks = list(self.queue[:3])
                for t in top_tracks:
                    if self.is_track_cached(t):
                        continue
                    logger.info(f"Pre-buffering queued track: '{t.title}' ({t.id})...")
                    await self.ensure_track_audio_file(t)
                    await asyncio.sleep(0.5)

                self._clean_old_cache(keep_limit=25)

                try:
                    await asyncio.wait_for(self._queue_updated_event.wait(), timeout=5.0)
                    self._queue_updated_event.clear()
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in queue prefetch worker: {e}")
                await asyncio.sleep(3.0)

    async def ensure_track_audio_file(self, track: Track | None) -> str | None:
        """Download or retrieve local cached MP3 audio file for a track (deduplicated)."""
        if not track:
            return None
        out_file = os.path.join("cache", f"{track.id}.mp3")
        if os.path.exists(out_file) and os.path.getsize(out_file) > 10000:
            return out_file

        if track.id in self._download_tasks:
            return await self._download_tasks[track.id]

        task = asyncio.create_task(
            asyncio.to_thread(
                download_track_audio_sync,
                track.title,
                track.id,
                track.query,
                track.duration_sec,
            )
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
        self._queue_updated_event.set()

        # If nothing is currently playing and this is the only track, start downloading immediately
        if self.current is None and len(self.queue) == 1:
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

        queue_items = []
        for t in self.queue:
            d = t.to_dict()
            d["is_cached"] = self.is_track_cached(t)
            queue_items.append(d)

        return {
            "is_playing": self.current is not None and self.started_at > 0,
            "is_loading": self.current is not None and self.started_at == 0,
            "position_sec": round(position, 2),
            "now_playing": self.current.to_dict() if self.current else None,
            "radio_stream_url": f"{base_url}/radio.mp3",
            "queue": queue_items,
            "queue_count": len(self.queue),
        }


# Global singleton music manager
music_manager = MusicManager()
