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

_ytmusic_instance = None

def get_ytmusic():
    global _ytmusic_instance
    if _ytmusic_instance is None:
        try:
            import ytmusicapi
            _ytmusic_instance = ytmusicapi.YTMusic()
        except Exception as e:
            logger.warning(f"Failed to initialize YTMusic: {e}")
            return None
    return _ytmusic_instance

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
    artist: str = ""
    album: str = ""

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
        artist=best_candidate.get("channel", ""),
    )


def search_ytmusic_sync(query: str, requester: str = "Anonymous") -> Track | None:
    """Search YouTube Music for canonical studio track, filtering out unwanted remixes and covers."""
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
                    artist=info.get("artist") or info.get("uploader") or "",
                    album=info.get("album") or "",
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

    user_wants_remix = any(
        rw in query.lower()
        for rw in ["remix", "mashup", "lofi", "slowed", "reverb", "cover", "live", "acoustic", "mix", "dj"]
    )

    yt = get_ytmusic()
    if yt:
        try:
            results = yt.search(query, filter="songs")
            if not results:
                results = yt.search(query)

            candidates = []
            for s in (results or []):
                v_id = s.get("videoId")
                if not v_id:
                    continue
                title = s.get("title") or ""
                artists_list = [a.get("name") for a in s.get("artists", []) if a.get("name")]
                artist_str = ", ".join(artists_list)
                album_str = s.get("album", {}).get("name") if s.get("album") else ""
                dur_sec = s.get("duration_seconds")
                dur_str = s.get("duration") or "3:30"
                if not dur_sec:
                    dur_sec = parse_duration_str(dur_str)

                # Check if this result is an unwanted remix
                title_lower = title.lower()
                is_remix = any(
                    re.search(r"\b" + re.escape(rw) + r"\b", title_lower)
                    for rw in ["remix", "lofi", "lo-fi", "slowed", "reverb", "mashup", "acoustic", "cover", "dj"]
                )
                if is_remix and not user_wants_remix:
                    continue

                # Pick highest resolution thumbnail
                thumb_url = f"https://img.youtube.com/vi/{v_id}/hqdefault.jpg"
                thumbs = s.get("thumbnails", [])
                if thumbs:
                    thumb_url = thumbs[-1].get("url") or thumb_url

                candidates.append({
                    "id": v_id,
                    "title": title,
                    "artist": artist_str,
                    "album": album_str,
                    "duration_str": dur_str,
                    "duration_sec": int(dur_sec),
                    "thumbnail": thumb_url,
                    "url": f"https://music.youtube.com/watch?v={v_id}",
                })

            if candidates:
                best = candidates[0]
                logger.info(
                    f"YTMusic resolved '{query}' -> '{best['title']}' by {best['artist']} ({best['album']}) [{best['id']}]"
                )
                return Track(
                    id=best["id"],
                    title=best["title"],
                    duration_str=best["duration_str"],
                    duration_sec=best["duration_sec"],
                    requester=requester,
                    thumbnail=best["thumbnail"],
                    url=best["url"],
                    query=query,
                    artist=best["artist"],
                    album=best["album"],
                )
        except Exception as e:
            logger.warning(f"YTMusic search failed for '{query}': {e}")

    # Fallback to standard YouTube search if YTMusic is unavailable or returns no candidates
    return search_youtube_sync(query, requester)


def search_track_sync(query: str, requester: str = "Anonymous") -> Track | None:
    """Unified search: prefers ytmusicapi for 100% canonical studio tracks, falls back to YouTube."""
    return search_ytmusic_sync(query, requester)


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


