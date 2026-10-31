import asyncio
import json
import logging
import math
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
import config

logger = logging.getLogger("MusicManager")

# Stop words ignored during word-overlap matching
STOP_WORDS = {"the", "a", "an", "of", "in", "and", "or", "ft", "feat", "to", "is", "song", "video", "by", "from"}

# Official record labels, publishing houses, and verified music entities
OFFICIAL_CHANNELS = {
    "t-series", "tseries", "saregama music", "saregama", "sony music india", "sony music",
    "zee music company", "zeemusiccompany", "yrf", "svf", "tips official", "speed records",
    "warner music", "universal music", "vevo", "atlantic records", "republic records",
    "columbia records", "interscope", "def jam", "geffen records", "rca records",
    "capitol records", "epic records", "island records", "virgin records"
}

# Buzzwords that indicate non-standard, altered, or promotional variants
REMIX_INDICATORS = {
    "remix", "re-mix", "mix", "slowed", "reverb", "lofi", "lo-fi", "8d", "bass boosted",
    "mashup", "club", "dj", "cover", "acoustic", "live", "concert", "reaction", "teaser",
    "trailer", "status", "ringtone", "short", "shorts", "instrumental", "karaoke", "speed up",
    "sped up", "nightcore"
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
    query: str = ""
    is_autoplay: bool = False

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


def parse_view_count(text: str) -> int:
    """Parse view count text (e.g. '378,226,830 views', '50M views', '2.8K views') into an integer."""
    if not text:
        return 0
    t = text.lower().replace(",", "").replace("views", "").strip()
    try:
        if "b" in t:
            return int(float(t.replace("b", "").strip()) * 1_000_000_000)
        elif "m" in t:
            return int(float(t.replace("m", "").strip()) * 1_000_000)
        elif "k" in t:
            return int(float(t.replace("k", "").strip()) * 1_000)
        digits = re.sub(r"[^\d]", "", t)
        return int(digits) if digits else 0
    except Exception:
        return 0


def score_youtube_candidate(c: dict, query: str) -> float:
    """Calculate composite canonical score for a candidate video. Higher score = more authentic/accepted."""
    q_lower = query.lower().strip()
    user_wants_remix = any(rw in q_lower for rw in ["remix", "mashup", "lofi", "slowed", "reverb", "cover", "live", "acoustic"])
    
    q_words = set(re.findall(r"\w+", q_lower)) - STOP_WORDS
    if not q_words:
        q_words = set(re.findall(r"\w+", q_lower))

    title = (c.get("title") or "").lower()
    channel = (c.get("channel") or "").lower()
    views = c.get("view_count") or 0
    dur = c.get("duration") or 0

    # 1. Hard filter on extreme durations for standard tracks
    if dur > 0 and (dur < 45 or dur > 750):
        return -999.0

    score = 0.0

    # 2. Query word overlap (0 to 100 points)
    t_words = set(re.findall(r"\w+", title))
    c_words = set(re.findall(r"\w+", channel))
    all_words = t_words.union(c_words)
    matched = q_words.intersection(all_words)
    overlap = len(matched) / max(1, len(q_words))

    # Must match at least 50% of query words (or 100% if single word query)
    if len(q_words) >= 2 and overlap < 0.5:
        return -999.0
    if len(q_words) == 1 and overlap < 1.0:
        return -999.0

    score += overlap * 100.0

    # 3. Channel Credibility (+40 for official labels or verified topic/vevo channels)
    is_official_channel = (
        any(lbl in channel for lbl in OFFICIAL_CHANNELS)
        or "topic" in channel
        or "vevo" in channel
    )
    if is_official_channel:
        score += 40.0

    # 4. Title Quality Markers (+25 for official video/audio, +15 for lyric/full song)
    if any(qm in title for qm in ["official video", "official music video", "official audio"]):
        score += 25.0
    elif any(qm in title for qm in ["lyric", "lyrical", "full song", "original"]):
        score += 15.0

    # 5. Popularity / View Count Bonus (Logarithmic scale)
    if views > 0:
        score += math.log10(views) * 10.0

    # 6. Remix / Unwanted Variant Penalty (-120 points)
    has_remix_word = any(re.search(r"\b" + re.escape(rw) + r"\b", title) for rw in REMIX_INDICATORS)
    if has_remix_word and not user_wants_remix:
        score -= 120.0

    return score


def search_youtube_sync(query: str, requester: str = "Anonymous") -> Track | None:
    """Search YouTube for the widely accepted, canonical version of a track using multi-candidate ranking."""
    query = query.strip()
    if not query:
        return None

    # 1. Direct YouTube URL or Video ID check
    url_match = re.search(r"(?:v=|\/|youtu\.be\/)([0-9A-Za-z_-]{11})", query)
    if url_match:
        video_id = url_match.group(1)
        search_url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            import yt_dlp
            opts = {"quiet": True, "extract_flat": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(search_url, download=False)
                t_title = info.get("title") or query
                dur_sec = info.get("duration") or 210
                m, s = divmod(int(dur_sec), 60)
                dur_str = f"{m}:{s:02d}"
                return Track(
                    id=video_id,
                    title=t_title,
                    duration_str=dur_str,
                    duration_sec=int(dur_sec),
                    requester=requester,
                    thumbnail=f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                    url=search_url,
                    query=query,
                )
        except Exception:
            return Track(
                id=video_id,
                title=query,
                duration_str="3:30",
                duration_sec=210,
                requester=requester,
                thumbnail=f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                url=search_url,
                query=query,
            )

    candidates = []

    # 2. Strategy A: Use yt-dlp flat search (fast, structured, includes view counts and channels)
    try:
        import yt_dlp
        ydl_search_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "socket_timeout": 6,
        }
        with yt_dlp.YoutubeDL(ydl_search_opts) as ydl:
            res = ydl.extract_info(f"ytsearch10:{query}", download=False)
            entries = res.get("entries") or []
            for e in entries:
                if e and e.get("id"):
                    candidates.append({
                        "id": e.get("id"),
                        "title": e.get("title") or "",
                        "channel": e.get("channel") or e.get("uploader") or "",
                        "view_count": e.get("view_count") or 0,
                        "duration": e.get("duration") or 0,
                        "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}",
                    })
    except Exception as e:
        logger.debug(f"yt-dlp search error: {e}")

    # 3. Strategy B: Scrape web search results as resilient fallback if yt-dlp yielded few results
    if len(candidates) < 3:
        try:
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
            with urllib.request.urlopen(req, timeout=6) as response:
                html = response.read().decode("utf-8", errors="ignore")
            match = re.search(r"var ytInitialData = ({.*?});</script>", html)
            if match:
                data = json.loads(match.group(1))
                contents = data["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"]["sectionListRenderer"]["contents"]
                for section in contents:
                    if "itemSectionRenderer" in section:
                        for item in section["itemSectionRenderer"]["contents"]:
                            if "videoRenderer" in item:
                                vr = item["videoRenderer"]
                                v_id = vr.get("videoId")
                                if not v_id:
                                    continue
                                v_title = vr.get("title", {}).get("runs", [{}])[0].get("text", "")
                                v_channel = vr.get("ownerText", {}).get("runs", [{}])[0].get("text", "")
                                v_views_text = vr.get("viewCountText", {}).get("simpleText", "")
                                v_views = parse_view_count(v_views_text)
                                v_dur_text = vr.get("lengthText", {}).get("simpleText", "3:00")
                                v_dur = parse_duration_str(v_dur_text)
                                candidates.append({
                                    "id": v_id,
                                    "title": v_title,
                                    "channel": v_channel,
                                    "view_count": v_views,
                                    "duration": v_dur,
                                    "url": f"https://www.youtube.com/watch?v={v_id}",
                                })
        except Exception as html_err:
            logger.debug(f"HTML search parsing error: {html_err}")

    if not candidates:
        logger.warning(f"No YouTube candidates found for query: '{query}'")
        return None

    # 4. Rank candidates by canonical score
    scored_candidates = []
    for c in candidates:
        sc = score_youtube_candidate(c, query)
        scored_candidates.append((sc, c))

    scored_candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_candidate = scored_candidates[0]

    # If all candidates got filtered out (e.g. extreme mismatch), pick best candidate available
    if best_score <= -990.0 and candidates:
        best_candidate = candidates[0]

    v_id = best_candidate["id"]
    dur_sec = int(best_candidate.get("duration") or 210)
    m, s = divmod(dur_sec, 60)
    dur_str = f"{m}:{s:02d}"

    logger.info(
        f"Canonical track resolved for '{query}': '{best_candidate['title']}' "
        f"[Score: {best_score:.1f}, Channel: {best_candidate.get('channel')}, Views: {best_candidate.get('view_count', 0):,}]"
    )

    return Track(
        id=v_id,
        title=best_candidate["title"],
        duration_str=dur_str,
        duration_sec=dur_sec,
        requester=requester,
        thumbnail=f"https://img.youtube.com/vi/{v_id}/hqdefault.jpg",
        url=f"https://www.youtube.com/watch?v={v_id}",
        query=query,
    )


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
    # Under Strict Anti-Remix policy, only return fallback if user explicitly wanted a remix/mashup
    if fallback and user_wants_remix:
        fallback.sort(key=lambda x: x[0])
        return [x[1] for x in fallback]
    return []


def search_jiosaavn_sync(query: str) -> str | None:
    """Search JioSaavn for official licensed studio tracks with CTR popularity ranking."""
    import urllib.parse
    import urllib.request
    try:
        encoded_query = urllib.parse.quote_plus(query.strip())
        url = f"https://www.jiosaavn.com/api.php?__call=autocomplete.get&_format=json&_marker=0&cc=in&includeMetaTags=1&query={encoded_query}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=5) as res:
            data = json.loads(res.read().decode("utf-8", errors="ignore"))
            songs = data.get("songs", {}).get("data", [])
            if not songs:
                return None

            q_clean = query.lower()
            user_wants_remix = any(rw in q_clean for rw in ["remix", "lofi", "slowed", "reverb", "cover"])

            best_song_url = None
            best_ctr = -1
            for song in songs:
                song_url = song.get("url")
                if not song_url or "jiosaavn.com/song/" not in song_url:
                    continue
                s_title = (song.get("title") or "").lower()
                ctr = song.get("ctr") or 0

                # Strict remix avoidance unless requested
                has_remix = any(re.search(r"\b" + re.escape(rw) + r"\b", s_title) for rw in ["remix", "lofi", "slowed", "reverb"])
                if has_remix and not user_wants_remix:
                    continue

                if ctr > best_ctr:
                    best_ctr = ctr
                    best_song_url = song_url

            if best_song_url:
                return best_song_url
            for song in songs:
                s_url = song.get("url")
                if s_url and "jiosaavn.com/song/" in s_url:
                    return s_url
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

    # Tier 1: YouTube with Node JS-runtime and robust multi-client challenge solver
    try:
        ydl_opts_yt = {
            "format": "18/140/251/bestaudio/best",
            "outtmpl": os.path.join(cache_dir, f"{video_id}.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "retries": 2,
            "extractor_retries": 2,
            "socket_timeout": 12,
            "ffmpeg_location": ffmpeg_exe,
            "js_runtimes": {"node": {}},
            "extractor_args": {
                "youtube": {
                    "player_client": ["tv_samsung", "visionos", "android", "web"]
                }
            },
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


DEFAULT_AUTOPLAY_PLAYLIST = [
    "Arijit Singh Kesariya",
    "Ed Sheeran Shape of You",
    "The Weeknd Starboy",
    "Arijit Singh Apna Bana Le",
    "Alan Walker Faded",
    "Arijit Singh Tum Hi Ho",
    "Dua Lipa Levitating",
    "Sanam Re Arijit Singh",
    "The Weeknd Blinding Lights",
    "Raataan Lambiyan Jubin Nautiyal",
    "Coldplay Hymn for the Weekend",
    "Pritam Shayad Arijit Singh",
    "Post Malone Circles",
    "Mon Bojhe Naa Arijit Singh",
    "Imagine Dragons Believer",
    "Kabira Yeh Jawaani Hai Deewani",
    "Charlie Puth Attention",
    "Channa Mereya Arijit Singh",
    "OneRepublic Counting Stars",
    "Gehra Hua Arijit Singh",
]


class MusicManager:
    """Manages the room music queue for the 24/7 custom live radio."""

    def __init__(self):
        self.queue: list[Track] = []
        self.current: Track | None = None
        self.started_at: float = 0.0
        self.bot = None

        self.autoplay_enabled: bool = config.AUTOPLAY_ENABLED
        self.autoplay_instant_takeover: bool = config.AUTOPLAY_INSTANT_TAKEOVER
        self.autoplay_index: int = 0
        self.autoplay_cache_queue: list[Track] = []

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
            for t in self.autoplay_cache_queue:
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
        """Continuously pre-downloads top 3 songs in queue (and upcoming autoplay track) to disk."""
        while True:
            try:
                # 1. Prefetch top 3 human-requested tracks in queue
                top_tracks = list(self.queue[:3])
                for t in top_tracks:
                    if self.is_track_cached(t):
                        continue
                    logger.info(f"Pre-buffering user queue track: '{t.title}' ({t.id})...")
                    await self.ensure_track_audio_file(t)
                    await asyncio.sleep(0.5)

                # 2. If user queue has 0-1 tracks and autoplay is enabled, pre-buffer upcoming autoplay song
                if len(self.queue) <= 1 and self.autoplay_enabled:
                    if not self.autoplay_cache_queue:
                        loop = asyncio.get_running_loop()
                        ap_query = DEFAULT_AUTOPLAY_PLAYLIST[self.autoplay_index % len(DEFAULT_AUTOPLAY_PLAYLIST)]
                        self.autoplay_index += 1
                        ap_track = await loop.run_in_executor(None, search_youtube_sync, ap_query, "RayDJ Radio")
                        if ap_track:
                            ap_track.is_autoplay = True
                            self.autoplay_cache_queue.append(ap_track)

                    for at in list(self.autoplay_cache_queue[:1]):
                        if not self.is_track_cached(at):
                            logger.info(f"Pre-buffering autoplay track: '{at.title}' ({at.id})...")
                            await self.ensure_track_audio_file(at)
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

    async def get_next_autoplay_track(self) -> Track | None:
        """Get or resolve next track from autoplay station rotation."""
        if self.autoplay_cache_queue:
            track = self.autoplay_cache_queue.pop(0)
            self._queue_updated_event.set()
            return track

        loop = asyncio.get_running_loop()
        query = DEFAULT_AUTOPLAY_PLAYLIST[self.autoplay_index % len(DEFAULT_AUTOPLAY_PLAYLIST)]
        self.autoplay_index += 1

        track = await loop.run_in_executor(None, search_youtube_sync, query, "RayDJ Radio")
        if track:
            track.is_autoplay = True
            return track
        return None

    async def pop_next_track(self) -> Track | None:
        """Pop next track to play: User queue always takes top priority."""
        # 1. User requests always take top priority
        if self.queue:
            track = self.queue.pop(0)
            self._queue_updated_event.set()
            return track

        # 2. If user queue is empty and autoplay is enabled, pick next autoplay track
        if self.autoplay_enabled:
            return await self.get_next_autoplay_track()

        return None

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

        # If an autoplay track is currently playing and instant takeover is enabled, interrupt immediately
        from broadcaster import broadcaster
        if self.current and self.current.is_autoplay and self.autoplay_instant_takeover:
            logger.info(f"User requested '{track.title}': taking over autoplay track immediately.")
            broadcaster.skip_current()
        elif self.current is None and len(self.queue) == 1:
            asyncio.create_task(self.ensure_track_audio_file(track))

        pos = len(self.queue)
        if self.current is None and pos == 1:
            msg = f"🎶 Playing now: \"{track.title}\" [{track.duration_str}]"
        elif self.current and self.current.is_autoplay and self.autoplay_instant_takeover:
            msg = f"⚡ Priority request! Playing now: \"{track.title}\" [{track.duration_str}]"
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
            "is_autoplay": bool(self.current.is_autoplay) if self.current else False,
            "autoplay_enabled": self.autoplay_enabled,
            "position_sec": round(position, 2),
            "now_playing": self.current.to_dict() if self.current else None,
            "radio_stream_url": f"{base_url}/radio.mp3",
            "queue": queue_items,
            "queue_count": len(self.queue),
        }


# Global singleton music manager
music_manager = MusicManager()
