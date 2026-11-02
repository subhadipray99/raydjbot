import os
import re
import json
import logging
import time
import asyncio
import collections
import aiohttp
from aiohttp import web
import config
from music import music_manager
from broadcaster import broadcaster
import position
import tips
import db

logger = logging.getLogger("WebServer")
start_time = time.time()

# In-memory log buffer for live diagnostics
recent_logs = collections.deque(maxlen=150)

class MemoryLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            recent_logs.append(msg)
        except Exception:
            pass

memory_handler = MemoryLogHandler()
memory_handler.setLevel(logging.INFO)
memory_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().setLevel(logging.INFO)
logging.getLogger().addHandler(memory_handler)

REGISTERED_BOTS_FILE = "registered_bots.json"


def get_registered_bots() -> list:
    """Retrieve list of registered customer bot URLs for 10-minute master keep-alive pings."""
    if not os.path.exists(REGISTERED_BOTS_FILE):
        return []
    try:
        with open(REGISTERED_BOTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"[Pinger] Could not read {REGISTERED_BOTS_FILE}: {e}")
        return []


def register_bot_url(url: str):
    """Add a new bot URL to registered_bots.json so it is automatically pinged every 10 minutes."""
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        return
    bots = get_registered_bots()
    if cleaned not in bots:
        bots.append(cleaned)
        try:
            with open(REGISTERED_BOTS_FILE, "w", encoding="utf-8") as f:
                json.dump(bots, f, indent=2)
            logger.info(f"[Pinger] Enrolled new bot in 10-minute master keep-alive network: {cleaned}")
        except Exception as e:
            logger.error(f"[Pinger] Failed to save {REGISTERED_BOTS_FILE}: {e}")


async def run_master_pinger_loop():
    """Background task pinging all registered buyer bots every 10 minutes (600s) to keep them awake 24/7."""
    await asyncio.sleep(45)  # Initial grace delay after server startup
    while True:
        try:
            bots = list(set(get_registered_bots()))
            # Also combine with bots stored in Supabase
            try:
                db_bots = await db.get_all_active_bots_from_db()
                for b_url in db_bots:
                    base = b_url.replace("/health", "").rstrip("/")
                    if base and base not in bots:
                        bots.append(base)
            except Exception as dbe:
                logger.debug(f"[Pinger] Supabase bots lookup notice: {dbe}")

            if bots:
                logger.info(f"[Pinger] Starting 10-minute keep-alive ping cycle for {len(bots)} registered bot(s)...")
                async with aiohttp.ClientSession() as session:
                    for base_url in bots:
                        ping_url = f"{base_url}/health" if not base_url.endswith("/health") else base_url
                        try:
                            async with session.get(ping_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                                logger.info(f"[Pinger] Pinged {ping_url} -> HTTP {resp.status}")
                        except Exception as p_err:
                            logger.warning(f"[Pinger] Ping to {ping_url} failed: {p_err}")
        except Exception as loop_err:
            logger.error(f"[Pinger] Unexpected error in master pinger loop: {loop_err}")
        await asyncio.sleep(600)  # Exactly 10 minutes


def get_uptime() -> str:
    elapsed_seconds = int(time.time() - start_time)
    hours, remainder = divmod(elapsed_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes}m {seconds}s"


def get_base_url(request: web.Request) -> str:
    """Dynamically resolve the external base URL from custom domain or incoming request headers."""
    forwarded_host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host")
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "https")
    if forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}"
    if config.CUSTOM_DOMAIN:
        return config.CUSTOM_DOMAIN
    return config.RENDER_EXTERNAL_URL or f"http://localhost:{config.PORT}"


async def handle_status_api(request: web.Request) -> web.Response:
    """JSON status API endpoint."""
    base_url = get_base_url(request)
    return web.json_response({
        "status": "online",
        "service": "ZevRay Highrise Bot SaaS & Jukebox",
        "domain": base_url,
        "uptime": get_uptime(),
        "room_configured": bool(config.ROOM_ID),
        "room_id": config.ROOM_ID,
        "saved_position": position.get_saved_position_dict(config.ROOM_ID),
        "floors": position.get_all_floors(config.ROOM_ID),
        "top_tipper": tips.get_top_tipper(config.ROOM_ID),
        "radio_stream_url": f"{base_url}/radio.mp3",
        "radio_url": f"{base_url}/radio.mp3",
        "player_url": f"{base_url}/player",
        "deploy_portal_url": f"{base_url}/",
        "registered_bots_count": len(get_registered_bots()),
        "master_pinger": "active (10m interval)",
        "music_state": music_manager.get_state(),
    })


async def handle_root(request: web.Request) -> web.Response:
    """Root homepage: serves SaaS portal to browsers and JSON status to API clients."""
    accept_header = request.headers.get("Accept", "")
    if "text/html" in accept_header and request.query.get("format") != "json":
        return await handle_portal_page(request)
    return await handle_status_api(request)


async def handle_health(request: web.Request) -> web.Response:
    """Lightweight 200 OK endpoint for cron-job / UptimeRobot / Render health checks."""
    return web.Response(text="OK", status=200)


async def handle_player_state(request: web.Request) -> web.Response:
    """API endpoint providing current synchronized music state."""
    return web.json_response(music_manager.get_state())