def search_jiosaavn_sync(title: str, artist: str = "", query: str = "") -> str | None:
    """Search JioSaavn for official licensed studio tracks with artist and title precision."""
    try:
        clean_title = re.sub(r"\(.*?\)|\[.*?\]", "", title).strip()
        primary_artist = artist.split(",")[0].strip() if artist else ""

        # Try search queries: 1) clean_title + primary_artist, 2) clean_title, 3) raw query
        queries_to_try = []
        if primary_artist:
            queries_to_try.append(f"{clean_title} {primary_artist}")
        queries_to_try.append(clean_title)
        if query and query != clean_title and query != title:
            queries_to_try.append(query)

        user_wants_remix = any(
            rw in (query or title).lower()
            for rw in ["remix", "lofi", "slowed", "reverb", "cover"]
        )
        clean_title_words = set(re.findall(r"\w+", clean_title.lower())) - STOP_WORDS
        if not clean_title_words:
            clean_title_words = set(re.findall(r"\w+", clean_title.lower()))

        for sq in queries_to_try:
            try:
                encoded_query = urllib.parse.quote_plus(sq)
                url = f"https://www.jiosaavn.com/api.php?__call=autocomplete.get&_format=json&_marker=0&cc=in&includeMetaTags=1&query={encoded_query}"
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                )
                with urllib.request.urlopen(req, timeout=5) as res:
                    data = json.loads(res.read().decode("utf-8", errors="ignore"))
                    songs = data.get("songs", {}).get("data", [])
                    if not songs:
                        continue

                    best_song_url = None
                    best_ctr = -1
                    for song in songs:
                        song_url = song.get("url")
                        if not song_url or "jiosaavn.com/song/" not in song_url:
                            continue
                        s_title = (song.get("title") or "").lower()
                        ctr = song.get("ctr") or 0

                        # Strict remix avoidance unless requested
                        has_remix = any(
                            re.search(r"\b" + re.escape(rw) + r"\b", s_title)
                            for rw in ["remix", "lofi", "slowed", "reverb"]
                        )
                        if has_remix and not user_wants_remix:
                            continue

                        s_words = set(re.findall(r"\w+", s_title))
                        overlap = len(clean_title_words.intersection(s_words))
                        if overlap > 0 and ctr > best_ctr:
                            best_ctr = ctr
                            best_song_url = song_url

                    if best_song_url:
                        return best_song_url
                    for song in songs:
                        s_url = song.get("url")
                        if s_url and "jiosaavn.com/song/" in s_url:
                            return s_url
            except Exception:
                continue

    except Exception as e:
        logger.debug(f"JioSaavn search lookup error: {e}")
    return None


def resolve_audio_stream_source_sync(track: Track) -> str | None:
    """Resolve high-quality audio stream source in <1s (Local Cache -> JioSaavn CDN -> YouTube -> SoundCloud)."""
    # 1. Local disk cache check (0.00s latency)
    cache_file = os.path.join("cache", f"{track.id}.mp3")
    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 10000:
        logger.info(f"Using local cached audio for '{track.title}' ({cache_file})")
        return os.path.abspath(cache_file)

    import yt_dlp

    # If the user requested an explicit YouTube URL, prioritize YouTube stream extraction
    is_direct_yt = bool(re.search(r"https?://|(?:v=|\/|youtu\.be\/)[0-9A-Za-z_-]{11}", track.query))
    if is_direct_yt:
        try:
            target_url = track.url or f"https://www.youtube.com/watch?v={track.id}"
            with yt_dlp.YoutubeDL({
                "format": "bestaudio/best",
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": 5,
            }) as ydl:
                info = ydl.extract_info(target_url, download=False)
                stream_url = info.get("url")
                if not stream_url and "formats" in info:
                    for f in info["formats"]:
                        if f.get("acodec") != "none" and f.get("url"):
                            stream_url = f.get("url")
                            break
                if stream_url:
                    logger.info(f"Resolved direct YouTube stream URL for '{track.title}'")
                    return stream_url
        except Exception as e:
            logger.debug(f"Direct YouTube stream extraction failed: {e}")

    # 2. Tier 1: JioSaavn Official Licensed Studio Audio Stream URL
    # Highly resilient, 320kbps CD/studio quality, never blocked by datacenter IPs on Render
    try:
        saavn_url = search_jiosaavn_sync(track.title, track.artist, track.query)
        if saavn_url:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "socket_timeout": 5}) as ydl:
                info = ydl.extract_info(saavn_url, download=False)
                stream_url = info.get("url")
                if stream_url:
                    logger.info(f"Resolved JioSaavn studio stream URL for '{track.title}' ({saavn_url})")
                    return stream_url
    except Exception as saavn_err:
        logger.debug(f"JioSaavn stream extraction failed for '{track.title}': {saavn_err}")

    # 3. Tier 2: YouTube Music / YouTube Stream URL
    try:
        target_url = track.url or f"https://music.youtube.com/watch?v={track.id}"
        with yt_dlp.YoutubeDL({
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 5,
        }) as ydl:
            info = ydl.extract_info(target_url, download=False)
            stream_url = info.get("url")
            if not stream_url and "formats" in info:
                for f in info["formats"]:
                    if f.get("acodec") != "none" and f.get("url"):
                        stream_url = f.get("url")
                        break
            if stream_url:
                logger.info(f"Resolved YouTube Music stream URL for '{track.title}'")
                return stream_url
    except Exception as yt_err:
        logger.debug(f"YouTube stream extraction failed for '{track.title}': {yt_err}")

    # 4. Tier 3: SoundCloud Fallback Stream URL
    try:
        search_term = f"{clean_search_query(track.title)} {track.artist}".strip() or track.title
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "socket_timeout": 5}) as ydl:
            info = ydl.extract_info(f"scsearch3:{search_term}", download=False)
            entries = info.get("entries") or []
            if entries and entries[0]:
                target_url = entries[0].get("webpage_url") or entries[0].get("url")
                sub_info = ydl.extract_info(target_url, download=False)
                stream_url = sub_info.get("url")
                if stream_url:
                    logger.info(f"Resolved SoundCloud fallback stream URL for '{track.title}'")
                    return stream_url
    except Exception as sc_err:
        logger.debug(f"SoundCloud fallback stream extraction failed for '{track.title}': {sc_err}")

    logger.error(f"Failed to resolve playable audio stream source for '{track.title}' ({track.id})")
    return None


def download_track_audio_sync(
    title: str, video_id: str, query: str = "", target_duration: int = 0, artist: str = ""
) -> str | None:
    """Download audio locally using multi-tier resilient strategy (JioSaavn official -> YouTube -> SoundCloud fallback)."""
    import yt_dlp
    import imageio_ffmpeg

    cache_dir = "cache"
    os.makedirs(cache_dir, exist_ok=True)
    out_file = os.path.join(cache_dir, f"{video_id}.mp3")

    if os.path.exists(out_file) and os.path.getsize(out_file) > 10000:
        return out_file

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    is_url = bool(re.search(r"https?://|(?:v=|\/|youtu\.be\/)[0-9A-Za-z_-]{11}", query))
    search_term = query.strip() if (query and not is_url) else clean_search_query(title)

    # Strategy: If not a direct YouTube link, try JioSaavn FIRST (fastest, unblocked in datacenters, official studio audio)
    if not is_url:
        saavn_url = search_jiosaavn_sync(title, artist, query)
        if not saavn_url and query:
            saavn_url = search_jiosaavn_sync(query, artist, query)

        if saavn_url:
            logger.info(f"Attempting official JioSaavn download for '{title}' ({saavn_url})...")
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
                        logger.info(f"Successfully downloaded official licensed audio from JioSaavn for '{title}' ({video_id})")
                        return out_file
            except Exception as saavn_err:
                logger.warning(f"JioSaavn download failed for '{title}': {saavn_err}")

    # Fallback / Direct YouTube Link: Try YouTube
    try:
        ydl_opts_yt = {
            "format": "18/140/251/bestaudio/best",
            "outtmpl": os.path.join(cache_dir, f"{video_id}.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "retries": 1,
            "extractor_retries": 1,
            "socket_timeout": 6,
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

    # If it was a direct URL and YouTube failed, now try JioSaavn fallback
    if is_url:
        saavn_url = search_jiosaavn_sync(title, artist, query)
        if saavn_url:
            logger.info(f"Attempting JioSaavn download fallback for '{title}'...")
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
                        return out_file
            except Exception:
                pass

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
                        ap_track = await loop.run_in_executor(None, search_track_sync, ap_query, "RayDJ Radio")
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

        track = await loop.run_in_executor(None, search_track_sync, query, "RayDJ Radio")
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

    async def resolve_audio_stream_source(self, track: Track | None) -> str | None:
        """Resolve audio stream URL or local path asynchronously in <1s."""
        if not track:
            return None
        return await asyncio.to_thread(resolve_audio_stream_source_sync, track)

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
                track.artist,
            )
        )
        self._download_tasks[track.id] = task
        try:
            return await task
        finally:
            self._download_tasks.pop(track.id, None)

    async def search_and_queue(self, query: str, requester: str) -> tuple[bool, str, Track | None]:
        """Search music asynchronously and enqueue."""
        loop = asyncio.get_running_loop()
        track = await loop.run_in_executor(None, search_track_sync, query, requester)
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
            asyncio.create_task(self.resolve_audio_stream_source(track))

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