PLAYER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Highrise Live Room Jukebox</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: radial-gradient(circle at top, #1f1235 0%, #0d0a1a 100%);
      color: #f3f4f6;
      font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 24px 16px;
    }
    .header {
      width: 100%;
      max-width: 560px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 24px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(239, 68, 68, 0.2);
      border: 1px solid rgba(239, 68, 68, 0.4);
      color: #f87171;
      padding: 4px 12px;
      border-radius: 9999px;
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .badge .dot {
      width: 8px;
      height: 8px;
      background: #ef4444;
      border-radius: 50%;
      box-shadow: 0 0 10px #ef4444;
      animation: pulse 1.5s infinite;
    }
    @keyframes pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(0.85); } }
    .card {
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.1);
      backdrop-filter: blur(16px);
      border-radius: 24px;
      width: 100%;
      max-width: 560px;
      padding: 28px;
      box-shadow: 0 20px 40px rgba(0,0,0,0.4);
      margin-bottom: 20px;
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
    }
    .art-container {
      position: relative;
      width: 220px;
      height: 220px;
      border-radius: 20px;
      overflow: hidden;
      box-shadow: 0 12px 30px rgba(147, 51, 234, 0.3);
      margin-bottom: 20px;
      background: #181428;
    }
    .art-container img {
      width: 100%;
      height: 100%;
      object-fit: cover;
    }
    .track-title {
      font-size: 20px;
      font-weight: 800;
      line-height: 1.3;
      margin-bottom: 8px;
      color: #ffffff;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .requester {
      font-size: 14px;
      color: #a78bfa;
      font-weight: 600;
      margin-bottom: 18px;
    }
    .progress-bar-container {
      width: 100%;
      height: 6px;
      background: rgba(255, 255, 255, 0.1);
      border-radius: 999px;
      overflow: hidden;
      margin-bottom: 8px;
    }
    .progress-bar {
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, #8b5cf6, #ec4899);
      border-radius: 999px;
      transition: width 0.3s ease;
    }
    .time-info {
      width: 100%;
      display: flex;
      justify-content: space-between;
      font-size: 12px;
      color: #9ca3af;
      margin-bottom: 20px;
    }
    .btn-sync {
      background: linear-gradient(135deg, #7c3aed 0%, #ec4899 100%);
      color: white;
      border: none;
      padding: 14px 28px;
      border-radius: 999px;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 10px;
      box-shadow: 0 8px 25px rgba(236, 72, 153, 0.35);
      transition: transform 0.2s, box-shadow 0.2s;
    }
    .btn-sync:hover {
      transform: translateY(-2px);
      box-shadow: 0 12px 30px rgba(236, 72, 153, 0.5);
    }
    .queue-card {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 20px;
      width: 100%;
      max-width: 560px;
      padding: 20px;
    }
    .queue-header {
      font-size: 15px;
      font-weight: 700;
      color: #d1d5db;
      margin-bottom: 14px;
      text-align: left;
    }
    .queue-item {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 10px;
      border-radius: 12px;
      background: rgba(255, 255, 255, 0.02);
      margin-bottom: 8px;
      text-align: left;
    }
    .queue-thumb {
      width: 48px;
      height: 48px;
      border-radius: 8px;
      object-fit: cover;
      flex-shrink: 0;
    }
    .queue-info {
      flex: 1;
      min-width: 0;
    }
    .queue-title {
      font-size: 13px;
      font-weight: 600;
      color: #f3f4f6;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .queue-sub {
      font-size: 11px;
      color: #9ca3af;
    }
    .instructions {
      font-size: 12px;
      color: #6b7280;
      margin-top: 16px;
      line-height: 1.5;
      text-align: center;
    }
  </style>
</head>
<body>
  <div class="header">
    <h2 style="font-size: 18px; font-weight: 800; letter-spacing: -0.02em;">🎧 Highrise Live Jukebox</h2>
    <div class="badge"><span class="dot"></span> SYNCED</div>
  </div>

  <div class="card">
    <div class="art-container">
      <img id="track-art" src="https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=500" alt="Album Cover">
    </div>
    <div class="track-title" id="track-title">No Music Playing</div>
    <div class="requester" id="track-requester">Type /play &lt;song&gt; in Highrise room!</div>

    <div class="progress-bar-container">
      <div class="progress-bar" id="progress-bar"></div>
    </div>
    <div class="time-info">
      <span id="time-current">0:00</span>
      <span id="time-total">0:00</span>
    </div>

    <div style="display: flex; flex-direction: column; align-items: center; gap: 14px; width: 100%;">
      <button class="btn-sync" id="btn-audio-control" onclick="toggleRadio()">
        <span id="btn-icon">🔊</span> <span id="btn-text">Listen Live (Browser)</span>
      </button>
      <div style="display: flex; align-items: center; gap: 10px; font-size: 13px; color: #9ca3af;">
        <span>🔈</span>
        <input type="range" id="volume-slider" min="0" max="1" step="0.05" value="0.8" oninput="setVolume(this.value)" style="accent-color: #ec4899; cursor: pointer; width: 120px;">
        <span>🔊</span>
      </div>
    </div>
  </div>

  <div class="queue-card">
    <div class="queue-header" id="queue-header">Upcoming Queue (0)</div>
    <div id="queue-list">
      <p style="font-size: 12px; color: #6b7280; text-align: center; padding: 12px 0;">
        The queue is empty. Request songs in room chat using <b>/play &lt;song&gt;</b> or <b>/p &lt;song&gt;</b>!
      </p>
    </div>
  </div>

  <div class="instructions">
    📻 <b>24/7 Autonomous Radio:</b> The radio station runs 24/7 on the cloud and plays directly inside your Highrise room without needing any browser or PC running.<br>
    💡 In-room commands: <b>/play &lt;song&gt;</b> &bull; <b>/skip</b> &bull; <b>/queue</b> &bull; <b>/np</b>
  </div>

  <audio id="live-audio" src="/radio.mp3" preload="none"></audio>

  <script>
    let currentPosition = 0;
    let totalDuration = 180;
    let currentTrackId = null;

    function setVolume(val) {
      const audio = document.getElementById('live-audio');
      audio.volume = parseFloat(val);
    }

    function toggleRadio() {
      const audio = document.getElementById('live-audio');
      if (audio.paused) {
        audio.src = '/radio.mp3?t=' + Date.now();
        audio.play().then(() => {
          document.getElementById('btn-icon').innerText = '⏸️';
          document.getElementById('btn-text').innerText = 'Playing Live (In Sync)';
          document.getElementById('btn-audio-control').style.background = 'linear-gradient(135deg, #10b981 0%, #059669 100%)';
        }).catch(err => {
          console.error("Audio play error:", err);
        });
      } else {
        audio.pause();
        audio.removeAttribute('src');
        audio.load();
        document.getElementById('btn-icon').innerText = '🔊';
        document.getElementById('btn-text').innerText = 'Listen Live (Browser)';
        document.getElementById('btn-audio-control').style.background = 'linear-gradient(135deg, #7c3aed 0%, #ec4899 100%)';
      }
    }

    const liveAudioEl = document.getElementById('live-audio');
    liveAudioEl.addEventListener('error', () => {
      console.warn("Live audio connection lost, retrying...");
    });

    function formatTime(secs) {
      secs = Math.max(0, Math.floor(secs));
      const m = Math.floor(secs / 60);
      const s = secs % 60;
      return `${m}:${s < 10 ? '0' : ''}${s}`;
    }

    async function fetchState() {
      try {
        const res = await fetch('/api/player-state');
        const state = await res.json();
        renderState(state);
      } catch (err) {
        console.error("Failed to fetch state:", err);
      }
    }

    let isLoadingTrack = false;

    function renderState(state) {
      const now = state.now_playing;
      if (!now) {
        document.getElementById('track-title').innerText = "Radio Active - Queue Empty";
        document.getElementById('track-requester').innerText = "Type /play <song> in Highrise chat!";
        document.getElementById('track-art').src = "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=500";
        document.getElementById('progress-bar').style.width = '0%';
        document.getElementById('time-current').innerText = "0:00";
        document.getElementById('time-total').innerText = "0:00";
        currentTrackId = null;
        isLoadingTrack = false;
        renderQueue(state.queue);
        return;
      }

      currentTrackId = now.id;
      let titleHtml = now.title;
      if (now.artist) {
        titleHtml += `<div style="font-size:14px;color:#9ca3af;font-weight:600;margin-top:4px;">${now.artist}</div>`;
      }
      document.getElementById('track-title').innerHTML = titleHtml;
      if (now.is_autoplay) {
        document.getElementById('track-requester').innerHTML = '<span style="color:#f59e0b;font-weight:600;">📻 Radio AutoDJ</span> &bull; <span style="color:#9ca3af;">Type /play in chat to jump ahead!</span>';
      } else {
        document.getElementById('track-requester').innerText = "👤 Requested by @" + now.requester;
      }
      document.getElementById('track-art').src = now.thumbnail;
      totalDuration = now.duration_sec || 180;
      currentPosition = state.position_sec || 0;
      document.getElementById('time-total').innerText = now.duration_str;
      isLoadingTrack = Boolean(state.is_loading);

      if (isLoadingTrack) {
        document.getElementById('time-current').innerText = "Buffering...";
        document.getElementById('progress-bar').style.width = '0%';
      } else {
        const pct = Math.min(100, (currentPosition / totalDuration) * 100);
        document.getElementById('progress-bar').style.width = pct + '%';
        document.getElementById('time-current').innerText = formatTime(currentPosition);
      }
      renderQueue(state.queue, state.autoplay_enabled);
    }

    function updateProgressTick() {
      if (!currentTrackId || isLoadingTrack) return;
      currentPosition += 1;
      const pct = Math.min(100, (currentPosition / totalDuration) * 100);
      document.getElementById('progress-bar').style.width = pct + '%';
      document.getElementById('time-current').innerText = formatTime(currentPosition);
    }

    function renderQueue(queue, autoplayEnabled) {
      const listEl = document.getElementById('queue-list');
      document.getElementById('queue-header').innerText = `Upcoming Queue (${queue ? queue.length : 0})`;
      if (!queue || queue.length === 0) {
        const emptyText = autoplayEnabled
          ? 'Queue empty &bull; 📻 Station AutoDJ is active! Request a song using <b>/play &lt;song&gt;</b> to play next!'
          : 'The queue is empty. Request songs in room chat using <b>/play &lt;song&gt;</b> or <b>/p &lt;song&gt;</b>!';
        listEl.innerHTML = `<p style="font-size: 12px; color: #9ca3af; text-align: center; padding: 12px 0;">${emptyText}</p>`;
        return;
      }
      let html = '';
      queue.forEach((t, i) => {
        const readyBadge = t.is_cached
          ? '<span style="display:inline-block;background:rgba(16,185,129,0.2);color:#34d399;font-size:10px;font-weight:700;padding:2px 6px;border-radius:6px;margin-left:6px;vertical-align:middle;">⚡ READY</span>'
          : '';
        html += `
          <div class="queue-item">
            <img class="queue-thumb" src="${t.thumbnail}" alt="Thumbnail">
            <div class="queue-info">
              <div class="queue-title">#${i + 1} &bull; ${t.title} ${readyBadge}</div>
              <div class="queue-sub">${t.duration_str} &bull; Requested by @${t.requester}</div>
            </div>
          </div>
        `;
      });
      listEl.innerHTML = html;
    }

    fetchState();
    setInterval(fetchState, 2500);
    setInterval(updateProgressTick, 1000);
  </script>

  <div style="margin-top: 24px; text-align: center;">
    <a href="/portal" target="_blank" style="display: inline-flex; align-items: center; gap: 8px; background: rgba(139, 92, 246, 0.15); border: 1px solid rgba(139, 92, 246, 0.4); color: #c084fc; font-size: 13px; font-weight: 700; padding: 10px 20px; border-radius: 9999px; text-decoration: none; transition: all 0.2s ease;">
      <span>🚀 Want this 24/7 DJ & Floor Bot in your room? Launch yours here &rarr;</span>
    </a>
  </div>
</body>
</html>
"""


async def handle_player_page(request: web.Request) -> web.Response:
    """Serve the synchronized live jukebox player page."""
    return web.Response(
        text=PLAYER_HTML,
        content_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


async def handle_audio_current(request: web.Request) -> web.StreamResponse:
    """Stream directly for Highrise in-room radio player without 302 redirect."""
    return await handle_radio_stream(request)


async def handle_radio_stream(request: web.Request) -> web.StreamResponse:
    """Stream continuous live audio for Highrise Custom Radio."""
    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "audio/mpeg",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache, no-store, no-transform, must-revalidate",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Expires": "0",
            "icy-name": "Highrise Custom Room Radio",
            "icy-genre": "User Requested Music",
            "icy-br": "128",
            "icy-pub": "1",
            "ice-audio-info": "ice-samplerate=44100;ice-bitrate=128;ice-channels=2",
        },
    )
    await response.prepare(request)
    if request.method == "HEAD":
        return response

    q = broadcaster.subscribe()
    try:
        while True:
            chunk = await q.get()
            await response.write(chunk)
            await response.drain()
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    except Exception as e:
        logger.debug(f"Client disconnected from radio stream: {e}")
    finally:
        broadcaster.unsubscribe(q)

    return response


async def handle_debug_logs(request: web.Request) -> web.Response:
    """Return recent in-memory server logs for live troubleshooting."""
    text = "\n".join(recent_logs)
    return web.Response(text=text or "No logs recorded yet.", content_type="text/plain")


async def handle_debug_download(request: web.Request) -> web.Response:
    """Trigger a test audio download and return results/diagnostics."""
    vid = request.query.get("id", "JGwWNGJdvx8")
    title = request.query.get("title", "Test Track")
    query = request.query.get("q", request.query.get("query", ""))
    target_dur = int(request.query.get("dur", 0))
    from music import download_track_audio_sync
    try:
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, download_track_audio_sync, title, vid, query, target_dur)
        return web.json_response({
            "success": bool(res),
            "file_path": res,
            "exists": os.path.exists(res) if res else False,
            "size": os.path.getsize(res) if res and os.path.exists(res) else 0
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


PORTAL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ZevRay &bull; The Autonomous 24/7 Highrise DJ & Butler Platform</title>
  
  <!-- Premium Apple-style Typography -->
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=SF+Pro+Display:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  
  <!-- Supabase JS Client v2 -->
  <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>

  <style>
    :root {
      --bg-page: #fbfbfd;
      --bg-surface: #ffffff;
      --bg-secondary: #f5f5f7;
      --bg-tertiary: #eef0f4;
      --border-subtle: rgba(0, 0, 0, 0.08);
      --border-medium: rgba(0, 0, 0, 0.14);
      --text-primary: #1d1d1f;
      --text-secondary: #515154;
      --text-tertiary: #86868b;
      --accent-blue: #0071e3;
      --accent-blue-hover: #0077ed;
      --accent-indigo: #4f46e5;
      --accent-purple: #7c3aed;
      --accent-green: #10b981;
      --accent-amber: #f59e0b;
      --accent-red: #ef4444;
      --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.04);
      --shadow-md: 0 10px 30px -4px rgba(0, 0, 0, 0.06), 0 2px 6px -1px rgba(0, 0, 0, 0.02);
      --shadow-lg: 0 20px 45px -8px rgba(0, 0, 0, 0.09), 0 4px 12px -2px rgba(0, 0, 0, 0.03);
      --radius-sm: 10px;
      --radius-md: 16px;
      --radius-lg: 24px;
      --radius-full: 9999px;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    
    html {
      scroll-behavior: smooth;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }

    body {
      background-color: var(--bg-page);
      color: var(--text-primary);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Plus Jakarta Sans", "Inter", sans-serif;
      line-height: 1.5;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      overflow-x: hidden;
    }

    /* ---------------- HEADER & STICKY NAV ---------------- */
    .site-header {
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(251, 251, 253, 0.82);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border-bottom: 1px solid var(--border-subtle);
      transition: all 0.2s ease;
    }
    .header-inner {
      max-width: 1120px;
      margin: 0 auto;
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .brand-wrap {
      display: flex;
      align-items: center;
      gap: 10px;
      text-decoration: none;
      color: var(--text-primary);
    }
    .brand-icon {
      width: 32px;
      height: 32px;
      background: linear-gradient(135deg, #0071e3 0%, #7c3aed 100%);
      color: #fff;
      border-radius: 9px;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 4px 12px rgba(0, 113, 227, 0.25);
    }
    .brand-name {
      font-size: 19px;
      font-weight: 800;
      letter-spacing: -0.02em;
    }
    .brand-tag {
      font-size: 11px;
      background: var(--bg-tertiary);
      color: var(--text-secondary);
      font-weight: 600;
      padding: 2px 8px;
      border-radius: var(--radius-full);
      margin-left: 4px;
    }
    .nav-menu {
      display: flex;
      align-items: center;
      gap: 28px;
    }
    .nav-link {
      font-size: 13px;
      font-weight: 600;
      color: var(--text-secondary);
      text-decoration: none;
      transition: color 0.15s ease;
    }
    .nav-link:hover { color: var(--accent-blue); }
    .nav-actions {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    /* Modern Apple Buttons */
    .btn {
      font-family: inherit;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      font-size: 13px;
      font-weight: 600;
      padding: 8px 16px;
      border-radius: var(--radius-full);
      cursor: pointer;
      text-decoration: none;
      transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1);
      border: 1px solid transparent;
      outline: none;
      white-space: nowrap;
    }
    .btn:active { transform: scale(0.97); }
    .btn-secondary {
      background: var(--bg-surface);
      color: var(--text-primary);
      border-color: var(--border-medium);
      box-shadow: var(--shadow-sm);
    }
    .btn-secondary:hover {
      background: var(--bg-secondary);
      border-color: rgba(0, 0, 0, 0.2);
    }
    .btn-primary {
      background: var(--accent-blue);
      color: #ffffff;
      box-shadow: 0 4px 14px rgba(0, 113, 227, 0.28);
    }
    .btn-primary:hover {
      background: var(--accent-blue-hover);
      box-shadow: 0 6px 18px rgba(0, 113, 227, 0.36);
      transform: translateY(-1px);
    }
    .btn-cta-large {
      padding: 14px 28px;
      font-size: 16px;
      font-weight: 700;
      border-radius: var(--radius-full);
    }

    /* ---------------- HERO SECTION ---------------- */
    .hero-section {
      padding: 72px 24px 48px;
      text-align: center;
      max-width: 900px;
      margin: 0 auto;
    }
    .hero-badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: rgba(0, 113, 227, 0.08);
      border: 1px solid rgba(0, 113, 227, 0.2);
      color: var(--accent-blue);
      padding: 6px 16px;
      border-radius: var(--radius-full);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.02em;
      margin-bottom: 24px;
    }
    .hero-title {
      font-size: 54px;
      line-height: 1.08;
      font-weight: 800;
      letter-spacing: -0.035em;
      color: var(--text-primary);
      margin-bottom: 20px;
    }
    .hero-title-highlight {
      background: linear-gradient(135deg, #0071e3 0%, #7c3aed 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .hero-desc {
      font-size: 19px;
      line-height: 1.45;
      color: var(--text-secondary);
      max-width: 680px;
      margin: 0 auto 32px;
      font-weight: 400;
    }
    .hero-cta-group {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 14px;
      flex-wrap: wrap;
      margin-bottom: 48px;
    }

    /* ---------------- INTERACTIVE LIVE PREVIEW CARD ---------------- */
    .preview-card-wrap {
      max-width: 820px;
      margin: 0 auto 80px;
      padding: 0 16px;
    }
    .preview-card {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-lg);
      padding: 24px;
      box-shadow: var(--shadow-lg);
      display: flex;
      flex-direction: column;
      gap: 20px;
    }
    .preview-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border-subtle);
      padding-bottom: 16px;
    }
    .preview-live-indicator {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      font-weight: 700;
      color: var(--accent-green);
      background: rgba(16, 185, 129, 0.1);
      padding: 4px 12px;
      border-radius: var(--radius-full);
    }
    .live-dot {
      width: 8px;
      height: 8px;
      background: var(--accent-green);
      border-radius: 50%;
      animation: pulse 1.8s infinite;
    }
    @keyframes pulse {
      0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.6); }
      70% { box-shadow: 0 0 0 8px rgba(16, 185, 129, 0); }
      100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
    }
    .preview-player-body {
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 18px;
      align-items: center;
      background: var(--bg-secondary);
      border-radius: var(--radius-md);
      padding: 16px 20px;
    }
    .player-visualizer {
      display: flex;
      align-items: flex-end;
      gap: 3px;
      height: 32px;
      padding-bottom: 4px;
    }
    .visualizer-bar {
      width: 4px;
      background: var(--accent-blue);
      border-radius: 2px;
      animation: wave 1.2s ease-in-out infinite alternate;
    }
    .visualizer-bar:nth-child(1) { height: 16px; animation-delay: 0.1s; }
    .visualizer-bar:nth-child(2) { height: 28px; animation-delay: 0.3s; }
    .visualizer-bar:nth-child(3) { height: 10px; animation-delay: 0.2s; }
    .visualizer-bar:nth-child(4) { height: 24px; animation-delay: 0.4s; }
    .visualizer-bar:nth-child(5) { height: 18px; animation-delay: 0.15s; }
    @keyframes wave {
      0% { transform: scaleY(0.4); }
      100% { transform: scaleY(1); }
    }
    .player-meta {
      text-align: left;
    }
    .player-track-title {
      font-size: 15px;
      font-weight: 700;
      color: var(--text-primary);
    }
    .player-track-sub {
      font-size: 13px;
      color: var(--text-secondary);
    }
    .preview-commands-row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: center;
    }
    .command-tag {
      background: var(--bg-secondary);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-sm);
      padding: 6px 12px;
      font-family: ui-monospace, Menlo, Monaco, monospace;
      font-size: 12px;
      font-weight: 600;
      color: var(--accent-indigo);
    }

    /* ---------------- FEATURES PILLARS ---------------- */
    .section-wrap {
      max-width: 1120px;
      margin: 0 auto 96px;
      padding: 0 24px;
    }
    .section-header {
      text-align: center;
      max-width: 640px;
      margin: 0 auto 48px;
    }
    .section-subtitle {
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-weight: 800;
      color: var(--accent-blue);
      margin-bottom: 8px;
    }
    .section-title {
      font-size: 36px;
      font-weight: 800;
      letter-spacing: -0.025em;
      color: var(--text-primary);
      line-height: 1.16;
    }
    .features-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 24px;
    }
    @media (max-width: 768px) {
      .features-grid { grid-template-columns: 1fr; }
      .hero-title { font-size: 38px; }
      .nav-menu { display: none; }
    }
    .feature-card {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-lg);
      padding: 32px;
      box-shadow: var(--shadow-sm);
      transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .feature-card:hover {
      box-shadow: var(--shadow-md);
      transform: translateY(-2px);
      border-color: rgba(0, 0, 0, 0.12);
    }
    .feature-icon-box {
      width: 48px;
      height: 48px;
      border-radius: 14px;
      background: var(--bg-secondary);
      color: var(--accent-blue);
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .feature-card-title {
      font-size: 20px;
      font-weight: 800;
      letter-spacing: -0.015em;
    }
    .feature-card-desc {
      font-size: 14px;
      color: var(--text-secondary);
      line-height: 1.6;
    }

    /* ---------------- DEPLOYER CORE FORM (APPLE CARD) ---------------- */
    .deploy-section {
      max-width: 760px;
      margin: 0 auto 96px;
      padding: 0 20px;
    }
    .deploy-card {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-lg);
      padding: 36px 40px;
      box-shadow: var(--shadow-lg);
    }
    @media (max-width: 600px) {
      .deploy-card { padding: 24px 20px; }
    }
    .deploy-header {
      text-align: center;
      margin-bottom: 32px;
    }
    .deploy-title {
      font-size: 26px;
      font-weight: 800;
      letter-spacing: -0.02em;
      margin-bottom: 8px;
    }
    .deploy-subtitle {
      font-size: 14px;
      color: var(--text-secondary);
    }

    /* Interactive Form Groups & Tutorials */
    .form-step {
      margin-bottom: 28px;
      border-bottom: 1px solid var(--border-subtle);
      padding-bottom: 24px;
    }
    .form-step:last-of-type {
      border-bottom: none;
      padding-bottom: 12px;
    }
    .step-label-row {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      margin-bottom: 8px;
    }
    .field-label {
      font-size: 14px;
      font-weight: 700;
      color: var(--text-primary);
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .badge-required {
      font-size: 10px;
      font-weight: 700;
      color: var(--accent-blue);
      background: rgba(0, 113, 227, 0.08);
      padding: 2px 6px;
      border-radius: 4px;
      text-transform: uppercase;
    }
    .badge-optional {
      font-size: 10px;
      font-weight: 700;
      color: var(--text-tertiary);
      background: var(--bg-secondary);
      padding: 2px 6px;
      border-radius: 4px;
      text-transform: uppercase;
    }

    /* Input wrapper with Hide/Reveal Icon */
    .input-wrapper {
      position: relative;
      display: flex;
      align-items: center;
    }
    .app-input {
      width: 100%;
      background: var(--bg-surface);
      border: 1px solid var(--border-medium);
      border-radius: var(--radius-md);
      padding: 13px 44px 13px 14px;
      font-family: inherit;
      font-size: 14px;
      color: var(--text-primary);
      outline: none;
      transition: all 0.15s ease;
      box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.02);
    }
    .app-input:focus {
      border-color: var(--accent-blue);
      box-shadow: 0 0 0 4px rgba(0, 113, 227, 0.15);
    }
    .app-input.no-reveal {
      padding-right: 14px;
    }
    .btn-reveal-toggle {
      position: absolute;
      right: 12px;
      background: none;
      border: none;
      color: var(--text-tertiary);
      cursor: pointer;
      padding: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 6px;
      transition: color 0.15s ease;
    }
    .btn-reveal-toggle:hover { color: var(--text-primary); }

    /* Interactive Tutorial Box */
    .tutorial-box {
      margin-top: 10px;
      background: var(--bg-secondary);
      border: 1px solid rgba(0, 0, 0, 0.05);
      border-radius: var(--radius-md);
      padding: 14px 16px;
    }
    .tutorial-header {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      font-weight: 700;
      color: var(--accent-blue);
      margin-bottom: 6px;
    }
    .tutorial-text {
      font-size: 12px;
      line-height: 1.55;
      color: var(--text-secondary);
    }
    .tutorial-text a {
      color: var(--accent-blue);
      text-decoration: underline;
      font-weight: 600;
    }
    .example-code {
      display: inline-block;
      margin-top: 6px;
      font-family: ui-monospace, Menlo, Monaco, monospace;
      font-size: 11px;
      background: #ffffff;
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 4px 8px;
      color: #b91c1c;
      word-break: break-all;
    }

    /* Save preferences switch */
    .pref-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 14px 0;
      margin-bottom: 16px;
    }
    .pref-label-wrap {
      display: flex;
      flex-direction: column;
    }
    .pref-title {
      font-size: 13px;
      font-weight: 700;
      color: var(--text-primary);
    }
    .pref-sub {
      font-size: 11px;
      color: var(--text-tertiary);
    }
    .switch {
      position: relative;
      display: inline-block;
      width: 44px;
      height: 24px;
    }
    .switch input { opacity: 0; width: 0; height: 0; }
    .slider {
      position: absolute;
      cursor: pointer;
      inset: 0;
      background-color: #e5e5ea;
      transition: .2s;
      border-radius: 24px;
    }
    .slider:before {
      position: absolute;
      content: "";
      height: 20px;
      width: 20px;
      left: 2px;
      bottom: 2px;
      background-color: white;
      transition: .2s;
      border-radius: 50%;
      box-shadow: 0 2px 4px rgba(0,0,0,0.2);
    }
    input:checked + .slider {
      background-color: var(--accent-green);
    }
    input:checked + .slider:before {
      transform: translateX(20px);
    }

    /* Submit & Status */
    .btn-submit-deploy {
      width: 100%;
      padding: 16px;
      font-size: 16px;
      font-weight: 700;
      background: linear-gradient(135deg, #0071e3 0%, #7c3aed 100%);
      color: #fff;
      border: none;
      border-radius: var(--radius-full);
      box-shadow: 0 8px 24px rgba(0, 113, 227, 0.3);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
    }
    .btn-submit-deploy:hover {
      box-shadow: 0 12px 28px rgba(0, 113, 227, 0.4);
      transform: translateY(-1px);
    }
    .btn-submit-deploy:disabled {
      opacity: 0.6;
      cursor: not-allowed;
      transform: none;
    }

    /* Status and Error banners */
    .status-alert {
      display: none;
      margin-top: 20px;
      padding: 14px 18px;
      border-radius: var(--radius-md);
      font-size: 13px;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .status-loading {
      background: rgba(0, 113, 227, 0.08);
      border: 1px solid rgba(0, 113, 227, 0.2);
      color: var(--accent-blue);
    }
    .status-error {
      background: rgba(239, 68, 68, 0.08);
      border: 1px solid rgba(239, 68, 68, 0.2);
      color: #dc2626;
    }

    /* ---------------- SUCCESS RESULT CARD ---------------- */
    .result-card {
      display: none;
      margin-top: 32px;
      background: var(--bg-surface);
      border: 2px solid rgba(16, 185, 129, 0.3);
      border-radius: var(--radius-lg);
      padding: 28px;
      box-shadow: var(--shadow-lg);
    }
    .result-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(16, 185, 129, 0.1);
      color: var(--accent-green);
      font-size: 12px;
      font-weight: 700;
      padding: 4px 12px;
      border-radius: var(--radius-full);
      margin-bottom: 12px;
    }
    .result-title {
      font-size: 22px;
      font-weight: 800;
      letter-spacing: -0.02em;
      margin-bottom: 8px;
    }
    .result-desc {
      font-size: 13px;
      color: var(--text-secondary);
      margin-bottom: 24px;
    }

    /* Prominent MP3 Stream Container */
    .radio-stream-box {
      background: linear-gradient(135deg, rgba(0, 113, 227, 0.04) 0%, rgba(124, 58, 237, 0.05) 100%);
      border: 1.5px solid rgba(0, 113, 227, 0.2);
      border-radius: var(--radius-md);
      padding: 20px;
      margin-bottom: 20px;
    }
    .radio-stream-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 6px;
    }
    .radio-stream-label {
      font-size: 14px;
      font-weight: 800;
      color: var(--text-primary);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .stream-input-group {
      display: flex;
      gap: 8px;
      margin-top: 10px;
    }
    .stream-url-field {
      flex: 1;
      background: #ffffff;
      border: 1px solid var(--border-medium);
      border-radius: var(--radius-sm);
      padding: 10px 14px;
      font-family: ui-monospace, Menlo, Monaco, monospace;
      font-size: 13px;
      font-weight: 600;
      color: var(--accent-blue);
      outline: none;
    }

    /* ---------------- AUTH MODAL (NATIVE DIALOG COMPLIANT) ---------------- */
    dialog:modal {
      border: none;
      padding: 0;
      background: transparent;
      max-width: 420px;
      width: calc(100% - 32px);
      margin: auto;
      overscroll-behavior: contain;
    }
    dialog::backdrop {
      background: rgba(0, 0, 0, 0.35);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      overflow: hidden;
      overscroll-behavior: contain;
    }
    .auth-dialog-card {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-lg);
      padding: 32px;
      box-shadow: var(--shadow-lg);
    }
    .auth-tabs {
      display: flex;
      background: var(--bg-secondary);
      border-radius: var(--radius-full);
      padding: 4px;
      margin-bottom: 24px;
    }
    .auth-tab-btn {
      flex: 1;
      border: none;
      background: none;
      padding: 8px 16px;
      border-radius: var(--radius-full);
      font-size: 13px;
      font-weight: 700;
      color: var(--text-secondary);
      cursor: pointer;
      font-family: inherit;
      transition: all 0.15s ease;
    }
    .auth-tab-btn.active {
      background: #ffffff;
      color: var(--text-primary);
      box-shadow: var(--shadow-sm);
    }
    .auth-dialog-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 18px;
    }
    .auth-close-btn {
      background: none;
      border: none;
      color: var(--text-tertiary);
      cursor: pointer;
      padding: 4px;
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .auth-close-btn:hover { color: var(--text-primary); }

    /* ---------------- USER BOT FLEET (DASHBOARD) ---------------- */
    .dashboard-panel {
      display: none;
      max-width: 760px;
      margin: 0 auto 48px;
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-lg);
      padding: 28px 32px;
      box-shadow: var(--shadow-md);
    }
    .dash-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--border-subtle);
    }
    .dash-title {
      font-size: 18px;
      font-weight: 800;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .bot-card-item {
      background: var(--bg-secondary);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 16px 20px;
      margin-bottom: 12px;
    }
    .bot-card-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 6px;
    }
    .bot-card-name {
      font-size: 15px;
      font-weight: 700;
      color: var(--text-primary);
    }
    .bot-card-links {
      display: flex;
      gap: 8px;
      margin-top: 10px;
      flex-wrap: wrap;
    }

    /* ---------------- FOOTER ---------------- */
    .site-footer {
      margin-top: auto;
      border-top: 1px solid var(--border-subtle);
      background: var(--bg-secondary);
      padding: 48px 24px;
    }
    .footer-inner {
      max-width: 1120px;
      margin: 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 20px;
      font-size: 13px;
      color: var(--text-tertiary);
    }
    .footer-links {
      display: flex;
      gap: 24px;
    }
    .footer-links a {
      color: var(--text-secondary);
      text-decoration: none;
      font-weight: 500;
      transition: color 0.15s ease;
    }
    .footer-links a:hover { color: var(--accent-blue); }

    .spinner {
      width: 16px;
      height: 16px;
      border: 2px solid rgba(255, 255, 255, 0.4);
      border-radius: 50%;
      border-top-color: #fff;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>

  <!-- ---------------- NAVIGATION BAR ---------------- -->
  <header class="site-header">
    <div class="header-inner">
      <a href="#" class="brand-wrap">
        <div class="brand-icon">
          <!-- Vector Bolt Icon -->
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
          </svg>
        </div>
        <span class="brand-name">ZevRay</span>
        <span class="brand-tag">Highrise Cloud</span>
      </a>

      <nav class="nav-menu">
        <a href="#features" class="nav-link">Features</a>
        <a href="#deploy" class="nav-link">Deployer</a>
        <a href="/player" target="_blank" class="nav-link">Live Jukebox</a>
        <a href="#faq" class="nav-link">FAQ</a>
      </nav>

      <div class="nav-actions">
        <!-- Logged Out State -->
        <div id="nav-logged-out" style="display: flex; gap: 8px;">
          <button class="btn btn-secondary" onclick="openAuthDialog('login')">Sign In</button>
          <button class="btn btn-primary" onclick="openAuthDialog('signup')">Create Account</button>
        </div>

        <!-- Logged In State -->
        <div id="nav-logged-in" style="display: none; align-items: center; gap: 8px;">
          <button class="btn btn-secondary" onclick="toggleUserDashboard()">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>
            </svg>
            <span>My Bots (<span id="user-bots-badge">0</span>)</span>
          </button>
          <button class="btn btn-secondary" onclick="handleSignOut()" title="Sign Out">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>
            </svg>
          </button>
        </div>
      </div>
    </div>
  </header>

  <!-- ---------------- USER FLEET DASHBOARD ---------------- -->
  <section class="dashboard-panel" id="user-dashboard-panel">
    <div class="dash-top">
      <div class="dash-title">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent-blue)">
          <rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 8h6"/><path d="M9 12h6"/><path d="M9 16h6"/>
        </svg>
        <span>Your Active Bot Fleet</span>
      </div>
      <button class="btn btn-secondary" onclick="fetchUserBots()">Refresh</button>
    </div>
    <div id="user-bots-list">
      <p style="font-size: 13px; color: var(--text-tertiary); text-align: center; padding: 20px 0;">Loading your deployed bots...</p>
    </div>
  </section>

  <!-- ---------------- HERO ---------------- -->
  <main>
    <section class="hero-section">
      <div class="hero-badge">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
        </svg>
        <span>The Autonomous Highrise Butler Engine</span>
      </div>

      <h1 class="hero-title">
        The 24/7 AI DJ & Butler for <br>
        <span class="hero-title-highlight">Your Highrise Lounge.</span>
      </h1>

      <p class="hero-desc">
        Effortless in-room live music streams, instant multi-floor teleportation, permanent outfit memory, and live tip leaderboards. Deployed directly to the cloud in under 60 seconds.
      </p>

      <div class="hero-cta-group">
        <a href="#deploy" class="btn btn-primary btn-cta-large">
          <span>Deploy Your Bot in 60s</span>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>
          </svg>
        </a>
        <a href="/player" target="_blank" class="btn btn-secondary btn-cta-large">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="5 3 19 12 5 21 5 3"/>
          </svg>
          <span>Live Music Demo</span>
        </a>
      </div>
    </section>

    <!-- ---------------- INTERACTIVE LIVE PREVIEW ---------------- -->
    <div class="preview-card-wrap">
      <div class="preview-card">
        <div class="preview-header">
          <div style="text-align: left;">
            <div style="font-size: 14px; font-weight: 800; color: var(--text-primary);">Simulated Room Experience</div>
            <div style="font-size: 12px; color: var(--text-tertiary);">Autonomous Bot Status &bull; Connected 24/7</div>
          </div>
          <div class="preview-live-indicator">
            <span class="live-dot"></span>
            <span>Online & Broadcasting</span>
          </div>
        </div>

        <div class="preview-player-body">
          <div class="player-visualizer">
            <div class="visualizer-bar"></div>
            <div class="visualizer-bar"></div>
            <div class="visualizer-bar"></div>
            <div class="visualizer-bar"></div>
            <div class="visualizer-bar"></div>
          </div>
          <div class="player-meta">
            <div class="player-track-title">24/7 Highrise Autonomous Radio</div>
            <div class="player-track-sub">Zero-latency synchronous stream &bull; Highrise Room Audio Ready</div>
          </div>
          <a href="/player" target="_blank" class="btn btn-secondary" style="font-size: 12px; padding: 6px 12px;">Open Player</a>
        </div>

        <div class="preview-commands-row">
          <span class="command-tag">/play &lt;song&gt;</span>
          <span class="command-tag">/skip</span>
          <span class="command-tag">/f1 ... /f4</span>
          <span class="command-tag">!fit</span>
          <span class="command-tag">!setpos</span>
          <span class="command-tag">!setwelcome</span>
        </div>
      </div>
    </div>

    <!-- ---------------- 4 PILLARS FEATURES ---------------- -->
    <section class="section-wrap" id="features">
      <div class="section-header">
        <div class="section-subtitle">Engineered with Precision</div>
        <h2 class="section-title">Everything your Highrise lounge needs to run completely on autopilot.</h2>
      </div>

      <div class="features-grid">
        <!-- Feature 1 -->
        <div class="feature-card">
          <div class="feature-icon-box">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
            </svg>
          </div>
          <h3 class="feature-card-title">24/7 Radio & Music Takeover</h3>
          <p class="feature-card-desc">
            Plays music directly through your in-game room speakers. Guests type <code>/play &lt;song&gt;</code> in chat to instantly queue their favorite tracks, while the bot automatically falls back to curated tunes when the queue clears.
          </p>
        </div>

        <!-- Feature 2 -->
        <div class="feature-card">
          <div class="feature-icon-box">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="4" y="2" width="16" height="20" rx="2" ry="2"/><line x1="9" y1="22" x2="9" y2="22.01"/><line x1="15" y1="22" x2="15" y2="22.01"/><line x1="8" y1="6" x2="8.01" y2="6"/><line x1="12" y1="6" x2="12.01" y2="6"/><line x1="16" y1="6" x2="16.01" y2="6"/><line x1="8" y1="10" x2="8.01" y2="10"/><line x1="12" y1="10" x2="12.01" y2="10"/><line x1="16" y1="10" x2="16.01" y2="10"/>
            </svg>
          </div>
          <h3 class="feature-card-title">Dynamic Multi-Floor Elevator</h3>
          <p class="feature-card-desc">
            Whether your venue has 2 floors or 5 floors, stand where you want each landing spot to be and type <code>!setfloor f1</code>, <code>!setfloor f2</code>, etc. Guests can teleport smoothly across levels simply by typing <code>/f1</code> to <code>/f4</code>.
          </p>
        </div>

        <!-- Feature 3 -->
        <div class="feature-card">
          <div class="feature-icon-box">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.45 1-1 1H8c-.55 0-1 .45-1 1v1h10v-1c0-.55-.45-1-1-1h-1c-.55 0-1-.45-1-1v-2.34"/><path d="M6 4h12v7a6 6 0 0 1-12 0V4Z"/>
            </svg>
          </div>
          <h3 class="feature-card-title">Live Tip Leaderboard</h3>
          <p class="feature-card-desc">
            Celebrate your room's supporters in real time. The bot tracks every tip, plays custom celebration sound bites, crowns your Top Tipper, and broadcasts celebratory whispers to inspire your community.
          </p>
        </div>

        <!-- Feature 4 -->
        <div class="feature-card">
          <div class="feature-icon-box">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
            </svg>
          </div>
          <h3 class="feature-card-title">Permanent Outfits & State Memory</h3>
          <p class="feature-card-desc">
            Backed by cloud persistence, your bot never forgets its wardrobe, DJ booth anchor, custom welcome messages, or floors when the cloud container updates or restarts. Simply wear an outfit and type <code>!fit</code> to lock it forever.
          </p>
        </div>
      </div>
    </section>

    <!-- ---------------- CLOUD DEPLOYER INTERACTIVE SECTION ---------------- -->
    <section class="deploy-section" id="deploy">
      <div class="deploy-card">
        <div class="deploy-header">
          <div class="section-subtitle">60-Second Setup</div>
          <h2 class="deploy-title">Launch Your 24/7 Cloud Bot</h2>
          <p class="deploy-subtitle">Deploy directly to your free Render cloud account. Follow each step below with our simple, playful tutorials.</p>
        </div>

        <form id="deployer-form">
          <!-- Step 1: Highrise Bot Token -->
          <div class="form-step">
            <div class="step-label-row">
              <label class="field-label" for="highrise-token">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent-blue)">
                  <rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                </svg>
                <span>Highrise Bot API Token</span>
              </label>
              <span class="badge-required">Required</span>
            </div>
            
            <div class="input-wrapper">
              <input type="password" id="highrise-token" class="app-input" placeholder="e.g. 20d06551e88098d0b7c6..." required autocomplete="off">
              <button type="button" class="btn-reveal-toggle" onclick="toggleInputVisibility('highrise-token', this)" aria-label="Toggle password visibility">
                <!-- Eye Icon -->
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
              </button>
            </div>

            <!-- Tutorial Box for Token -->
            <div class="tutorial-box">
              <div class="tutorial-header">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
                <span>How to grab your Bot Token in 3 clicks:</span>
              </div>
              <div class="tutorial-text">
                1. Visit the official portal <a href="https://create.highrise.game" target="_blank">create.highrise.game</a> and sign in with your Highrise account.<br>
                2. In the left menu, click <b>Bots & API Keys</b> &rarr; tap <b>Create Bot</b>.<br>
                3. Name your bot (e.g. <i>NeonDJ</i>) and copy the generated 64-character token.<br>
                <span class="example-code">Example format: 20d06551e88098d0b7c6a084de450abf...</span>
              </div>
            </div>
          </div>

          <!-- Step 2: Room ID or URL -->
          <div class="form-step">
            <div class="step-label-row">
              <label class="field-label" for="room-id">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent-blue)">
                  <path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>
                </svg>
                <span>Highrise Room ID or Share Link</span>
              </label>
              <span class="badge-required">Required</span>
            </div>

            <div class="input-wrapper">
              <input type="text" id="room-id" class="app-input no-reveal" placeholder="e.g. 64005ecfb3d3644258e0040e or paste Room Link" required>
            </div>

            <!-- Tutorial Box for Room -->
            <div class="tutorial-box">
              <div class="tutorial-header">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
                <span>Super easy: Just paste your room share link!</span>
              </div>
              <div class="tutorial-text">
                In the Highrise app, walk into your room &rarr; tap the top Room Info &rarr; tap <b>Share Room</b> &rarr; <b>Copy Link</b>.<br>
                Paste the entire link right here. Our parser automatically extracts the 24-character hexadecimal room ID for you!<br>
                <span class="example-code">Example: https://highrise.game/room/64005ecfb3d3644258e0040e &rarr; 64005ecfb3d3644258e0040e</span>
              </div>
            </div>
          </div>

          <!-- Step 3: Room Owner Username -->
          <div class="form-step">
            <div class="step-label-row">
              <label class="field-label" for="room-owner">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent-blue)">
                  <circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 0 0-16 0"/>
                </svg>
                <span>Your Highrise Username (Bot Administrator)</span>
              </label>
              <span class="badge-required">Required</span>
            </div>

            <div class="input-wrapper">
              <input type="text" id="room-owner" class="app-input no-reveal" placeholder="e.g. Sarah (without @)" required>
            </div>

            <!-- Tutorial Box for Owner -->
            <div class="tutorial-box">
              <div class="tutorial-header">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
                <span>Why this is crucial:</span>
              </div>
              <div class="tutorial-text">
                This grants you Master Admin privileges inside your room. Only you will be able to lock the DJ booth location (<code>!setpos</code>), change outfits (<code>!fit</code>), and set floors (<code>!setfloor</code>).<br>
                <span class="example-code">Example: Sarah or JayShah</span>
              </div>
            </div>
          </div>

          <!-- Step 4: Room Display Title -->
          <div class="form-step">
            <div class="step-label-row">
              <label class="field-label" for="room-title">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent-blue)">
                  <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5Z"/>
                </svg>
                <span>Venue Display Name</span>
              </label>
              <span class="badge-optional">Optional</span>
            </div>

            <div class="input-wrapper">
              <input type="text" id="room-title" class="app-input no-reveal" placeholder="e.g. Neon Rooftop or Chill Lounge" value="My Lounge">
            </div>

            <!-- Tutorial Box for Room Name -->
            <div class="tutorial-box">
              <div class="tutorial-header">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
                <span>Playful room customization:</span>
              </div>
              <div class="tutorial-text">
                Your bot uses this name in automated welcome whispers when players step into your room! (e.g. <i>"Welcome to Neon Rooftop, @player! Enjoy the music!"</i>).
              </div>
            </div>
          </div>

          <!-- Step 5: Render API Key -->
          <div class="form-step">
            <div class="step-label-row">
              <label class="field-label" for="render-key">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent-blue)">
                  <rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>
                </svg>
                <span>Render.com API Key</span>
              </label>
              <span class="badge-required">Required</span>
            </div>

            <div class="input-wrapper">
              <input type="password" id="render-key" class="app-input" placeholder="e.g. rnd_xxxxxxxxxxxxxxxxxxxx" required autocomplete="off">
              <button type="button" class="btn-reveal-toggle" onclick="toggleInputVisibility('render-key', this)" aria-label="Toggle Render key visibility">
                <!-- Eye Icon -->
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
              </button>
            </div>

            <!-- Tutorial Box for Render -->
            <div class="tutorial-box">
              <div class="tutorial-header">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
                <span>How to get your free Render API key:</span>
              </div>
              <div class="tutorial-text">
                1. Sign up or log into <a href="https://dashboard.render.com" target="_blank">dashboard.render.com</a> (100% free, zero credit card needed).<br>
                2. Click your avatar icon (bottom-left) &rarr; <b>Account Settings</b>.<br>
                3. Click <b>API Keys</b> &rarr; <b>Create API Key</b>.<br>
                4. Copy the key starting with <code>rnd_</code> and paste it above.<br>
                <span class="example-code">Example format: rnd_3a4b5c6d7e8f90123456789...</span>
              </div>
            </div>
          </div>

          <!-- Step 6: 24/7 Keep-Alive & Auto-Pinger -->
          <div class="form-step">
            <div class="step-label-row">
              <label class="field-label" for="cron-key">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent-blue)">
                  <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
                </svg>
                <span>cron-job.org API Key</span>
              </label>
              <span class="badge-optional">Optional</span>
            </div>

            <div class="input-wrapper">
              <input type="password" id="cron-key" class="app-input" placeholder="e.g. crons_xxxx... (leave blank for Zero-Setup Auto-Pinger)" autocomplete="off">
              <button type="button" class="btn-reveal-toggle" onclick="toggleInputVisibility('cron-key', this)" aria-label="Toggle cron key visibility">
                <!-- Eye Icon -->
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
              </button>
            </div>

            <!-- Tutorial Box for 24/7 keep alive -->
            <div class="tutorial-box">
              <div class="tutorial-header">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
                <span>Zero-setup 24/7 keep-alive:</span>
              </div>
              <div class="tutorial-text">
                Leave this blank! Our Master Keep-Alive network automatically enrolls your bot and pings it every 10 minutes so Render never sleeps. If you have your own personal cron-job.org key, you can optionally paste it here.
              </div>
            </div>
          </div>

          <!-- Save Preferences Switch -->
          <div class="pref-row">
            <div class="pref-label-wrap">
              <span class="pref-title">Remember details in this browser</span>
              <span class="pref-sub">Securely saves your non-sensitive tokens locally so you can redeploy with one click.</span>
            </div>
            <label class="switch">
              <input type="checkbox" id="save-local-pref" checked>
              <span class="slider"></span>
            </label>
          </div>

          <!-- Submit Button -->
          <button type="submit" id="btn-deploy-submit" class="btn-submit-deploy">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
            </svg>
            <span id="deploy-btn-text">Launch Autonomous Bot in Cloud</span>
          </button>
        </form>

        <!-- Loading State Indicator -->
        <div id="deploy-loading" class="status-alert status-loading" style="display: none;">
          <div class="spinner" style="border-color: rgba(0, 113, 227, 0.3); border-top-color: var(--accent-blue);"></div>
          <span id="deploy-loading-text">Connecting to Render cloud & provisioning container...</span>
        </div>

        <!-- Error Banner -->
        <div id="deploy-error" class="status-alert status-error" style="display: none;"></div>

        <!-- ---------------- RESULT CARD ---------------- -->
        <div id="deploy-result" class="result-card">
          <div class="result-badge">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
            <span>Cloud Service Created Successfully!</span>
          </div>

          <h3 class="result-title">Your 24/7 DJ Bot is Building</h3>
          <p class="result-desc">Render is compiling your bot. It will join your room automatically in approximately 60-90 seconds.</p>

          <!-- MP3 Stream URL Box -->
          <div class="radio-stream-box">
            <div class="radio-stream-header">
              <span class="radio-stream-label">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent-blue)">
                  <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
                </svg>
                <span>Highrise In-Room Custom Radio (.mp3 Stream)</span>
              </span>
              <span style="font-size: 11px; background: rgba(0, 113, 227, 0.1); color: var(--accent-blue); padding: 2px 8px; border-radius: 6px; font-weight: 700;">Paste into Room Settings</span>
            </div>
            <p style="font-size: 12px; color: var(--text-secondary); line-height: 1.5; margin-bottom: 8px;">
              Paste this URL into Highrise <b>Room Settings &rarr; Custom Radio Station</b> so that music plays through your room speakers for everyone:
            </p>
            <div class="stream-input-group">
              <input type="text" id="res-radio-field" class="stream-url-field" readonly value="" placeholder="https://...onrender.com/radio.mp3">
              <button type="button" class="btn btn-primary" id="btn-copy-radio" onclick="copyStreamUrl()">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>
                </svg>
                <span id="copy-radio-text">Copy .mp3 Link</span>
              </button>
            </div>
          </div>

          <!-- Secondary Links -->
          <div style="display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--border-subtle); font-size: 13px;">
            <span style="font-weight: 600; color: var(--text-secondary);">Web Jukebox Dashboard:</span>
            <a id="res-player-link" href="#" target="_blank" style="color: var(--accent-blue); font-weight: 700; text-decoration: none;">Open /player &rarr;</a>
          </div>

          <div style="display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--border-subtle); font-size: 13px;">
            <span style="font-weight: 600; color: var(--text-secondary);">24/7 Keep-Alive:</span>
            <span id="res-cron-badge" style="font-weight: 700; color: var(--accent-green);">Auto-Pinger Active (10m)</span>
          </div>

          <div style="margin-top: 20px; background: var(--bg-secondary); border-radius: var(--radius-md); padding: 16px;">
            <h4 style="font-size: 13px; font-weight: 800; margin-bottom: 8px;">Next: In-Room 30-Second Checklist</h4>
            <ol style="margin-left: 20px; font-size: 12px; color: var(--text-secondary); line-height: 1.6;">
              <li>Invite your bot into your Highrise room.</li>
              <li>Go to <b>Room Settings &rarr; Privileges</b> and give your bot <b>Designer</b> rights (needed to teleport players).</li>
              <li>Paste your copied <b>.mp3 Stream URL</b> into <b>Room Settings &rarr; Custom Radio Station</b> and hit Save!</li>
              <li>Walk into your DJ booth and type <code>!setpos</code> in chat.</li>
            </ol>
          </div>
        </div>
      </div>
    </section>

    <!-- ---------------- FAQ SECTION ---------------- -->
    <section class="section-wrap" id="faq">
      <div class="section-header">
        <div class="section-subtitle">Got Questions?</div>
        <h2 class="section-title">Frequently Asked Questions</h2>
      </div>

      <div style="max-width: 760px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px;">
        <div style="background: var(--bg-surface); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: 20px 24px;">
          <h3 style="font-size: 16px; font-weight: 700; margin-bottom: 8px;">Does my PC or phone need to stay on?</h3>
          <p style="font-size: 14px; color: var(--text-secondary); line-height: 1.6;">
            No! Your bot runs 100% inside a high-speed cloud container on Render. You can turn off your computer, close your phone, or go to sleep — your bot stays online playing music and greeting visitors 24/7.
          </p>
        </div>

        <div style="background: var(--bg-surface); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: 20px 24px;">
          <h3 style="font-size: 16px; font-weight: 700; margin-bottom: 8px;">How does in-room audio work?</h3>
          <p style="font-size: 14px; color: var(--text-secondary); line-height: 1.6;">
            When deployed, our platform gives you a direct synchronous <code>/radio.mp3</code> audio stream link. Highrise has a built-in Custom Radio feature in Room Settings &mdash; paste the link there, and all players in the room will hear the music simultaneously!
          </p>
        </div>

        <div style="background: var(--bg-surface); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: 20px 24px;">
          <h3 style="font-size: 16px; font-weight: 700; margin-bottom: 8px;">Can anyone else change my bot's settings?</h3>
          <p style="font-size: 14px; color: var(--text-secondary); line-height: 1.6;">
            Never. When you specify your Highrise username, you are locked in as the Master Administrator. Admin commands like <code>!setpos</code>, <code>!setfloor</code>, <code>!fit</code>, and <code>!setwelcome</code> strictly respond only to you.
          </p>
        </div>

        <div style="background: var(--bg-surface); border: 1px solid var(--border-subtle); border-radius: var(--radius-md); padding: 20px 24px;">
          <h3 style="font-size: 16px; font-weight: 700; margin-bottom: 8px;">Is Render really free?</h3>
          <p style="font-size: 14px; color: var(--text-secondary); line-height: 1.6;">
            Yes. Render provides 750 free web service hours each month, which covers a full 24/7 month with zero charges. No credit card is required to sign up.
          </p>
        </div>
      </div>
    </section>
  </main>

  <!-- ---------------- PLAYFUL AUTH DIALOG (NATIVE <DIALOG>) ---------------- -->
  <dialog id="auth-dialog" closedby="any" aria-labelledby="auth-dialog-title">
    <div class="auth-dialog-card">
      <div class="auth-dialog-header">
        <div class="brand-wrap">
          <div class="brand-icon" style="width:26px; height:26px;">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
          </div>
          <span style="font-size: 16px; font-weight: 800;" id="auth-dialog-title">ZevRay Account</span>
        </div>
        <button type="button" class="auth-close-btn" onclick="closeAuthDialog()" aria-label="Close dialog">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>

      <div class="auth-tabs">
        <button type="button" class="auth-tab-btn active" id="btn-tab-login" onclick="switchAuthTab('login')">Sign In</button>
        <button type="button" class="auth-tab-btn" id="btn-tab-signup" onclick="switchAuthTab('signup')">Create Account</button>
      </div>

      <form id="auth-form" onsubmit="handleAuthSubmit(event)">
        <div style="margin-bottom: 16px;">
          <label style="display:block; font-size:13px; font-weight:700; margin-bottom:6px;" for="auth-email">Email Address</label>
          <input type="email" id="auth-email" class="app-input no-reveal" placeholder="you@example.com" required>
        </div>

        <div style="margin-bottom: 16px;">
          <label style="display:block; font-size:13px; font-weight:700; margin-bottom:6px;" for="auth-password">Password</label>
          <div class="input-wrapper">
            <input type="password" id="auth-password" class="app-input" placeholder="••••••••" required minlength="6">
            <button type="button" class="btn-reveal-toggle" onclick="toggleInputVisibility('auth-password', this)" aria-label="Toggle password visibility">
              <!-- Eye Icon -->
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </div>
        </div>

        <div id="signup-extra-fields" style="display: none; margin-bottom: 16px;">
          <label style="display:block; font-size:13px; font-weight:700; margin-bottom:6px;" for="auth-highrise-user">
            Highrise Username <span style="font-size:11px; color:var(--text-tertiary); font-weight:normal;">(Optional)</span>
          </label>
          <input type="text" id="auth-highrise-user" class="app-input no-reveal" placeholder="e.g. Sarah">
        </div>

        <div id="auth-msg-box" class="status-alert" style="display: none; margin-bottom: 16px;"></div>

        <button type="submit" id="btn-auth-submit" class="btn btn-primary" style="width:100%; padding:12px; font-size:14px; font-weight:700;">
          <span id="auth-submit-label">Sign In to ZevRay</span>
        </button>
      </form>
    </div>
  </dialog>

  <!-- ---------------- FOOTER ---------------- -->
  <footer class="site-footer">
    <div class="footer-inner">
      <div style="display: flex; align-items: center; gap: 8px;">
        <div class="brand-icon" style="width:22px; height:22px; border-radius:6px;">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
        </div>
        <span>&copy; 2026 <b>ZevRay</b> &bull; zevray.com. All rights reserved.</span>
      </div>

      <div class="footer-links">
        <a href="#features">Features</a>
        <a href="#deploy">Deployer</a>
        <a href="/player" target="_blank">Jukebox</a>
        <a href="https://create.highrise.game" target="_blank">Highrise Developer Hub</a>
      </div>
    </div>
  </footer>

  <!-- ---------------- CLIENT LOGIC ---------------- -->
  <script>
    const SUPABASE_URL = "__SUPABASE_URL__";
    const SUPABASE_ANON_KEY = "__SUPABASE_ANON_KEY__";
    const sb = window.supabase ? window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY) : null;

    let currentUser = null;
    let authMode = 'login'; // 'login' or 'signup'
    let currentRadioUrl = '';

    // Eye SVGs for Reveal/Hide toggling
    const SVG_EYE_OPEN = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>`;
    const SVG_EYE_SLASH = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><line x1="2" y1="2" x2="22" y2="22"/></svg>`;

    function toggleInputVisibility(inputId, btn) {
      const input = document.getElementById(inputId);
      if (!input) return;
      if (input.type === 'password') {
        input.type = 'text';
        btn.innerHTML = SVG_EYE_SLASH;
      } else {
        input.type = 'password';
        btn.innerHTML = SVG_EYE_OPEN;
      }
    }

    // Modern Dialog Helpers with closedby="any" light-dismiss fallback
    const authDialog = document.getElementById('auth-dialog');
    if (!('closedBy' in HTMLDialogElement.prototype)) {
      const outside = (e, dialog) => {
        const r = dialog.getBoundingClientRect();
        return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      };
      let pressedOutside = false;
      authDialog.addEventListener('pointerdown', (e) => { pressedOutside = outside(e, authDialog); });
      authDialog.addEventListener('pointerup', (e) => { if (pressedOutside && outside(e, authDialog)) authDialog.close(); });
    }

    function openAuthDialog(mode = 'login') {
      authMode = mode;
      switchAuthTab(mode);
      document.getElementById('auth-msg-box').style.display = 'none';
      if (typeof authDialog.showModal === 'function') {
        authDialog.showModal();
      }
    }

    function closeAuthDialog() {
      if (authDialog.open) authDialog.close();
    }

    function switchAuthTab(mode) {
      authMode = mode;
      const tabLogin = document.getElementById('btn-tab-login');
      const tabSignup = document.getElementById('btn-tab-signup');
      const extra = document.getElementById('signup-extra-fields');
      const submitLabel = document.getElementById('auth-submit-label');

      if (mode === 'signup') {
        tabLogin.classList.remove('active');
        tabSignup.classList.add('active');
        extra.style.display = 'block';
        submitLabel.innerText = 'Create Free Account';
      } else {
        tabSignup.classList.remove('active');
        tabLogin.classList.add('active');
        extra.style.display = 'none';
        submitLabel.innerText = 'Sign In to ZevRay';
      }
      document.getElementById('auth-msg-box').style.display = 'none';
    }

    // ---------------- AUTH OPERATIONS ----------------
    async function initAuth() {
      if (!sb) return;
      try {
        const { data: { session } } = await sb.auth.getSession();
        applyUserSession(session ? session.user : null);

        sb.auth.onAuthStateChange((_event, session) => {
          applyUserSession(session ? session.user : null);
        });
      } catch (err) {
        console.error("Auth error:", err);
      }
    }

    function applyUserSession(user) {
      currentUser = user;
      const loggedOutNav = document.getElementById('nav-logged-out');
      const loggedInNav = document.getElementById('nav-logged-in');
      const dash = document.getElementById('user-dashboard-panel');

      if (user) {
        loggedOutNav.style.display = 'none';
        loggedInNav.style.display = 'flex';
        dash.style.display = 'block';
        fetchUserBots();
      } else {
        loggedOutNav.style.display = 'flex';
        loggedInNav.style.display = 'none';
        dash.style.display = 'none';
      }
    }

    async function handleAuthSubmit(e) {
      e.preventDefault();
      if (!sb) return;

      const email = document.getElementById('auth-email').value.trim();
      const password = document.getElementById('auth-password').value;
      const hrUser = document.getElementById('auth-highrise-user').value.trim();
      const msgBox = document.getElementById('auth-msg-box');
      const submitBtn = document.getElementById('btn-auth-submit');

      msgBox.style.display = 'none';
      submitBtn.disabled = true;

      try {
        if (authMode === 'signup') {
          const { data, error } = await sb.auth.signUp({
            email,
            password,
            options: {
              data: { highrise_username: hrUser }
            }
          });
          if (error) throw error;
          if (data.session) {
            closeAuthDialog();
          } else {
            msgBox.className = 'status-alert status-loading';
            msgBox.innerText = 'Confirmation link sent! Please check your email to complete registration.';
            msgBox.style.display = 'block';
          }
        } else {
          const { error } = await sb.auth.signInWithPassword({ email, password });
          if (error) throw error;
          closeAuthDialog();
        }
      } catch (err) {
        msgBox.className = 'status-alert status-error';
        msgBox.innerText = err.message || 'Authentication error.';
        msgBox.style.display = 'block';
      } finally {
        submitBtn.disabled = false;
      }
    }

    async function handleSignOut() {
      if (sb) await sb.auth.signOut();
      applyUserSession(null);
    }

    function toggleUserDashboard() {
      const panel = document.getElementById('user-dashboard-panel');
      if (panel.style.display === 'none') {
        panel.style.display = 'block';
        panel.scrollIntoView({ behavior: 'smooth' });
        fetchUserBots();
      } else {
        panel.style.display = 'none';
      }
    }

    async function fetchUserBots() {
      if (!currentUser) return;
      const container = document.getElementById('user-bots-list');
      const badge = document.getElementById('user-bots-badge');

      try {
        const res = await fetch(`/api/user-bots?user_id=${currentUser.id}`);
        const data = await res.json();
        if (!data.success || !data.bots || data.bots.length === 0) {
          container.innerHTML = `<p style="font-size: 13px; color: var(--text-tertiary); text-align: center; padding: 20px 0;">You haven't deployed any bots yet. Use the form below to launch your first 24/7 bot!</p>`;
          badge.innerText = '0';
          return;
        }

        badge.innerText = data.bots.length;
        let html = '';
        data.bots.forEach(bot => {
          const streamUrl = bot.radio_stream_url || `${bot.render_service_url}/radio.mp3`;
          const playerUrl = `${bot.render_service_url}/player`;
          html += `
            <div class="bot-card-item">
              <div class="bot-card-top">
                <span class="bot-card-name">${bot.room_title || 'Highrise Bot'}</span>
                <span class="preview-live-indicator" style="font-size:11px; padding:2px 8px;">Online 24/7</span>
              </div>
              <div style="font-size:12px; color:var(--text-secondary); margin-bottom:6px;">
                Room ID: <code>${bot.room_id}</code> &bull; Owner: <b>@${bot.room_owner}</b>
              </div>
              <div class="bot-card-links">
                <button class="btn btn-secondary" style="font-size:12px; padding:4px 10px;" onclick="copyDirect('${streamUrl}', this)">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>
                  <span>Copy .mp3 Stream URL</span>
                </button>
                <a href="${playerUrl}" target="_blank" class="btn btn-secondary" style="font-size:12px; padding:4px 10px;">
                  <span>Open Player &rarr;</span>
                </a>
              </div>
            </div>
          `;
        });
        container.innerHTML = html;
      } catch (err) {
        container.innerHTML = `<p style="color:var(--accent-red); font-size:12px;">Failed to load bots: ${err.message}</p>`;
      }
    }

    // ---------------- LOCAL CREDENTIAL REMEMBER PREFERENCE ----------------
    const STORAGE_KEY = 'zevray_bot_saved_creds';

    function restoreSavedDetails() {
      try {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved) {
          const data = JSON.parse(saved);
          if (data.highrise_token) document.getElementById('highrise-token').value = data.highrise_token;
          if (data.room_id) document.getElementById('room-id').value = data.room_id;
          if (data.room_owner) document.getElementById('room-owner').value = data.room_owner;
          if (data.room_title) document.getElementById('room-title').value = data.room_title;
          if (data.render_key) document.getElementById('render-key').value = data.render_key;
          if (data.cron_key) document.getElementById('cron-key').value = data.cron_key;
        }
      } catch (e) {
        console.warn("Could not load saved credentials:", e);
      }
    }

    function saveDetailsIfAllowed(payload) {
      const allowed = document.getElementById('save-local-pref').checked;
      if (allowed) {
        try {
          localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
        } catch (e) {
          console.warn("Could not save to localStorage:", e);
        }
      }
    }

    // ---------------- DEPLOY SUBMIT HANDLER ----------------
    const deployForm = document.getElementById('deployer-form');
    const submitBtn = document.getElementById('btn-deploy-submit');
    const submitText = document.getElementById('deploy-btn-text');
    const loadBox = document.getElementById('deploy-loading');
    const errBox = document.getElementById('deploy-error');
    const resCard = document.getElementById('deploy-result');

    deployForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      errBox.style.display = 'none';
      resCard.style.display = 'none';
      loadBox.style.display = 'flex';
      submitBtn.disabled = true;

      const payload = {
        highrise_token: document.getElementById('highrise-token').value.trim(),
        room_id: document.getElementById('room-id').value.trim(),
        room_owner: document.getElementById('room-owner').value.trim(),
        room_title: document.getElementById('room-title').value.trim(),
        render_api_key: document.getElementById('render-key').value.trim(),
        cron_api_key: document.getElementById('cron-key').value.trim(),
        user_id: currentUser ? currentUser.id : ''
      };

      saveDetailsIfAllowed(payload);

      try {
        const res = await fetch('/api/deploy-render', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        const data = await res.json();
        loadBox.style.display = 'none';
        submitBtn.disabled = false;

        if (!res.ok || !data.success) {
          errBox.innerText = data.error || "Deployment failed. Please verify your Render and Highrise credentials.";
          errBox.style.display = 'flex';
          return;
        }

        // Populate Success Card
        currentRadioUrl = data.radio_url;
        document.getElementById('res-radio-field').value = data.radio_url;
        document.getElementById('res-player-link').href = data.player_url;

        const cronBadge = document.getElementById('res-cron-badge');
        if (data.cron_job_created) {
          cronBadge.innerText = 'Custom cron-job.org Configured (10m)';
        } else {
          cronBadge.innerText = 'Master Auto-Pinger Active (10m)';
        }

        resCard.style.display = 'block';
        resCard.scrollIntoView({ behavior: 'smooth' });

        if (currentUser) {
          fetchUserBots();
          document.getElementById('user-dashboard-panel').style.display = 'block';
        }
      } catch (err) {
        loadBox.style.display = 'none';
        submitBtn.disabled = false;
        errBox.innerText = "Network error: " + err.message;
        errBox.style.display = 'flex';
      }
    });

    function copyStreamUrl() {
      const url = currentRadioUrl || document.getElementById('res-radio-field').value;
      if (!url) return;
      navigator.clipboard.writeText(url).then(() => {
        const btnText = document.getElementById('copy-radio-text');
        btnText.innerText = "Copied to Clipboard!";
        setTimeout(() => { btnText.innerText = "Copy .mp3 Link"; }, 2500);
      });
    }

    function copyDirect(text, btn) {
      navigator.clipboard.writeText(text).then(() => {
        const oldText = btn.innerHTML;
        btn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent-green)"><polyline points="20 6 9 17 4 12"/></svg><span>Copied!</span>`;
        setTimeout(() => { btn.innerHTML = oldText; }, 2000);
      });
    }

    // Initialize
    initAuth();
    restoreSavedDetails();
  </script>
</body>
</html>
"""


async def handle_portal_page(request: web.Request) -> web.Response:
    """Serve the 1-click Render automated cloud deployer portal with Supabase Auth."""
    rendered_html = PORTAL_HTML.replace("__SUPABASE_URL__", config.SUPABASE_URL).replace("__SUPABASE_ANON_KEY__", config.SUPABASE_ANON_KEY)
    return web.Response(
        text=rendered_html,
        content_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


async def handle_user_bots_api(request: web.Request) -> web.Response:
    """Fetch all bots deployed by a specific authenticated user from Supabase."""
    user_id = request.query.get("user_id", "").strip()
    if not user_id:
        return web.json_response({"success": False, "error": "user_id parameter is required"}, status=400)
    try:
        bots = await db.get_user_bots_from_db(user_id)
        return web.json_response({"success": True, "bots": bots})
    except Exception as e:
        logger.error(f"[API] Failed to fetch user bots: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_deploy_render(request: web.Request) -> web.Response:
    """Automated backend API to validate credentials and deploy a web service on buyer's Render account."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON payload."}, status=400)

    render_key = str(data.get("render_api_key", "")).strip()
    cron_key = str(data.get("cron_api_key", "")).strip()
    highrise_token = str(data.get("highrise_token", "")).strip()
    raw_room_id = str(data.get("room_id", "")).strip()
    room_owner = str(data.get("room_owner", "")).strip().lstrip("@")
    room_title = str(data.get("room_title", "the room")).strip() or "the room"
    bot_name = str(data.get("bot_name", "")).strip()
    user_id = str(data.get("user_id", "")).strip() or None

    if not render_key:
        return web.json_response({"success": False, "error": "Render API Key is required."}, status=400)
    if not highrise_token:
        return web.json_response({"success": False, "error": "Highrise Bot API Token is required."}, status=400)
    if not raw_room_id:
        return web.json_response({"success": False, "error": "Highrise Room ID or URL is required."}, status=400)

    # Extract 24-character hexadecimal ID from raw input or URL
    match = re.search(r"[0-9a-f]{24}", raw_room_id.lower())
    if not match:
        return web.json_response({
            "success": False,
            "error": "Could not find a valid 24-character Highrise Room ID. Example format: 64005ecfb3d3644258e0040e"
        }, status=400)
    room_id = match.group(0)

    # Build safe unique slug for Render service name
    if bot_name:
        slug = re.sub(r"[^a-z0-9-]", "", bot_name.lower().replace(" ", "-"))[:28]
    else:
        owner_slug = re.sub(r"[^a-z0-9-]", "", room_owner.lower()) if room_owner else "dj"
        slug = f"hrbot-{owner_slug[:10]}-{room_id[-6:]}"

    headers = {
        "Authorization": f"Bearer {render_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        # Step 1: Validate Render API Key & Retrieve Owner ID
        try:
            async with session.get("https://api.render.com/v1/owners", headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status in (401, 403):
                    return web.json_response({
                        "success": False,
                        "error": "Invalid Render API Key. Please verify your API key from Render.com -> Account Settings -> API Keys."
                    }, status=401)
                if resp.status != 200:
                    err_txt = await resp.text()
                    return web.json_response({
                        "success": False,
                        "error": f"Render API error ({resp.status}): {err_txt}"
                    }, status=400)
                owners_data = await resp.json()
        except Exception as e:
            return web.json_response({"success": False, "error": f"Failed to connect to Render API: {e}"}, status=500)

        if not owners_data:
            return web.json_response({"success": False, "error": "No Render owner account found for this API key."}, status=400)

        first_owner = owners_data[0]
        owner_id = first_owner.get("owner", {}).get("id") or first_owner.get("id")

        # Step 2: Create Web Service via Render API
        env_vars_list = [
            {"key": "PYTHON_VERSION", "value": "3.11.9"},
            {"key": "API_TOKEN", "value": highrise_token},
            {"key": "ROOM_ID", "value": room_id},
            {"key": "ROOM_OWNER", "value": room_owner or "owner"},
            {"key": "ROOM_TITLE", "value": room_title},
            {"key": "BOT_PREFIX", "value": "!"},
            {"key": "AUTOPLAY_ENABLED", "value": "true"},
            {"key": "SUPABASE_URL", "value": config.SUPABASE_URL},
            {"key": "SUPABASE_KEY", "value": config.SUPABASE_KEY},
            {"key": "SUPABASE_ANON_KEY", "value": config.SUPABASE_ANON_KEY},
        ]

        create_payload = {
            "type": "web_service",
            "name": slug,
            "ownerId": owner_id,
            "repo": "https://github.com/subhadipray99/raydjbot",
            "branch": "main",
            "autoDeploy": "yes",
            "serviceDetails": {
                "env": "python",
                "plan": "free",
                "region": "oregon",
                "envSpecificDetails": {
                    "buildCommand": "pip install -r requirements.txt",
                    "startCommand": "python main.py",
                },
                "envVars": env_vars_list,
            },
            "envVars": env_vars_list,
        }

        try:
            async with session.post("https://api.render.com/v1/services", headers=headers, json=create_payload, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                resp_json = await resp.json()
                if resp.status not in (200, 201):
                    err_msg = resp_json.get("message") or str(resp_json)
                    return web.json_response({
                        "success": False,
                        "error": f"Render rejected service creation: {err_msg}"
                    }, status=400)

                srv_data = resp_json
                srv_id = srv_data.get("id", "")
                srv_name = srv_data.get("name", slug)
                srv_url = srv_data.get("serviceDetails", {}).get("url") or f"https://{srv_name}.onrender.com"

                # Step 3: Firmly commit environment variables via dedicated endpoint
                if srv_id:
                    try:
                        await session.put(
                            f"https://api.render.com/v1/services/{srv_id}/env-vars",
                            headers=headers,
                            json=env_vars_list,
                            timeout=aiohttp.ClientTimeout(total=10),
                        )
                    except Exception as ev_err:
                        logger.warning(f"Secondary env-vars sync notice: {ev_err}")

                # Step 4: Automatically enroll in 10-minute master keep-alive network
                register_bot_url(srv_url)

                # Step 4b: Record bot deployment in Supabase bots table
                try:
                    bot_record = {
                        "room_id": room_id,
                        "room_owner": room_owner or "owner",
                        "room_title": room_title,
                        "render_service_id": srv_id,
                        "render_service_name": srv_name,
                        "render_service_url": srv_url,
                        "radio_stream_url": f"{srv_url}/radio.mp3",
                        "health_url": f"{srv_url}/health",
                        "is_active": True,
                    }
                    if user_id:
                        bot_record["user_id"] = user_id
                    asyncio.create_task(db.register_bot_in_db(bot_record))
                except Exception as db_err:
                    logger.warning(f"Failed to queue db.register_bot_in_db: {db_err}")

                # Step 5: If buyer provided personal cron-job.org API key, automatically create the 10-min job
                cron_job_created = False
                cron_job_notice = ""
                if cron_key:
                    try:
                        cron_payload = {
                            "job": {
                                "title": f"Highrise DJ Bot ({slug})",
                                "url": f"{srv_url}/health",
                                "enabled": True,
                                "saveResponses": True,
                                "requestMethod": 0,
                                "schedule": {
                                    "timezone": "UTC",
                                    "hours": [-1],
                                    "mdays": [-1],
                                    "months": [-1],
                                    "wdays": [-1],
                                    "minutes": [0, 10, 20, 30, 40, 50],
                                },
                            }
                        }
                        cron_headers = {
                            "Authorization": f"Bearer {cron_key}",
                            "Content-Type": "application/json",
                        }
                        async with session.put(
                            "https://api.cron-job.org/jobs",
                            headers=cron_headers,
                            json=cron_payload,
                            timeout=aiohttp.ClientTimeout(total=15),
                        ) as c_resp:
                            if c_resp.status in (200, 201):
                                cron_job_created = True
                                logger.info(f"Successfully auto-created 10-min cron job for {slug} on cron-job.org")
                            else:
                                c_txt = await c_resp.text()
                                cron_job_notice = f"cron-job.org status {c_resp.status}"
                                logger.warning(f"cron-job.org returned {c_resp.status}: {c_txt}")
                    except Exception as ce:
                        cron_job_notice = str(ce)
                        logger.warning(f"cron-job.org API exception: {ce}")

                return web.json_response({
                    "success": True,
                    "service_id": srv_id,
                    "service_name": srv_name,
                    "service_url": srv_url,
                    "player_url": f"{srv_url}/player",
                    "health_url": f"{srv_url}/health",
                    "radio_url": f"{srv_url}/radio.mp3",
                    "cron_job_created": cron_job_created,
                    "cron_job_notice": cron_job_notice,
                    "master_pinger_enrolled": True,
                })
        except Exception as e:
            return web.json_response({"success": False, "error": f"Error communicating with Render: {e}"}, status=500)


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/status", handle_status_api)
    app.router.add_get("/api/status", handle_status_api)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/player", handle_player_page)
    app.router.add_get("/portal", handle_portal_page)
    app.router.add_get("/deploy", handle_portal_page)
    app.router.add_post("/api/deploy-render", handle_deploy_render)
    app.router.add_get("/api/user-bots", handle_user_bots_api)
    app.router.add_get("/api/player-state", handle_player_state)
    app.router.add_get("/audio/current.mp3", handle_audio_current)
    app.router.add_get("/audio/current", handle_audio_current)
    app.router.add_get("/radio.mp3", handle_radio_stream)
    app.router.add_get("/radio", handle_radio_stream)
    app.router.add_get("/stream.mp3", handle_radio_stream)
    app.router.add_get("/debug/logs", handle_debug_logs)
    app.router.add_get("/debug/download", handle_debug_download)
    return app


async def start_web_server(port: int) -> web.AppRunner:
    music_manager.start()
    broadcaster.start()
    asyncio.create_task(run_master_pinger_loop())
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"[Web] Keep-alive Web Server listening on http://0.0.0.0:{port}")
    return runner
