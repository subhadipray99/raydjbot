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
        return await handle_landing_page(request)
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


LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ZevRay &bull; Autonomous 24/7 Highrise Butler & DJ</title>
  
  <!-- Cloudstudio Typography: Bricolage Grotesque + Geist Mono -->
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;500;600;700;800&family=Geist+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
  
  <!-- Supabase JS Client v2 -->
  <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>

  <style>
    :root {
      --bg: #FFF48D;
      --paper: #faf8f0;
      --paper-card: #ffffff;
      --ink: #0e0e0c;
      --ink-soft: #2e2e28;
      --ink-mute: #78786a;
      --sky: #8ED8FF;
      --lavender: #A3B1FF;
      --accent: #FFF48D;
      --ok: #27ae60;
      --border: rgba(14, 14, 12, 0.12);
      --border-dark: rgba(14, 14, 12, 0.22);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    
    html {
      scroll-behavior: smooth;
      -webkit-font-smoothing: antialiased;
    }

    body {
      background-color: var(--paper);
      color: var(--ink);
      font-family: 'Bricolage Grotesque', -apple-system, BlinkMacSystemFont, sans-serif;
      line-height: 1.45;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      overflow-x: hidden;
    }

    /* ---------------- CLOUDSTUDIO FIXED NAV ---------------- */
    #cs-nav {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      z-index: 180;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px clamp(18px, 4vw, 44px);
      background: rgba(250, 248, 240, 0.88);
      backdrop-filter: blur(14px);
      -webkit-backdrop-filter: blur(14px);
      border-bottom: 1px solid var(--border);
      transition: all 0.3s ease;
    }
    .nav-brand {
      text-decoration: none;
      font-weight: 800;
      font-size: 24px;
      letter-spacing: -0.03em;
      color: var(--ink);
      display: flex;
      align-items: center;
      gap: 2px;
    }
    .nav-brand span {
      color: var(--ink);
      font-weight: 900;
    }
    .nav-links {
      display: flex;
      align-items: center;
      gap: clamp(14px, 2.5vw, 32px);
    }
    .nav-link {
      text-decoration: none;
      font-weight: 600;
      font-size: 15px;
      color: var(--ink);
      transition: opacity 0.15s ease;
    }
    .nav-link:hover { opacity: 0.65; }
    
    .btn-pill-ink {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      text-decoration: none;
      font-weight: 700;
      font-size: 15px;
      background: var(--ink);
      color: var(--accent);
      padding: 11px 22px;
      border-radius: 100px;
      border: 1px solid var(--ink);
      cursor: pointer;
      font-family: inherit;
      transition: transform 0.3s cubic-bezier(.34, 1.56, .64, 1), background 0.2s ease;
    }
    .btn-pill-ink:hover {
      transform: scale(1.04);
      background: #1c1c18;
    }
    .btn-pill-outline {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      text-decoration: none;
      font-weight: 700;
      font-size: 15px;
      background: transparent;
      color: var(--ink);
      padding: 11px 22px;
      border-radius: 100px;
      border: 1.5px solid var(--ink);
      cursor: pointer;
      font-family: inherit;
      transition: transform 0.2s ease, background 0.2s ease;
    }
    .btn-pill-outline:hover {
      background: rgba(14, 14, 12, 0.05);
      transform: translateY(-1px);
    }

    /* ---------------- HERO SECTION ---------------- */
    .hero-wrap {
      position: relative;
      min-height: 90vh;
      display: flex;
      flex-direction: column;
      justify-content: center;
      padding: 130px clamp(18px, 4vw, 44px) 80px;
      border-bottom: 1px solid var(--border);
      overflow: hidden;
      max-width: 1320px;
      margin: 0 auto;
      width: 100%;
    }
    
    /* Rotating Stamp Badge (Cloudstudio Signature) */
    .hero-badge-stamp {
      position: absolute;
      right: clamp(20px, 6vw, 90px);
      top: 140px;
      width: clamp(100px, 13vw, 150px);
      height: clamp(100px, 13vw, 150px);
      z-index: 5;
    }
    .spin-ring {
      position: absolute;
      inset: 0;
      animation: cs-spin 16s linear infinite;
    }
    @keyframes cs-spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }
    .stamp-dot {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .stamp-dot span {
      width: 36%;
      height: 36%;
      border-radius: 50%;
      background: var(--ink);
    }

    .hero-kicker {
      font-family: 'Geist Mono', monospace;
      font-size: 13px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      margin-bottom: 24px;
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 600;
    }
    .hero-kicker-dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--ink);
    }

    .hero-h1 {
      font-size: clamp(2.8rem, 7vw, 6.4rem);
      line-height: 0.94;
      font-weight: 800;
      letter-spacing: -0.04em;
      margin-bottom: 28px;
      max-width: 960px;
    }
    .hero-h1-accent {
      background: var(--accent);
      padding: 0 8px;
      border-radius: 8px;
      display: inline-block;
      border: 1px solid var(--ink);
      box-shadow: 3px 3px 0 var(--ink);
    }
    .hero-lead {
      font-size: clamp(18px, 2.2vw, 24px);
      line-height: 1.4;
      color: var(--ink-soft);
      max-width: 720px;
      margin-bottom: 40px;
      font-weight: 500;
    }
    .hero-actions {
      display: flex;
      align-items: center;
      gap: 16px;
      flex-wrap: wrap;
      margin-bottom: 48px;
    }

    /* Micro-ticker */
    .hero-ticker {
      display: flex;
      gap: 24px;
      flex-wrap: wrap;
      font-family: 'Geist Mono', monospace;
      font-size: 12px;
      letter-spacing: 0.05em;
      color: var(--ink-mute);
      text-transform: uppercase;
      padding-top: 24px;
      border-top: 1px dashed var(--border);
    }
    .ticker-item {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .ticker-item span { color: var(--ok); font-weight: 700; }

    /* ---------------- INTERACTIVE JUKEBOX MOCKUP (STUDIO CARD) ---------------- */
    .live-showcase-wrap {
      max-width: 1320px;
      margin: 0 auto;
      padding: clamp(40px, 6vw, 80px) clamp(18px, 4vw, 44px);
    }
    .studio-card {
      background: var(--paper-card);
      border: 1.5px solid var(--ink);
      border-radius: 24px;
      padding: clamp(24px, 4vw, 44px);
      box-shadow: 6px 6px 0 var(--ink);
    }
    .studio-card-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--border);
    }
    .tag-editorial {
      font-family: 'Geist Mono', monospace;
      font-size: 12px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      background: var(--bg);
      border: 1px solid var(--ink);
      padding: 4px 10px;
      border-radius: 100px;
      font-weight: 700;
    }
    .live-stream-bar {
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 20px;
      align-items: center;
      background: #f7f6ed;
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 18px 24px;
      margin-bottom: 20px;
    }
    .wave-bars {
      display: flex;
      align-items: flex-end;
      gap: 4px;
      height: 32px;
    }
    .wave-col {
      width: 5px;
      background: var(--ink);
      border-radius: 3px;
      animation: cs-wave 1.1s ease-in-out infinite alternate;
    }
    .wave-col:nth-child(1) { height: 14px; animation-delay: 0.1s; }
    .wave-col:nth-child(2) { height: 28px; animation-delay: 0.3s; }
    .wave-col:nth-child(3) { height: 8px; animation-delay: 0.2s; }
    .wave-col:nth-child(4) { height: 26px; animation-delay: 0.4s; }
    .wave-col:nth-child(5) { height: 18px; animation-delay: 0.15s; }
    @keyframes cs-wave {
      0% { transform: scaleY(0.3); }
      100% { transform: scaleY(1); }
    }
    .command-pills {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .cmd-pill {
      font-family: 'Geist Mono', monospace;
      font-size: 12px;
      font-weight: 600;
      background: #ffffff;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 4px 10px;
      color: var(--ink);
    }

    /* ---------------- SECTION 01: WHAT WE DO (EDITORIAL GRID) ---------------- */
    .section-wrap {
      max-width: 1320px;
      margin: 0 auto;
      padding: clamp(60px, 8vw, 120px) clamp(18px, 4vw, 44px);
    }
    .sec-kicker {
      font-family: 'Geist Mono', monospace;
      font-size: 13px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      margin-bottom: 16px;
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 600;
    }
    .sec-h2 {
      font-size: clamp(2.4rem, 5.5vw, 4.8rem);
      line-height: 0.96;
      font-weight: 800;
      letter-spacing: -0.035em;
      margin-bottom: 56px;
      max-width: 880px;
    }
    .grid-2col {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 24px;
    }
    @media (max-width: 820px) {
      .grid-2col { grid-template-columns: 1fr; }
      .nav-links { display: none; }
      .live-stream-bar { grid-template-columns: 1fr; }
    }
    .editorial-card {
      background: var(--paper-card);
      border: 1.5px solid var(--ink);
      border-radius: 20px;
      padding: clamp(28px, 4vw, 40px);
      box-shadow: 4px 4px 0 var(--ink);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .editorial-card:hover {
      transform: translateY(-2px);
      box-shadow: 6px 6px 0 var(--ink);
    }
    .card-num {
      font-family: 'Geist Mono', monospace;
      font-size: 16px;
      font-weight: 700;
      color: var(--ink-mute);
      margin-bottom: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .card-title {
      font-size: clamp(24px, 3vw, 32px);
      font-weight: 800;
      letter-spacing: -0.025em;
      line-height: 1.1;
      margin-bottom: 14px;
    }
    .card-body {
      font-size: 15px;
      line-height: 1.6;
      color: var(--ink-soft);
      font-weight: 500;
    }

    /* ---------------- SECTION 02: THE 3-STEP SHIP PROCESS ---------------- */
    .steps-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 20px;
      margin-top: 40px;
    }
    @media (max-width: 880px) {
      .steps-grid { grid-template-columns: 1fr; }
    }
    .step-box {
      background: #ffffff;
      border: 1.5px solid var(--ink);
      border-radius: 18px;
      padding: 30px 24px;
      box-shadow: 3px 3px 0 var(--ink);
    }
    .step-badge {
      display: inline-block;
      font-family: 'Geist Mono', monospace;
      font-size: 12px;
      font-weight: 700;
      background: var(--sky);
      border: 1px solid var(--ink);
      padding: 3px 10px;
      border-radius: 6px;
      margin-bottom: 18px;
    }
    .step-title {
      font-size: 20px;
      font-weight: 800;
      margin-bottom: 10px;
      letter-spacing: -0.015em;
    }
    .step-desc {
      font-size: 14px;
      color: var(--ink-soft);
      line-height: 1.55;
    }

    /* ---------------- FAQ ACCORDION ---------------- */
    .faq-list {
      display: flex;
      flex-direction: column;
      gap: 14px;
      max-width: 880px;
      margin: 0 auto;
    }
    .faq-item {
      background: #ffffff;
      border: 1.5px solid var(--ink);
      border-radius: 16px;
      padding: 24px 28px;
      box-shadow: 3px 3px 0 var(--ink);
    }
    .faq-q {
      font-size: 18px;
      font-weight: 800;
      letter-spacing: -0.01em;
      margin-bottom: 8px;
    }
    .faq-a {
      font-size: 14px;
      color: var(--ink-soft);
      line-height: 1.6;
    }

    /* ---------------- BOTTOM BIG CALLOUT BANNER ---------------- */
    .banner-cta {
      background: var(--bg);
      border-top: 1.5px solid var(--ink);
      border-bottom: 1.5px solid var(--ink);
      padding: clamp(60px, 8vw, 100px) clamp(18px, 4vw, 44px);
      text-align: center;
    }
    .banner-h2 {
      font-size: clamp(2.6rem, 6vw, 5.2rem);
      line-height: 0.95;
      font-weight: 800;
      letter-spacing: -0.035em;
      margin-bottom: 24px;
    }
    .banner-sub {
      font-size: 18px;
      color: var(--ink-soft);
      max-width: 600px;
      margin: 0 auto 36px;
      font-weight: 500;
    }

    /* ---------------- FOOTER ---------------- */
    .cs-footer {
      padding: 48px clamp(18px, 4vw, 44px);
      background: var(--paper);
    }
    .cs-footer-inner {
      max-width: 1320px;
      margin: 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 20px;
      font-family: 'Geist Mono', monospace;
      font-size: 13px;
      color: var(--ink-mute);
    }
    .footer-nav {
      display: flex;
      gap: 24px;
    }
    .footer-nav a {
      color: var(--ink);
      text-decoration: none;
      font-weight: 600;
    }
    .footer-nav a:hover { text-decoration: underline; }

    /* ---------------- AUTH MODAL (NATIVE <DIALOG>) ---------------- */
    dialog:modal {
      border: none;
      padding: 0;
      background: transparent;
      max-width: 440px;
      width: calc(100% - 32px);
      margin: auto;
      overscroll-behavior: contain;
    }
    dialog::backdrop {
      background: rgba(14, 14, 12, 0.45);
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
      overflow: hidden;
      overscroll-behavior: contain;
    }
    .auth-box {
      background: #ffffff;
      border: 2px solid var(--ink);
      border-radius: 24px;
      padding: 32px;
      box-shadow: 8px 8px 0 var(--ink);
    }
    .auth-tabs-row {
      display: flex;
      background: #f7f6ed;
      border: 1px solid var(--border);
      border-radius: 100px;
      padding: 4px;
      margin-bottom: 22px;
    }
    .auth-tab {
      flex: 1;
      border: none;
      background: none;
      font-family: inherit;
      font-weight: 700;
      font-size: 13px;
      padding: 8px;
      border-radius: 100px;
      cursor: pointer;
    }
    .auth-tab.active {
      background: var(--ink);
      color: var(--accent);
    }
    .app-input {
      width: 100%;
      background: #ffffff;
      border: 1.5px solid var(--border-dark);
      border-radius: 12px;
      padding: 12px 14px;
      font-family: inherit;
      font-size: 14px;
      color: var(--ink);
      outline: none;
      margin-top: 6px;
    }
    .app-input:focus {
      border-color: var(--ink);
      box-shadow: 2px 2px 0 var(--ink);
    }

    .btn-google-auth {
      width: 100%;
      background: #ffffff;
      border: 1.5px solid var(--ink);
      border-radius: 100px;
      padding: 12px 18px;
      font-family: inherit;
      font-size: 14px;
      font-weight: 700;
      color: var(--ink);
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 12px;
      cursor: pointer;
      box-shadow: 2px 2px 0 var(--ink);
      transition: all 0.15s ease;
      margin-bottom: 16px;
    }
    .btn-google-auth:hover {
      background: #faf8f0;
      box-shadow: 4px 4px 0 var(--ink);
      transform: translate(-1px, -1px);
    }
    .btn-google-auth:active {
      box-shadow: 0 0 0 var(--ink);
      transform: translate(1px, 1px);
    }
    .auth-divider {
      display: flex;
      align-items: center;
      text-align: center;
      margin: 16px 0;
      color: var(--ink-mute);
      font-size: 11px;
      font-family: 'Geist Mono', monospace;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      font-weight: 700;
    }
    .auth-divider::before,
    .auth-divider::after {
      content: '';
      flex: 1;
      border-bottom: 1px solid var(--border-dark);
    }
    .auth-divider span {
      padding: 0 12px;
    }
  </style>
</head>
<body>

  <!-- Floating Nav (Cloudstudio style) -->
  <nav id="cs-nav">
    <a href="/" class="nav-brand">
      zevray<span>*</span>
    </a>

    <div class="nav-links">
      <a href="#work" class="nav-link">What We Do</a>
      <a href="#how" class="nav-link">How It Works</a>
      <a href="/player" target="_blank" class="nav-link">Live Jukebox</a>
      <a href="#faq" class="nav-link">FAQ</a>
    </div>

    <div style="display: flex; align-items: center; gap: 12px;">
      <button class="nav-link" onclick="openAuthModal()" style="background:none; border:none; cursor:pointer;" id="nav-auth-btn">Sign In</button>
      <a href="/deploy" class="btn-pill-ink">
        <span>Deploy Bot &rarr;</span>
      </a>
    </div>
  </nav>

  <!-- Hero Section -->
  <header class="hero-wrap">
    <!-- Rotating Circular Stamp Seal -->
    <div class="hero-badge-stamp">
      <div class="spin-ring">
        <svg viewBox="0 0 100 100" style="width:100%; height:100%">
          <defs>
            <path id="circle-path" d="M50,50 m-40,0 a40,40 0 1,1 80,0 a40,40 0 1,1 -80,0"></path>
          </defs>
          <text style="font-family:'Geist Mono',monospace; font-size:8.4px; letter-spacing:2.1px; fill:var(--ink); text-transform:uppercase; font-weight:700;">
            <textPath href="#circle-path" startOffset="0">AUTONOMOUS BUTLER &bull; 24/7 ONLINE &bull; HIGHRISE &bull; </textPath>
          </text>
        </svg>
      </div>
      <div class="stamp-dot">
        <span></span>
      </div>
    </div>

    <div class="hero-kicker">
      <span class="hero-kicker-dot"></span>
      <span>( The Highrise Room Engine )</span>
    </div>

    <h1 class="hero-h1">
      Your room's next host <br>
      <span class="hero-h1-accent">isn't a person.</span>
    </h1>

    <p class="hero-lead">
      Autonomous 24/7 in-room radio, multi-floor teleportation, tip leaderboards, and permanent outfit memory. Designed for creators who want their Highrise room bustling around the clock.
    </p>

    <div class="hero-actions">
      <a href="/deploy" class="btn-pill-ink" style="font-size: 17px; padding: 14px 28px;">
        <span>Deploy Your Bot in 60s &rarr;</span>
      </a>
      <a href="/player" target="_blank" class="btn-pill-outline" style="font-size: 17px; padding: 14px 28px;">
        <span>Open Live Jukebox</span>
      </a>
    </div>

    <div class="hero-ticker">
      <div class="ticker-item"><span>&bull;</span> 100% Free Cloud Hosting</div>
      <div class="ticker-item"><span>&bull;</span> 24/7 Keep-Alive Auto-Pinger</div>
      <div class="ticker-item"><span>&bull;</span> Permanent Supabase Cloud State</div>
      <div class="ticker-item"><span>&bull;</span> Zero Coding Required</div>
    </div>
  </header>

  <!-- Interactive Live Jukebox Showcase -->
  <div class="live-showcase-wrap">
    <div class="studio-card">
      <div class="studio-card-top">
        <span class="tag-editorial">Live In-Room Broadcast Simulation</span>
        <div style="font-family:'Geist Mono',monospace; font-size:12px; font-weight:700;">
          STATUS: <span style="color:var(--ok);">STREAMING 24/7</span>
        </div>
      </div>

      <div class="live-stream-bar">
        <div class="wave-bars">
          <div class="wave-col"></div>
          <div class="wave-col"></div>
          <div class="wave-col"></div>
          <div class="wave-col"></div>
          <div class="wave-col"></div>
        </div>
        <div>
          <div style="font-weight: 800; font-size: 16px;">Synchronous In-Room Radio Station</div>
          <div style="font-size: 13px; color: var(--ink-mute);">Highrise Custom Radio Link (.mp3) &bull; Synchronized for all guests</div>
        </div>
        <a href="/player" target="_blank" class="btn-pill-ink" style="font-size:13px; padding:8px 16px;">
          <span>Listen in Browser &rarr;</span>
        </a>
      </div>

      <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
        <span style="font-size: 12px; font-family:'Geist Mono',monospace; font-weight: 600; color: var(--ink-mute);">
          BUILT-IN CHAT COMMANDS:
        </span>
        <div class="command-pills">
          <span class="cmd-pill">/play &lt;song&gt;</span>
          <span class="cmd-pill">/skip</span>
          <span class="cmd-pill">/f1 ... /f4</span>
          <span class="cmd-pill">!fit</span>
          <span class="cmd-pill">!setpos</span>
          <span class="cmd-pill">!setwelcome</span>
        </div>
      </div>
    </div>
  </div>

  <!-- Section 01: What We Do (Editorial Grid) -->
  <section class="section-wrap" id="work">
    <div class="sec-kicker">
      <span class="hero-kicker-dot"></span>
      <span>What We Do &bull; The 4 Pillars</span>
    </div>
    <h2 class="sec-h2">We don't build toys.<br>We ship autonomous room infrastructure.</h2>

    <div class="grid-2col">
      <!-- 01 -->
      <div class="editorial-card">
        <div>
          <div class="card-num">
            <span>01</span>
            <span>( Radio Stream )</span>
          </div>
          <h3 class="card-title">24/7 In-Room Music Takeover</h3>
          <p class="card-body">
            Stream directly through your Highrise room speakers via <code>/radio.mp3</code>. Guests can request any song with <code>/play</code>, jump queue, or enjoy curated autonomous playlists when the queue clears.
          </p>
        </div>
      </div>

      <!-- 02 -->
      <div class="editorial-card">
        <div>
          <div class="card-num">
            <span>02</span>
            <span>( Multi-Floor )</span>
          </div>
          <h3 class="card-title">Dynamic Multi-Floor Elevator</h3>
          <p class="card-body">
            Whether you run a 2-floor lounge or a 5-story club, walk to each level and type <code>!setfloor f1</code>, <code>!setfloor f2</code>. Guests teleport instantly across levels simply by typing <code>/f1</code> to <code>/f4</code>.
          </p>
        </div>
      </div>

      <!-- 03 -->
      <div class="editorial-card">
        <div>
          <div class="card-num">
            <span>03</span>
            <span>( Tip Engine )</span>
          </div>
          <h3 class="card-title">Live Tip Leaderboard & Top Tipper</h3>
          <p class="card-body">
            Automatically track gold tips in real time. The bot broadcasts celebratory in-room whispers, celebrates supporters, and crowns your room's Top Tipper on the live leaderboard.
          </p>
        </div>
      </div>

      <!-- 04 -->
      <div class="editorial-card">
        <div>
          <div class="card-num">
            <span>04</span>
            <span>( State Anchor )</span>
          </div>
          <h3 class="card-title">Permanent Outfits & Memory</h3>
          <p class="card-body">
            Cloud-backed state via Supabase. Your bot never forgets its wardrobe (<code>!fit</code>), DJ booth anchor (<code>!setpos</code>), or welcome message when the cloud container updates or redeploys.
          </p>
        </div>
      </div>
    </div>
  </section>

  <!-- Section 02: How It Works (The 3-Step Ship Process) -->
  <section class="section-wrap" id="how" style="background:#f4f2e6; border-top:1px solid var(--border); border-bottom:1px solid var(--border);">
    <div class="sec-kicker">
      <span class="hero-kicker-dot"></span>
      <span>Execution &bull; 60 Seconds</span>
    </div>
    <h2 class="sec-h2">How it ships to your room.</h2>

    <div class="steps-grid">
      <div class="step-box">
        <span class="step-badge">STEP 01</span>
        <h3 class="step-title">Get Highrise Token</h3>
        <p class="step-desc">
          Sign into <b>create.highrise.game</b> &rarr; Bots & API Keys &rarr; Create Bot. Copy your bot's 64-character token in 30 seconds.
        </p>
      </div>

      <div class="step-box">
        <span class="step-badge">STEP 02</span>
        <h3 class="step-title">1-Click Cloud Launch</h3>
        <p class="step-desc">
          Open our dedicated <b>/deploy</b> page. Paste your token, room link, and Render key. Hit deploy &mdash; Render builds your bot container automatically.
        </p>
      </div>

      <div class="step-box">
        <span class="step-badge">STEP 03</span>
        <h3 class="step-title">Enable In-Room Audio</h3>
        <p class="step-desc">
          Copy your generated <code>.mp3</code> stream URL and paste it into Highrise <b>Room Settings &rarr; Custom Radio Station</b>. Music plays for everyone!
        </p>
      </div>
    </div>

    <div style="text-align: center; margin-top: 48px;">
      <a href="/deploy" class="btn-pill-ink" style="font-size: 16px; padding: 13px 26px;">
        <span>Go to Bot Setup Form (/deploy) &rarr;</span>
      </a>
    </div>
  </section>

  <!-- Section 03: FAQ -->
  <section class="section-wrap" id="faq">
    <div class="sec-kicker">
      <span class="hero-kicker-dot"></span>
      <span>Common Questions</span>
    </div>
    <h2 class="sec-h2">Frequently asked questions.</h2>

    <div class="faq-list">
      <div class="faq-item">
        <div class="faq-q">Does my computer or phone need to stay on?</div>
        <div class="faq-a">
          No! Your bot runs inside an autonomous cloud container on Render. You can close your laptop, turn off your phone, or go offline &mdash; the bot stays active 24/7 in your room.
        </div>
      </div>

      <div class="faq-item">
        <div class="faq-q">How does in-room sound work?</div>
        <div class="faq-a">
          Our cloud server broadcasts a direct synchronous MP3 stream (`/radio.mp3`). Highrise has a built-in Custom Radio feature in Room Settings &mdash; paste the link there, and all guests in your room hear the same music simultaneously!
        </div>
      </div>

      <div class="faq-item">
        <div class="faq-q">Can other players mess with my bot's coordinates or outfit?</div>
        <div class="faq-a">
          Never. You specify your Highrise username as the Master Administrator during deployment. Admin commands like <code>!setpos</code>, <code>!setfloor</code>, <code>!fit</code>, and <code>!setwelcome</code> strictly respond only to you.
        </div>
      </div>

      <div class="faq-item">
        <div class="faq-q">Where is the bot setup form?</div>
        <div class="faq-a">
          The bot setup form is located on our dedicated deployment page at <b><a href="/deploy" style="color:var(--ink); font-weight:700;">/deploy</a></b>. It features detailed step-by-step tutorials for every field, password reveal/hide eye toggles, and browser credential memory.
        </div>
      </div>
    </div>
  </section>

  <!-- Bottom CTA Banner (Cloudstudio signature) -->
  <section class="banner-cta">
    <h2 class="banner-h2">Ready to ship your 24/7 Highrise butler?</h2>
    <p class="banner-sub">
      Launch your autonomous DJ, multi-floor elevator, and live tip tracker on your free Render cloud account in 60 seconds.
    </p>
    <a href="/deploy" class="btn-pill-ink" style="font-size: 18px; padding: 16px 36px;">
      <span>Launch Your Bot on /deploy &rarr;</span>
    </a>
  </section>

  <!-- Footer -->
  <footer class="cs-footer">
    <div class="cs-footer-inner">
      <div>
        <b>zevray*</b> &bull; Autonomous Highrise Platform &bull; &copy; 2026 All rights reserved.
      </div>
      <div class="footer-nav">
        <a href="/deploy">Deployer</a>
        <a href="/player" target="_blank">Jukebox</a>
        <a href="https://create.highrise.game" target="_blank">Highrise Developer Hub</a>
      </div>
    </div>
  </footer>

  <!-- Auth Dialog (Native <dialog>) -->
  <dialog id="auth-dialog" closedby="any" aria-labelledby="auth-title">
    <div class="auth-box">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:18px;">
        <span style="font-weight:800; font-size:18px;" id="auth-title">zevray* account</span>
        <button onclick="document.getElementById('auth-dialog').close()" style="background:none; border:none; font-size:18px; cursor:pointer;">&times;</button>
      </div>

      <!-- Google OAuth Button -->
      <button type="button" class="btn-google-auth" onclick="signInWithGoogle()">
        <svg width="18" height="18" viewBox="0 0 24 24">
          <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
          <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
          <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"/>
          <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/>
        </svg>
        <span>Continue with Google</span>
      </button>

      <div class="auth-divider">
        <span>or with email</span>
      </div>

      <div class="auth-tabs-row">
        <button type="button" class="auth-tab active" id="tab-login" onclick="setAuthTab('login')">Sign In</button>
        <button type="button" class="auth-tab" id="tab-signup" onclick="setAuthTab('signup')">Create Account</button>
      </div>

      <form id="auth-form" onsubmit="handleAuth(event)">
        <div style="margin-bottom:14px;">
          <label style="font-size:13px; font-weight:700;">Email Address</label>
          <input type="email" id="auth-email" class="app-input" placeholder="you@example.com" required>
        </div>

        <div style="margin-bottom:14px;">
          <label style="font-size:13px; font-weight:700;">Password</label>
          <input type="password" id="auth-password" class="app-input" placeholder="••••••••" required minlength="6">
        </div>

        <div id="signup-extra" style="display:none; margin-bottom:14px;">
          <label style="font-size:13px; font-weight:700;">Highrise Username (Optional)</label>
          <input type="text" id="auth-hr" class="app-input" placeholder="e.g. Sarah">
        </div>

        <div id="auth-err" style="display:none; color:red; font-size:12px; margin-bottom:12px;"></div>

        <button type="submit" class="btn-pill-ink" style="width:100%; justify-content:center; padding:12px;" id="btn-auth-sub">
          <span id="auth-btn-txt">Sign In</span>
        </button>
      </form>
    </div>
  </dialog>

  <script>
    const SUPABASE_URL = "__SUPABASE_URL__";
    const SUPABASE_ANON_KEY = "__SUPABASE_ANON_KEY__";
    const sb = window.supabase ? window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY) : null;

    let authMode = 'login';
    const authDialog = document.getElementById('auth-dialog');

    function openAuthModal() {
      if (typeof authDialog.showModal === 'function') authDialog.showModal();
    }

    function setAuthTab(mode) {
      authMode = mode;
      document.getElementById('tab-login').classList.toggle('active', mode === 'login');
      document.getElementById('tab-signup').classList.toggle('active', mode === 'signup');
      document.getElementById('signup-extra').style.display = mode === 'signup' ? 'block' : 'none';
      document.getElementById('auth-btn-txt').innerText = mode === 'signup' ? 'Create Account' : 'Sign In';
      document.getElementById('auth-err').style.display = 'none';
    }

    async function handleAuth(e) {
      e.preventDefault();
      if (!sb) return;
      const email = document.getElementById('auth-email').value.trim();
      const password = document.getElementById('auth-password').value;
      const hr = document.getElementById('auth-hr').value.trim();
      const errBox = document.getElementById('auth-err');

      try {
        if (authMode === 'signup') {
          const { error } = await sb.auth.signUp({ email, password, options: { data: { highrise_username: hr } } });
          if (error) throw error;
          alert('Check your email for confirmation link!');
          authDialog.close();
        } else {
          const { error } = await sb.auth.signInWithPassword({ email, password });
          if (error) throw error;
          authDialog.close();
          window.location.href = '/deploy';
        }
      } catch (err) {
        errBox.innerText = err.message;
        errBox.style.display = 'block';
      }
    }

    async function signInWithGoogle() {
      if (!sb) {
        alert("Authentication client not initialized.");
        return;
      }
      try {
        const { data, error } = await sb.auth.signInWithOAuth({
          provider: 'google',
          options: {
            redirectTo: window.location.origin + '/deploy'
          }
        });
        if (error) throw error;
      } catch (err) {
        const errBox = document.getElementById('auth-err');
        if (errBox) {
          errBox.innerText = err.message;
          errBox.style.display = 'block';
        } else {
          alert(err.message);
        }
      }
    }

    function renderUserNav(user) {
      const navBtn = document.getElementById('nav-auth-btn');
      if (!navBtn) return;
      const meta = user.user_metadata || {};
      const name = meta.full_name || meta.name || user.email.split('@')[0];
      const avatar = meta.avatar_url || meta.picture || '';

      let avatarHtml = '';
      if (avatar) {
        avatarHtml = `<img src="${avatar}" alt="" style="width:22px; height:22px; border-radius:50%; border:1px solid var(--ink); object-fit:cover;">`;
      }
      navBtn.innerHTML = `<span style="display:inline-flex; align-items:center; gap:8px;">${avatarHtml} Dashboard (${name})</span>`;
      navBtn.onclick = () => { window.location.href = '/deploy'; };
    }

    async function checkUser() {
      if (!sb) return;
      const { data: { session } } = await sb.auth.getSession();
      if (session && session.user) {
        renderUserNav(session.user);
      }

      sb.auth.onAuthStateChange((event, session) => {
        if (session && session.user) {
          renderUserNav(session.user);
        }
      });
    }
    checkUser();
  </script>
</body>
</html>
"""


DEPLOY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Deploy Bot &bull; ZevRay Highrise Cloud Engine</title>
  
  <!-- Cloudstudio Typography -->
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;500;600;700;800&family=Geist+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
  
  <!-- Supabase JS Client v2 -->
  <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>

  <style>
    :root {
      --bg: #FFF48D;
      --paper: #faf8f0;
      --paper-card: #ffffff;
      --ink: #0e0e0c;
      --ink-soft: #2e2e28;
      --ink-mute: #78786a;
      --sky: #8ED8FF;
      --lavender: #A3B1FF;
      --accent: #FFF48D;
      --ok: #27ae60;
      --border: rgba(14, 14, 12, 0.12);
      --border-dark: rgba(14, 14, 12, 0.22);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    
    body {
      background-color: var(--paper);
      color: var(--ink);
      font-family: 'Bricolage Grotesque', -apple-system, BlinkMacSystemFont, sans-serif;
      line-height: 1.45;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      padding-bottom: 60px;
    }

    /* Top Nav */
    .deploy-nav {
      max-width: 1160px;
      margin: 0 auto;
      width: 100%;
      padding: 24px 20px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .brand-link {
      text-decoration: none;
      font-weight: 800;
      font-size: 22px;
      color: var(--ink);
    }
    .back-link {
      font-family: 'Geist Mono', monospace;
      font-size: 13px;
      font-weight: 600;
      color: var(--ink);
      text-decoration: none;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .back-link:hover { text-decoration: underline; }

    /* Responsive 2-Column Dashboard Grid */
    .deploy-grid-layout {
      display: grid;
      grid-template-columns: 1fr 340px;
      gap: 28px;
      align-items: start;
      width: 100%;
    }
    @media (max-width: 960px) {
      .deploy-grid-layout {
        grid-template-columns: 1fr;
      }
      .deploy-sidebar {
        order: 2;
      }
    }

    .deploy-main-col {
      display: flex;
      flex-direction: column;
      gap: 24px;
      min-width: 0;
    }

    /* Core Deploy Card */
    .deploy-container {
      width: 100%;
      background: #ffffff;
      border: 2px solid var(--ink);
      border-radius: 24px;
      padding: clamp(24px, 4vw, 40px);
      box-shadow: 6px 6px 0 var(--ink);
    }

    .kicker {
      font-family: 'Geist Mono', monospace;
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      font-weight: 700;
      color: var(--ink-mute);
      margin-bottom: 8px;
    }
    .deploy-h1 {
      font-size: clamp(28px, 4.2vw, 36px);
      font-weight: 800;
      letter-spacing: -0.025em;
      line-height: 1.1;
      margin-bottom: 12px;
    }
    .deploy-sub {
      font-size: 15px;
      color: var(--ink-soft);
      line-height: 1.5;
      margin-bottom: 30px;
    }

    /* Form Steps & Tutorials */
    .form-step {
      margin-bottom: 28px;
      padding-bottom: 24px;
      border-bottom: 1px dashed var(--border);
    }
    .form-step:last-of-type {
      border-bottom: none;
      padding-bottom: 12px;
    }
    .step-header {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      margin-bottom: 8px;
    }
    .step-title {
      font-size: 15px;
      font-weight: 800;
      color: var(--ink);
    }
    .badge-req {
      font-family: 'Geist Mono', monospace;
      font-size: 10px;
      font-weight: 700;
      background: var(--bg);
      border: 1px solid var(--ink);
      padding: 2px 6px;
      border-radius: 4px;
      text-transform: uppercase;
    }
    .badge-opt {
      font-family: 'Geist Mono', monospace;
      font-size: 10px;
      font-weight: 700;
      background: #eee;
      border: 1px solid var(--border-dark);
      padding: 2px 6px;
      border-radius: 4px;
      text-transform: uppercase;
      color: var(--ink-mute);
    }

    .input-box {
      position: relative;
      display: flex;
      align-items: center;
    }
    .app-input {
      width: 100%;
      background: #ffffff;
      border: 1.5px solid var(--border-dark);
      border-radius: 12px;
      padding: 12px 14px;
      font-family: inherit;
      font-size: 14px;
      color: var(--ink);
      outline: none;
      transition: all 0.15s ease;
    }
    .app-input:focus {
      border-color: var(--ink);
      box-shadow: 2px 2px 0 var(--ink);
    }
    .btn-reveal {
      position: absolute;
      right: 12px;
      background: none;
      border: none;
      cursor: pointer;
      color: var(--ink-mute);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 4px;
    }
    .btn-reveal:hover { color: var(--ink); }

    /* Interactive Tutorial Callout */
    .tutorial-card {
      background: #f7f6ed;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px 16px;
      margin-top: 10px;
      font-size: 13px;
      line-height: 1.5;
    }
    .tutorial-heading {
      font-weight: 700;
      margin-bottom: 4px;
      color: var(--ink);
    }
    .tutorial-desc {
      color: var(--ink-soft);
    }
    .tutorial-desc a {
      color: var(--ink);
      font-weight: 700;
      text-decoration: underline;
    }
    .code-sample {
      font-family: 'Geist Mono', monospace;
      font-size: 11px;
      background: rgba(0,0,0,0.04);
      padding: 2px 6px;
      border-radius: 4px;
      display: inline-block;
      margin-top: 4px;
      color: var(--ink);
    }

    /* Toggles & Preferences */
    .pref-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-top: 20px;
      margin-bottom: 28px;
    }
    .switch {
      position: relative;
      display: inline-block;
      width: 44px;
      height: 24px;
      flex-shrink: 0;
    }
    .switch input { opacity: 0; width: 0; height: 0; }
    .slider {
      position: absolute;
      cursor: pointer;
      top: 0; left: 0; right: 0; bottom: 0;
      background-color: #e5e5dc;
      border: 1.5px solid var(--ink);
      transition: .2s;
      border-radius: 34px;
    }
    .slider:before {
      position: absolute;
      content: "";
      height: 16px;
      width: 16px;
      left: 3px;
      bottom: 2.5px;
      background-color: var(--ink);
      transition: .2s;
      border-radius: 50%;
    }
    input:checked + .slider {
      background-color: var(--bg);
    }
    input:checked + .slider:before {
      transform: translateX(18px);
    }

    /* Submit Button */
    .btn-submit {
      width: 100%;
      background: var(--ink);
      color: var(--accent);
      border: 2px solid var(--ink);
      border-radius: 100px;
      padding: 16px 28px;
      font-family: inherit;
      font-size: 16px;
      font-weight: 800;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      box-shadow: 4px 4px 0 var(--ink);
      transition: all 0.2s cubic-bezier(.34, 1.56, .64, 1);
    }
    .btn-submit:hover {
      transform: scale(1.02);
      box-shadow: 6px 6px 0 var(--ink);
    }
    .btn-submit:disabled {
      opacity: 0.5;
      cursor: not-allowed;
      transform: none;
    }

    /* Results Card */
    .result-box {
      display: none;
      margin-top: 28px;
      background: var(--bg);
      border: 2px solid var(--ink);
      border-radius: 20px;
      padding: 28px;
      box-shadow: 6px 6px 0 var(--ink);
    }

    .stream-card {
      background: #ffffff;
      border: 1.5px solid var(--ink);
      border-radius: 14px;
      padding: 18px;
      margin: 18px 0;
    }

    /* Dashboard Fleet */
    .fleet-box {
      display: none;
      background: #f7f6ed;
      border: 1.5px solid var(--ink);
      border-radius: 18px;
      padding: 20px;
    }
    .btn-google-auth-nav {
      background: #ffffff;
      border: 1.5px solid var(--ink);
      border-radius: 100px;
      padding: 7px 16px;
      font-family: inherit;
      font-size: 13px;
      font-weight: 700;
      color: var(--ink);
      display: inline-flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      box-shadow: 2px 2px 0 var(--ink);
      transition: all 0.15s ease;
      text-decoration: none;
    }
    .btn-google-auth-nav:hover {
      background: #f7f6ed;
      box-shadow: 3px 3px 0 var(--ink);
      transform: translate(-1px, -1px);
    }
    .auth-banner {
      background: #ffffff;
      border: 1.5px solid var(--ink);
      border-radius: 18px;
      padding: 16px 20px;
      box-shadow: 4px 4px 0 var(--ink);
      font-size: 14px;
    }

    /* ---------------- RIGHT SIDEBAR STYLES ---------------- */
    .deploy-sidebar {
      display: flex;
      flex-direction: column;
      gap: 20px;
      position: sticky;
      top: 24px;
    }
    .sidebar-card {
      background: #ffffff;
      border: 2px solid var(--ink);
      border-radius: 20px;
      padding: 20px;
      box-shadow: 4px 4px 0 var(--ink);
    }
    .sidebar-card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
      padding-bottom: 10px;
      border-bottom: 1px dashed var(--border);
    }
    .sidebar-card-title {
      font-size: 14px;
      font-weight: 800;
      color: var(--ink);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .btn-sidebar-refresh {
      background: var(--bg);
      border: 1px solid var(--ink);
      border-radius: 6px;
      font-family: 'Geist Mono', monospace;
      font-size: 11px;
      font-weight: 700;
      padding: 4px 8px;
      cursor: pointer;
      color: var(--ink);
    }
    .btn-sidebar-refresh:hover { background: #ffffff; }
    .btn-sidebar-sync {
      background: var(--ink);
      color: var(--accent);
      border: none;
      border-radius: 6px;
      font-family: 'Geist Mono', monospace;
      font-size: 11px;
      font-weight: 700;
      padding: 6px 12px;
      cursor: pointer;
    }
    .btn-sidebar-sync:hover { opacity: 0.9; }

    /* Pulsing Status Dot */
    .status-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #27ae60;
      display: inline-block;
      box-shadow: 0 0 0 0 rgba(39, 174, 96, 0.7);
      animation: pulse-green 2s infinite;
    }
    @keyframes pulse-green {
      0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(39, 174, 96, 0.7); }
      70% { transform: scale(1); box-shadow: 0 0 0 6px rgba(39, 174, 96, 0); }
      100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(39, 174, 96, 0); }
    }
    .status-dot.offline {
      background: #95a5a6;
      animation: none;
      box-shadow: none;
    }
    .status-dot.checking {
      background: #f39c12;
      animation: pulse-yellow 1.5s infinite;
    }
    @keyframes pulse-yellow {
      0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(243, 156, 18, 0.7); }
      70% { transform: scale(1); box-shadow: 0 0 0 6px rgba(243, 156, 18, 0); }
      100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(243, 156, 18, 0); }
    }

    /* Quick Links */
    .quick-links-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .quick-link-item {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 9px 12px;
      border: 1px solid var(--border-dark);
      border-radius: 12px;
      background: #faf8f0;
      color: var(--ink);
      text-decoration: none;
      transition: all 0.15s ease;
    }
    .quick-link-item:hover {
      background: #ffffff;
      border-color: var(--ink);
      transform: translateX(2px);
    }
    .quick-link-icon {
      width: 28px;
      height: 28px;
      border-radius: 8px;
      background: var(--bg);
      border: 1px solid var(--ink);
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    .quick-link-content {
      flex: 1;
      min-width: 0;
    }
    .quick-link-name {
      font-size: 13px;
      font-weight: 700;
      color: var(--ink);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .quick-link-sub {
      font-size: 10px;
      color: var(--ink-mute);
    }
    .quick-link-arrow {
      font-family: 'Geist Mono', monospace;
      font-size: 12px;
      font-weight: 700;
      color: var(--ink-mute);
    }

    /* Cheat Sheet Commands */
    .cmd-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      padding: 5px 0;
      border-bottom: 1px dashed var(--border);
    }
    .cmd-row:last-child {
      border-bottom: none;
    }
    .cmd-row code {
      background: var(--bg);
      border: 1px solid var(--ink);
      border-radius: 4px;
      padding: 1px 6px;
      font-family: 'Geist Mono', monospace;
      font-size: 11px;
      font-weight: 700;
      color: var(--ink);
    }
    .cmd-row span {
      font-size: 11px;
      color: var(--ink-soft);
      text-align: right;
    }
  </style>
</head>
<body>

  <header class="deploy-nav">
    <a href="/" class="brand-link">zevray<span>*</span></a>
    <div style="display: flex; gap: 14px; align-items: center;">
      <a href="/" class="back-link">&larr; Home</a>
      
      <!-- Logged Out Google Auth Button in Nav -->
      <button type="button" class="btn-google-auth-nav" id="nav-login-google-btn" onclick="signInWithGoogle()" style="display:none;">
        <svg width="15" height="15" viewBox="0 0 24 24">
          <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
          <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
          <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"/>
          <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/>
        </svg>
        <span>Sign In with Google</span>
      </button>

      <!-- Logged In User Pill -->
      <div id="nav-user-badge" style="display:none; align-items:center; gap:8px; background:#fff; border:1.5px solid var(--ink); border-radius:100px; padding:4px 12px 4px 6px;">
        <img id="nav-user-avatar" src="" alt="" style="width:22px; height:22px; border-radius:50%; display:none; object-fit:cover;">
        <span id="nav-user-label" style="font-family:'Geist Mono',monospace; font-size:12px; font-weight:700;"></span>
        <button onclick="handleSignOut()" title="Sign Out" style="background:none; border:none; color:var(--ink-mute); cursor:pointer; font-size:11px; font-weight:700; padding-left:4px;">(Sign Out)</button>
      </div>
    </div>
  </header>

  <main style="max-width: 1160px; margin: 0 auto; width: 100%; padding: 0 16px;">
    <!-- Prompt Banner for Unauthenticated Visitors -->
    <div id="auth-prompt-banner" class="auth-banner" style="display:none; margin-bottom:24px;">
      <div style="display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap;">
        <div>
          <b>Save & sync your bot credentials</b> &bull; Sign in with Google so your tokens, room IDs, and deployed fleets are saved safely to your cloud account database.
        </div>
        <button type="button" class="btn-google-auth-nav" onclick="signInWithGoogle()">
          <svg width="15" height="15" viewBox="0 0 24 24">
            <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
            <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
            <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"/>
            <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/>
          </svg>
          <span>Continue with Google</span>
        </button>
      </div>
    </div>

    <!-- 2-Column Responsive Dashboard Layout -->
    <div class="deploy-grid-layout">
      <!-- Main Column: Deploy Form & Results -->
      <div class="deploy-main-col">
        <!-- Active Fleet if Logged In -->
        <div class="fleet-box" id="user-fleet-box">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
            <span style="font-weight:800; font-size:16px;">Your Active Highrise Bot Fleet</span>
            <button class="btn-reveal" onclick="loadUserBots()" style="font-family:'Geist Mono',monospace; font-size:12px; font-weight:700; color:var(--ink);">Refresh Fleet</button>
          </div>
          <div id="fleet-list">Loading bots...</div>
        </div>

        <!-- The 60-Second Setup Form Card -->
        <div class="deploy-container">
          <div class="kicker">( 60-Second Cloud Deployer )</div>
          <h1 class="deploy-h1">Configure & Launch Your Bot</h1>
          <p class="deploy-sub">
            Follow each step below. Your bot container will be created on your Render account and will connect to your room automatically.
          </p>

          <form id="deploy-form">
            <!-- Step 1: Highrise Token -->
            <div class="form-step">
              <div class="step-header">
                <span class="step-title">1. Highrise Bot API Token</span>
                <span class="badge-req">Required</span>
              </div>
              <div class="input-box">
                <input type="password" id="highrise-token" class="app-input" placeholder="e.g. 20d06551e88098d0b7c6..." required autocomplete="off">
                <button type="button" class="btn-reveal" onclick="toggleVisibility('highrise-token', this)" aria-label="Toggle token visibility">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                </button>
              </div>
              <div class="tutorial-card">
                <div class="tutorial-heading">How to get your Bot Token:</div>
                <div class="tutorial-desc">
                  1. Sign in to <a href="https://create.highrise.game" target="_blank">create.highrise.game</a> &rarr; <b>Bots & API Keys</b>.<br>
                  2. Click <b>Create Bot</b>, give it a name, and copy the 64-character token.<br>
                  <span class="code-sample">Example: 20d06551e88098d0b7c6a084de450abf...</span>
                </div>
              </div>
            </div>

            <!-- Step 2: Room ID or URL -->
            <div class="form-step">
              <div class="step-header">
                <span class="step-title">2. Highrise Room ID or Share Link</span>
                <span class="badge-req">Required</span>
              </div>
              <div class="input-box">
                <input type="text" id="room-id" class="app-input" placeholder="e.g. 64005ecfb3d3644258e0040e or room link" required>
              </div>
              <div class="tutorial-card">
                <div class="tutorial-heading">How to find your Room ID:</div>
                <div class="tutorial-desc">
                  Open your room in Highrise &rarr; tap the share icon &rarr; tap <b>Copy Link</b>.<br>
                  You can paste the full link directly or just the 24-character ID at the end.<br>
                  <span class="code-sample">Example: https://highrise.game/room/64005ecfb3d3644258e0040e</span>
                </div>
              </div>
            </div>

            <!-- Step 3: Room Owner -->
            <div class="form-step">
              <div class="step-header">
                <span class="step-title">3. Room Owner Username</span>
                <span class="badge-req">Required</span>
              </div>
              <div class="input-box">
                <input type="text" id="room-owner" class="app-input" placeholder="e.g. subhadipray99 (without @)" required>
              </div>
              <div class="tutorial-card">
                <div class="tutorial-heading">Who has admin rights:</div>
                <div class="tutorial-desc">
                  This user receives full owner commands (<code>!setpos</code>, <code>!setfloor</code>, <code>!fit</code>, <code>!copyfit</code>, <code>!top</code>).
                </div>
              </div>
            </div>

            <!-- Step 4: Room Title -->
            <div class="form-step">
              <div class="step-header">
                <span class="step-title">4. Room Title / Bot Display Name</span>
                <span class="badge-opt">Optional</span>
              </div>
              <div class="input-box">
                <input type="text" id="room-title" class="app-input" placeholder="e.g. The Neon Lounge">
              </div>
              <div class="tutorial-card">
                <div class="tutorial-desc">
                  Shown in chat greetings when players walk in: <i>"Welcome @guest to The Neon Lounge!"</i>
                </div>
              </div>
            </div>

            <!-- Step 5: Render API Key -->
            <div class="form-step">
              <div class="step-header">
                <span class="step-title">5. Render API Key</span>
                <span class="badge-req">Required for Cloud Hosting</span>
              </div>
              <div class="input-box">
                <input type="password" id="render-key" class="app-input" placeholder="e.g. rnd_a8f92b7c6..." required autocomplete="off">
                <button type="button" class="btn-reveal" onclick="toggleVisibility('render-key', this)" aria-label="Toggle Render key visibility">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                </button>
              </div>
              <div class="tutorial-card">
                <div class="tutorial-heading">How to get your free Render API Key:</div>
                <div class="tutorial-desc">
                  1. Go to <a href="https://dashboard.render.com" target="_blank">dashboard.render.com</a> &rarr; Profile (bottom left) &rarr; <b>Account Settings</b>.<br>
                  2. Scroll down to <b>API Keys</b> &rarr; click <b>Create API Key</b>.<br>
                  3. Paste the key here. It will deploy your container under Render's 100% free web service tier.
                </div>
              </div>
            </div>

            <!-- Step 6: Cron-Job.org API Key -->
            <div class="form-step">
              <div class="step-header">
                <span class="step-title">6. Cron-Job.org API Key</span>
                <span class="badge-opt">Optional &bull; Autonomous Keep-Alive</span>
              </div>
              <div class="input-box">
                <input type="password" id="cron-key" class="app-input" placeholder="Optional: e.g. cj_928172..." autocomplete="off">
                <button type="button" class="btn-reveal" onclick="toggleVisibility('cron-key', this)" aria-label="Toggle cron key visibility">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                </button>
              </div>
              <div class="tutorial-card">
                <div class="tutorial-heading">Keep-Alive Network:</div>
                <div class="tutorial-desc">
                  Leave this blank! Our Master Keep-Alive network automatically enrolls your bot and pings it every 10 minutes so Render never sleeps.
                </div>
              </div>
            </div>

            <!-- Cloud Database Sync Toggle -->
            <div class="pref-row" style="background:#f7f6ed; border:1.5px solid var(--border-dark); border-radius:14px; padding:16px;">
              <div>
                <div style="font-size:14px; font-weight:700; display:flex; align-items:center; gap:8px;">
                  <span>Save & sync credentials to Cloud Database</span>
                  <span style="font-family:'Geist Mono',monospace; font-size:10px; font-weight:700; background:var(--ink); color:var(--accent); padding:2px 6px; border-radius:4px;">SUPABASE</span>
                </div>
                <div style="font-size:12px; color:var(--ink-mute); margin-top:2px;">
                  Saves to your user account so your credentials and room settings persist across all your devices.
                </div>
              </div>
              <div style="display:flex; align-items:center; gap:10px;">
                <button type="button" onclick="saveConfigToDb()" class="btn-sidebar-sync" style="padding:6px 12px; font-size:11px;">Save Now</button>
                <label class="switch">
                  <input type="checkbox" id="save-db-pref" checked>
                  <span class="slider"></span>
                </label>
              </div>
            </div>

            <button type="submit" id="btn-submit-deploy" class="btn-submit">
              <span id="submit-text">Deploy 24/7 Bot to Render &rarr;</span>
            </button>
          </form>

          <!-- Status Alerts -->
          <div id="deploy-loading" style="display:none; margin-top:20px; font-weight:700; color:var(--ink); font-family:'Geist Mono',monospace; font-size:13px; text-align:center;">
            Connecting to Render cloud & building service container...
          </div>
          <div id="deploy-err" style="display:none; margin-top:20px; background:#fee2e2; border:1.5px solid var(--ink); border-radius:12px; padding:14px; color:#b91c1c; font-size:13px; font-weight:600;"></div>

          <!-- Result Card -->
          <div id="deploy-res" class="result-box">
            <div id="res-badge-kicker" style="font-family:'Geist Mono',monospace; font-size:12px; font-weight:700; margin-bottom:6px;">SUCCESS &bull; DEPLOYMENT INITIATED</div>
            <h2 id="res-title-h2" style="font-size:26px; font-weight:800; margin-bottom:8px;">Your Bot is Building on Render!</h2>
            <p id="res-desc-p" style="font-size:14px; color:var(--ink-soft); margin-bottom:16px;">
              Render is compiling your container. It will join your Highrise room in 60-90 seconds.
            </p>

            <!-- MP3 Stream Link -->
            <div class="stream-card">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <span style="font-weight:800; font-size:14px;">Highrise In-Room Radio (.mp3 URL)</span>
                <span style="font-family:'Geist Mono',monospace; font-size:10px; font-weight:700; background:var(--bg); border:1px solid var(--ink); padding:2px 6px; border-radius:4px;">PASTE INTO ROOM RADIO</span>
              </div>
              <div style="display:flex; gap:8px;">
                <input type="text" id="res-stream-input" readonly value="" class="app-input" style="font-family:'Geist Mono',monospace; font-weight:600; padding:10px 14px;">
                <button type="button" class="btn-submit" style="width:auto; padding:10px 20px; font-size:13px;" onclick="copyStream()">
                  <span id="copy-stream-lbl">Copy Link</span>
                </button>
              </div>
            </div>

            <div style="font-size:13px; margin-bottom:12px;">
              <b>Web Jukebox:</b> <a id="res-jukebox-link" href="#" target="_blank" style="color:var(--ink); font-weight:700;">Open /player Dashboard &rarr;</a>
            </div>

            <div style="background:#ffffff; border:1.5px solid var(--ink); border-radius:12px; padding:14px; font-size:12px; line-height:1.6;">
              <b>Quick Checklist:</b><br>
              1. Invite your bot into your Highrise room.<br>
              2. Room Settings &rarr; Privileges &rarr; Give your bot <b>Designer</b> rights.<br>
              3. Room Settings &rarr; Custom Radio Station &rarr; Paste your copied <b>.mp3 URL</b>.<br>
              4. Walk into your DJ booth and type <code>!setpos</code> in chat!
            </div>
          </div>
        </div>
      </div>

      <!-- Right Column: Sidebar (Live Bot Status, Quick Links, Cheat Sheet) -->
      <aside class="deploy-sidebar">
        <!-- Card 1: Bot System Status -->
        <div class="sidebar-card">
          <div class="sidebar-card-header">
            <span class="sidebar-card-title">
              <span class="status-dot offline" id="status-dot"></span>
              Live Bot Status
            </span>
            <button type="button" class="btn-sidebar-refresh" onclick="refreshBotStatus()" title="Refresh Status">Refresh</button>
          </div>

          <div id="bot-status-container">
            <div id="bot-status-idle" style="font-size:13px; color:var(--ink-soft); line-height:1.5;">
              <div style="font-weight:700; color:var(--ink); margin-bottom:4px;" id="bot-status-title">Ready to Deploy</div>
              <p id="bot-status-desc">No active bot instance registered yet. Fill in the form on the left to deploy your bot in 60 seconds.</p>
            </div>

            <div id="bot-status-active" style="display:none;">
              <div style="background:#f7f6ed; border:1px solid var(--border-dark); border-radius:12px; padding:12px; margin-bottom:12px;">
                <div style="font-weight:800; font-size:14px;" id="active-bot-name">Highrise Bot</div>
                <div style="font-size:12px; color:var(--ink-soft); margin-top:2px;" id="active-bot-room">Room: --</div>
                <div style="font-family:'Geist Mono',monospace; font-size:11px; color:#27ae60; font-weight:700; margin-top:6px;" id="active-bot-ping">
                  Latency: --ms &bull; Operational
                </div>
              </div>

              <div style="display:flex; flex-direction:column; gap:6px; font-size:12px;">
                <div style="display:flex; justify-content:space-between;">
                  <span style="color:var(--ink-mute);">Uptime Pinger:</span>
                  <span style="font-weight:700; color:#27ae60;">Active (Every 10m)</span>
                </div>
                <div style="display:flex; justify-content:space-between;">
                  <span style="color:var(--ink-mute);">Radio Stream:</span>
                  <span style="font-weight:700;">MP3 Live</span>
                </div>
                <div style="display:flex; justify-content:space-between;">
                  <span style="color:var(--ink-mute);">Cloud Database:</span>
                  <span style="font-weight:700; color:#27ae60;">Connected</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Card 2: Quick Links -->
        <div class="sidebar-card">
          <div class="sidebar-card-header">
            <span class="sidebar-card-title">Quick Links</span>
            <span style="font-family:'Geist Mono',monospace; font-size:10px; font-weight:700; text-transform:uppercase; color:var(--ink-mute);">Portals</span>
          </div>

          <div class="quick-links-list">
            <a href="/player" target="_blank" class="quick-link-item">
              <div class="quick-link-icon">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polygon points="10 8 16 12 10 16 10 8"/></svg>
              </div>
              <div class="quick-link-content">
                <div class="quick-link-name">Live Jukebox Player</div>
                <div class="quick-link-sub">Queue music & listen live</div>
              </div>
              <div class="quick-link-arrow">&rarr;</div>
            </a>

            <a href="https://create.highrise.game" target="_blank" class="quick-link-item">
              <div class="quick-link-icon">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
              </div>
              <div class="quick-link-content">
                <div class="quick-link-name">Highrise Developer Portal</div>
                <div class="quick-link-sub">Manage Bot API keys</div>
              </div>
              <div class="quick-link-arrow">&rarr;</div>
            </a>

            <a id="quick-room-link" href="#" target="_blank" class="quick-link-item" style="display:none;">
              <div class="quick-link-icon">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
              </div>
              <div class="quick-link-content">
                <div class="quick-link-name">Open Highrise Room</div>
                <div class="quick-link-sub">Join directly in browser or app</div>
              </div>
              <div class="quick-link-arrow">&rarr;</div>
            </a>

            <a href="https://dashboard.render.com" target="_blank" class="quick-link-item">
              <div class="quick-link-icon">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
              </div>
              <div class="quick-link-content">
                <div class="quick-link-name">Render Dashboard</div>
                <div class="quick-link-sub">View container logs & specs</div>
              </div>
              <div class="quick-link-arrow">&rarr;</div>
            </a>

            <a href="https://console.cron-job.org" target="_blank" class="quick-link-item">
              <div class="quick-link-icon">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
              </div>
              <div class="quick-link-content">
                <div class="quick-link-name">Cron-Job.org Monitor</div>
                <div class="quick-link-sub">Inspect keep-alive schedule</div>
              </div>
              <div class="quick-link-arrow">&rarr;</div>
            </a>

            <button type="button" class="quick-link-item" onclick="copyStream()" style="width:100%; text-align:left; background:none; border:none; cursor:pointer;">
              <div class="quick-link-icon">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
              </div>
              <div class="quick-link-content">
                <div class="quick-link-name" id="sidebar-copy-stream-txt">Copy Radio Stream URL</div>
                <div class="quick-link-sub">For Highrise Room Radio</div>
              </div>
              <div class="quick-link-arrow">&plus;</div>
            </button>
          </div>
        </div>

        <!-- Card 3: Cloud Database Sync Status -->
        <div class="sidebar-card">
          <div class="sidebar-card-header">
            <span class="sidebar-card-title">Cloud Database</span>
            <span style="font-family:'Geist Mono',monospace; font-size:10px; font-weight:700; color:#27ae60;">SUPABASE</span>
          </div>
          <div style="font-size:12px; color:var(--ink-soft); line-height:1.5;">
            Credentials & configurations are synced to your Supabase account so you never lose them.
          </div>
          <div style="display:flex; align-items:center; justify-content:space-between; margin-top:12px;">
            <span id="cloud-sync-status" style="font-family:'Geist Mono',monospace; font-size:11px; font-weight:700; color:var(--ink);">Checking sync...</span>
            <button type="button" onclick="saveConfigToDb()" class="btn-sidebar-sync">Sync Now</button>
          </div>
        </div>

        <!-- Card 4: In-Game Commands Cheat Sheet -->
        <div class="sidebar-card">
          <div class="sidebar-card-header">
            <span class="sidebar-card-title">In-Game Commands</span>
            <span style="font-family:'Geist Mono',monospace; font-size:10px; font-weight:700; color:var(--ink-mute);">CHEATSHEET</span>
          </div>
          <div style="display:flex; flex-direction:column; gap:8px; font-size:12px;">
            <div class="cmd-row"><code>!setpos</code><span>Lock DJ booth coordinates</span></div>
            <div class="cmd-row"><code>/play &lt;song&gt;</code><span>Request song to queue</span></div>
            <div class="cmd-row"><code>/emotes</code><span>View 68+ emotes catalog</span></div>
            <div class="cmd-row"><code>/emote &lt;name&gt;</code><span>Loop emote non-stop</span></div>
            <div class="cmd-row"><code>/stop</code><span>Stop active emote loop</span></div>
            <div class="cmd-row"><code>/f1, /f2</code><span>Teleport to floor spawns</span></div>
            <div class="cmd-row"><code>!fit</code><span>Save current bot outfit</span></div>
          </div>
        </div>
      </aside>
    </div>
  </main>

  <script>
    const SUPABASE_URL = "__SUPABASE_URL__";
    const SUPABASE_ANON_KEY = "__SUPABASE_ANON_KEY__";
    const sb = window.supabase ? window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY) : null;
    let currentUser = null;
    let latestDeployedBot = null;

    // Reveal/Hide Eye toggles
    const SVG_EYE = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>`;
    const SVG_EYE_SLASH = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><line x1="2" y1="2" x2="22" y2="22"/></svg>`;

    function toggleVisibility(inputId, btn) {
      const el = document.getElementById(inputId);
      if (el.type === 'password') {
        el.type = 'text';
        btn.innerHTML = SVG_EYE_SLASH;
      } else {
        el.type = 'password';
        btn.innerHTML = SVG_EYE;
      }
    }

    // Dynamic Room Link in Quick Links
    const roomInput = document.getElementById('room-id');
    roomInput.addEventListener('input', () => {
      const match = roomInput.value.match(/[0-9a-f]{24}/i);
      updateRoomQuickLink(match ? match[0] : null);
    });

    function updateRoomQuickLink(roomId) {
      const linkEl = document.getElementById('quick-room-link');
      if (!linkEl) return;
      if (roomId) {
        linkEl.href = `https://highrise.game/room/${roomId}`;
        linkEl.style.display = 'flex';
      } else {
        linkEl.style.display = 'none';
      }
    }

    // ---------------- DATABASE CREDENTIAL PERSISTENCE ----------------
    async function loadUserConfigFromDb(userId) {
      const statusEl = document.getElementById('cloud-sync-status');
      if (statusEl) statusEl.innerText = 'Syncing...';
      try {
        const res = await fetch(`/api/user-config?user_id=${userId}`);
        const data = await res.json();
        if (data.success && data.config) {
          const c = data.config;
          if (c.highrise_token) document.getElementById('highrise-token').value = c.highrise_token;
          if (c.room_id) {
            document.getElementById('room-id').value = c.room_id;
            updateRoomQuickLink(c.room_id);
          }
          if (c.room_owner) document.getElementById('room-owner').value = c.room_owner;
          if (c.room_title) document.getElementById('room-title').value = c.room_title;
          if (c.render_key) document.getElementById('render-key').value = c.render_key;
          if (c.cron_key) document.getElementById('cron-key').value = c.cron_key;

          if (statusEl) statusEl.innerText = 'Synced with Cloud';
          return;
        }
      } catch (e) {
        console.debug('Cloud config load exception:', e);
      }
      if (statusEl) statusEl.innerText = 'Account ready';
    }

    async function saveConfigToDb() {
      if (!currentUser) {
        alert('Please sign in with Google first to save your credentials to your personal cloud database.');
        return;
      }
      const payload = {
        highrise_token: document.getElementById('highrise-token').value.trim(),
        room_id: document.getElementById('room-id').value.trim(),
        room_owner: document.getElementById('room-owner').value.trim(),
        room_title: document.getElementById('room-title').value.trim(),
        render_key: document.getElementById('render-key').value.trim(),
        cron_key: document.getElementById('cron-key').value.trim()
      };

      const statusEl = document.getElementById('cloud-sync-status');
      if (statusEl) statusEl.innerText = 'Saving to database...';

      try {
        const res = await fetch('/api/user-config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: currentUser.id, config: payload })
        });
        const d = await res.json();
        if (d.success) {
          if (statusEl) statusEl.innerText = 'Saved to Supabase';
          alert('Configuration saved to your Supabase database!');
        } else {
          if (statusEl) statusEl.innerText = 'Save error';
          alert('Could not save to database: ' + (d.error || 'Unknown error'));
        }
      } catch (err) {
        if (statusEl) statusEl.innerText = 'Network error';
        alert('Error saving config: ' + err.message);
      }
    }

    // Fallback localStorage for unauthenticated users
    const STORE_KEY = 'zevray_creds';
    function loadSavedLocal() {
      try {
        const val = localStorage.getItem(STORE_KEY);
        if (val) {
          const d = JSON.parse(val);
          if (d.highrise_token && !document.getElementById('highrise-token').value) document.getElementById('highrise-token').value = d.highrise_token;
          if (d.room_id && !document.getElementById('room-id').value) {
            document.getElementById('room-id').value = d.room_id;
            updateRoomQuickLink(d.room_id);
          }
          if (d.room_owner && !document.getElementById('room-owner').value) document.getElementById('room-owner').value = d.room_owner;
          if (d.room_title && !document.getElementById('room-title').value) document.getElementById('room-title').value = d.room_title;
          if (d.render_key && !document.getElementById('render-key').value) document.getElementById('render-key').value = d.render_key;
          if (d.cron_key && !document.getElementById('cron-key').value) document.getElementById('cron-key').value = d.cron_key;
        }
      } catch (e) {}
    }

    function saveLocalFallback(payload) {
      try { localStorage.setItem(STORE_KEY, JSON.stringify(payload)); } catch (e) {}
    }

    // ---------------- LIVE BOT STATUS MONITOR ----------------
    async function refreshBotStatus() {
      const dot = document.getElementById('status-dot');
      dot.className = 'status-dot checking';

      let targetUrl = '';
      let botInfo = null;

      if (latestDeployedBot) {
        botInfo = latestDeployedBot;
        targetUrl = botInfo.health_url || (botInfo.service_url ? `${botInfo.service_url}/health` : '');
      } else {
        // Check local server engine status
        targetUrl = window.location.origin + '/health';
      }

      try {
        const res = await fetch(`/api/bot-health-check?url=${encodeURIComponent(targetUrl)}`);
        const data = await res.json();
        
        if (data.online) {
          dot.className = 'status-dot';
          if (botInfo) {
            document.getElementById('bot-status-idle').style.display = 'none';
            document.getElementById('bot-status-active').style.display = 'block';
            document.getElementById('active-bot-name').innerText = botInfo.room_title || 'Highrise Bot';
            document.getElementById('active-bot-room').innerText = `Room: ${botInfo.room_id || '--'}`;
            document.getElementById('active-bot-ping').innerText = `Latency: ${data.latency_ms}ms • Operational`;
          } else {
            document.getElementById('bot-status-title').innerText = 'Engine Online & Ready';
            document.getElementById('bot-status-desc').innerText = `Cloud engine healthy (${data.latency_ms}ms). Fill in the form on the left to deploy your bot container.`;
          }
        } else {
          dot.className = 'status-dot offline';
          if (botInfo) {
            document.getElementById('active-bot-ping').innerText = `Building / Booting • Retrying...`;
          }
        }
      } catch (e) {
        dot.className = 'status-dot offline';
      }
    }

    // ---------------- DEPLOY FORM SUBMISSION ----------------
    const form = document.getElementById('deploy-form');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      document.getElementById('deploy-err').style.display = 'none';
      document.getElementById('deploy-res').style.display = 'none';
      document.getElementById('deploy-loading').style.display = 'block';
      document.getElementById('btn-submit-deploy').disabled = true;

      const payload = {
        highrise_token: document.getElementById('highrise-token').value.trim(),
        room_id: document.getElementById('room-id').value.trim(),
        room_owner: document.getElementById('room-owner').value.trim(),
        room_title: document.getElementById('room-title').value.trim(),
        render_api_key: document.getElementById('render-key').value.trim(),
        cron_api_key: document.getElementById('cron-key').value.trim(),
        user_id: currentUser ? currentUser.id : ''
      };

      // Save to database if checkbox is enabled and user is logged in
      if (document.getElementById('save-db-pref').checked) {
        if (currentUser) {
          saveConfigToDb();
        } else {
          saveLocalFallback(payload);
        }
      }

      try {
        const res = await fetch('/api/deploy-render', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        document.getElementById('deploy-loading').style.display = 'none';
        document.getElementById('btn-submit-deploy').disabled = false;

        if (!res.ok || !data.success) {
          document.getElementById('deploy-err').innerText = data.error || 'Deployment failed. Check credentials.';
          document.getElementById('deploy-err').style.display = 'block';
          return;
        }

        if (data.already_existed) {
          const badgeEl = document.getElementById('res-badge-kicker');
          if (badgeEl) badgeEl.innerText = 'CONNECTED • EXISTING BOT FOUND';
          const titleEl = document.getElementById('res-title-h2');
          if (titleEl) titleEl.innerText = 'Your Bot is Live on Render!';
          const descEl = document.getElementById('res-desc-p');
          if (descEl) descEl.innerText = data.message || 'Connected to your existing service on Render. Credentials and 24/7 keep-alive network synced.';
        } else {
          const badgeEl = document.getElementById('res-badge-kicker');
          if (badgeEl) badgeEl.innerText = 'SUCCESS • DEPLOYMENT INITIATED';
          const titleEl = document.getElementById('res-title-h2');
          if (titleEl) titleEl.innerText = 'Your Bot is Building on Render!';
          const descEl = document.getElementById('res-desc-p');
          if (descEl) descEl.innerText = 'Render is compiling your container. It will join your Highrise room in 60-90 seconds.';
        }

        document.getElementById('res-stream-input').value = data.radio_url;
        document.getElementById('res-jukebox-link').href = data.player_url;
        document.getElementById('deploy-res').style.display = 'block';
        document.getElementById('deploy-res').scrollIntoView({ behavior: 'smooth' });

        // Update active bot in sidebar
        latestDeployedBot = {
          room_title: payload.room_title || 'Highrise Bot',
          room_id: payload.room_id,
          room_owner: payload.room_owner,
          service_url: data.service_url,
          health_url: data.health_url
        };
        refreshBotStatus();

        if (currentUser) loadUserBots();
      } catch (err) {
        document.getElementById('deploy-loading').style.display = 'none';
        document.getElementById('btn-submit-deploy').disabled = false;
        document.getElementById('deploy-err').innerText = 'Network error: ' + err.message;
        document.getElementById('deploy-err').style.display = 'block';
      }
    });

    function copyStream() {
      const val = document.getElementById('res-stream-input').value || (window.location.origin + '/radio.mp3');
      navigator.clipboard.writeText(val).then(() => {
        const lbl = document.getElementById('copy-stream-lbl');
        if (lbl) lbl.innerText = 'Copied!';
        const sideLbl = document.getElementById('sidebar-copy-stream-txt');
        if (sideLbl) sideLbl.innerText = 'Copied Radio URL!';
        setTimeout(() => {
          if (lbl) lbl.innerText = 'Copy Link';
          if (sideLbl) sideLbl.innerText = 'Copy Radio Stream URL';
        }, 2000);
      });
    }

    async function loadUserBots() {
      if (!currentUser) return;
      const listEl = document.getElementById('fleet-list');
      try {
        const res = await fetch(`/api/user-bots?user_id=${currentUser.id}`);
        const data = await res.json();
        if (!data.success || !data.bots || data.bots.length === 0) {
          listEl.innerHTML = '<span style="font-size:13px; color:var(--ink-mute);">No bots deployed yet on your account.</span>';
          return;
        }
        let html = '';
        data.bots.forEach((b, idx) => {
          if (idx === 0 && !latestDeployedBot) {
            latestDeployedBot = b;
            refreshBotStatus();
          }
          const stream = b.radio_stream_url || `${b.render_service_url}/radio.mp3`;
          html += `
            <div style="background:#fff; border:1px solid var(--border-dark); border-radius:10px; padding:10px 14px; margin-top:8px; display:flex; justify-content:space-between; align-items:center;">
              <div>
                <b>${b.room_title || 'Highrise Bot'}</b> &bull; Room <code>${b.room_id}</code> &bull; @${b.room_owner}
              </div>
              <div style="display:flex; gap:8px;">
                <button onclick="navigator.clipboard.writeText('${stream}'); alert('Copied stream link!');" style="background:var(--bg); border:1px solid var(--ink); border-radius:6px; font-weight:700; font-size:11px; padding:4px 8px; cursor:pointer;">Copy Radio URL</button>
                <a href="${b.render_service_url}/player" target="_blank" style="font-size:11px; font-weight:700; color:var(--ink);">Open Player</a>
              </div>
            </div>
          `;
        });
        listEl.innerHTML = html;
      } catch (e) {
        listEl.innerHTML = 'Error loading bots: ' + e.message;
      }
    }

    async function signInWithGoogle() {
      if (!sb) {
        alert("Authentication client not initialized.");
        return;
      }
      try {
        const { data, error } = await sb.auth.signInWithOAuth({
          provider: 'google',
          options: {
            redirectTo: window.location.origin + '/deploy'
          }
        });
        if (error) throw error;
      } catch (err) {
        alert("Google Sign-In failed: " + err.message);
      }
    }

    async function handleSignOut() {
      if (sb) {
        await sb.auth.signOut();
        window.location.reload();
      }
    }

    function renderUserSession(user) {
      currentUser = user;
      const meta = user.user_metadata || {};
      const name = meta.full_name || meta.name || user.email.split('@')[0];
      const avatar = meta.avatar_url || meta.picture || '';

      const loginBtn = document.getElementById('nav-login-google-btn');
      if (loginBtn) loginBtn.style.display = 'none';

      const badge = document.getElementById('nav-user-badge');
      if (badge) badge.style.display = 'inline-flex';

      const avatarEl = document.getElementById('nav-user-avatar');
      if (avatarEl) {
        if (avatar) {
          avatarEl.src = avatar;
          avatarEl.style.display = 'inline-block';
        } else {
          avatarEl.style.display = 'none';
        }
      }

      const labelEl = document.getElementById('nav-user-label');
      if (labelEl) labelEl.innerText = name;

      const banner = document.getElementById('auth-prompt-banner');
      if (banner) banner.style.display = 'none';

      const fleetBox = document.getElementById('user-fleet-box');
      if (fleetBox) fleetBox.style.display = 'block';

      // Load bot fleet and cloud config
      loadUserBots();
      loadUserConfigFromDb(user.id);
    }

    function renderLoggedOut() {
      currentUser = null;
      const loginBtn = document.getElementById('nav-login-google-btn');
      if (loginBtn) loginBtn.style.display = 'inline-flex';

      const badge = document.getElementById('nav-user-badge');
      if (badge) badge.style.display = 'none';

      const banner = document.getElementById('auth-prompt-banner');
      if (banner) banner.style.display = 'block';

      const fleetBox = document.getElementById('user-fleet-box');
      if (fleetBox) fleetBox.style.display = 'none';

      const statusEl = document.getElementById('cloud-sync-status');
      if (statusEl) statusEl.innerText = 'Sign in to sync';

      loadSavedLocal();
      refreshBotStatus();
    }

    async function initUser() {
      if (!sb) {
        renderLoggedOut();
        return;
      }

      const { data: { session } } = await sb.auth.getSession();
      if (session && session.user) {
        renderUserSession(session.user);
      } else {
        renderLoggedOut();
      }

      sb.auth.onAuthStateChange((event, session) => {
        if (session && session.user) {
          renderUserSession(session.user);
        } else {
          renderLoggedOut();
        }
      });

      refreshBotStatus();
    }
    initUser();
  </script>
</body>
</html>
"""

async def handle_landing_page(request: web.Request) -> web.Response:
    """Serve the Cloudstudio-inspired high-craft landing page on root /."""
    rendered = LANDING_HTML.replace("__SUPABASE_URL__", config.SUPABASE_URL).replace("__SUPABASE_ANON_KEY__", config.SUPABASE_ANON_KEY)
    return web.Response(
        text=rendered,
        content_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


async def handle_deploy_page(request: web.Request) -> web.Response:
    """Serve the dedicated 60-second cloud deployer and bot setup form on /deploy."""
    rendered = DEPLOY_HTML.replace("__SUPABASE_URL__", config.SUPABASE_URL).replace("__SUPABASE_ANON_KEY__", config.SUPABASE_ANON_KEY)
    return web.Response(
        text=rendered,
        content_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


async def handle_portal_page(request: web.Request) -> web.Response:
    """Backwards compatibility for /portal redirecting to /deploy."""
    return await handle_deploy_page(request)


async def handle_user_config_api(request: web.Request) -> web.Response:
    """API endpoint to get or save user bot credentials in Supabase database."""
    if request.method == "GET":
        user_id = request.query.get("user_id", "").strip()
        if not user_id:
            return web.json_response({"success": False, "error": "user_id is required"}, status=400)
        try:
            cfg = await db.get_user_config(user_id)
            return web.json_response({"success": True, "config": cfg or {}})
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=500)

    elif request.method == "POST":
        try:
            data = await request.json()
            user_id = str(data.get("user_id", "")).strip()
            config_data = data.get("config", {})
            if not user_id:
                return web.json_response({"success": False, "error": "user_id is required"}, status=400)
            success = await db.save_user_config(user_id, config_data)
            return web.json_response({"success": success})
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_bot_health_check(request: web.Request) -> web.Response:
    """Proxy health check to verify live connectivity and latency to a bot endpoint."""
    url = request.query.get("url", "").strip()
    if not url or url.startswith("/") or "127.0.0.1" in url or "localhost" in url:
        # Check current server health directly
        try:
            h_resp = await handle_health(request)
            return web.json_response({
                "online": True,
                "latency_ms": 1,
                "status": 200,
                "data": {"status": "OK"}
            })
        except Exception as e:
            return web.json_response({
                "online": False,
                "latency_ms": 1,
                "error": str(e)
            })

    t0 = asyncio.get_event_loop().time()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                elapsed_ms = int((asyncio.get_event_loop().time() - t0) * 1000)
                if resp.status == 200:
                    try:
                        data = await resp.json()
                    except Exception:
                        data = {}
                    return web.json_response({
                        "online": True,
                        "latency_ms": elapsed_ms,
                        "status": resp.status,
                        "data": data
                    })
                else:
                    return web.json_response({
                        "online": False,
                        "latency_ms": elapsed_ms,
                        "status": resp.status
                    })
    except Exception as e:
        elapsed_ms = int((asyncio.get_event_loop().time() - t0) * 1000)
        return web.json_response({
            "online": False,
            "latency_ms": elapsed_ms,
            "error": str(e)
        })

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
                already_existed = False

                if resp.status not in (200, 201):
                    err_msg = resp_json.get("message") or str(resp_json)
                    # Handle duplicate/existing service gracefully
                    if "already in use" in err_msg.lower() or "already exists" in err_msg.lower() or resp.status == 409:
                        already_existed = True
                        logger.info(f"Render service '{slug}' already exists. Resolving existing service details...")
                        srv_id = ""
                        srv_name = slug
                        srv_url = f"https://{slug}.onrender.com"

                        # Query Render services list to find the exact service ID and URL
                        try:
                            async with session.get("https://api.render.com/v1/services?limit=100", headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as srv_list_resp:
                                if srv_list_resp.status == 200:
                                    srv_list_data = await srv_list_resp.json()
                                    for item in srv_list_data:
                                        svc = item.get("service") if isinstance(item, dict) and "service" in item else item
                                        if svc.get("name") == slug:
                                            srv_id = svc.get("id", "")
                                            srv_name = svc.get("name", slug)
                                            srv_url = svc.get("serviceDetails", {}).get("url") or f"https://{srv_name}.onrender.com"
                                            logger.info(f"Found existing Render service ID: {srv_id}, URL: {srv_url}")
                                            break
                        except Exception as srv_find_err:
                            logger.warning(f"Could not list services from Render: {srv_find_err}")
                    else:
                        return web.json_response({
                            "success": False,
                            "error": f"Render rejected service creation: {err_msg}"
                        }, status=400)
                else:
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
                        logger.info(f"Successfully synced env vars to Render service {srv_id}")
                    except Exception as ev_err:
                        logger.warning(f"Secondary env-vars sync notice: {ev_err}")

                    # If service already existed, trigger a redeploy so latest code/env vars apply
                    if already_existed:
                        try:
                            await session.post(
                                f"https://api.render.com/v1/services/{srv_id}/deploys",
                                headers=headers,
                                json={"clearCache": "do_not_clear"},
                                timeout=aiohttp.ClientTimeout(total=10),
                            )
                            logger.info(f"Triggered re-deploy for existing service {srv_name} ({srv_id})")
                        except Exception as dep_err:
                            logger.debug(f"Redeploy trigger notice: {dep_err}")

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
                    "already_existed": already_existed,
                    "message": f"Connected to existing Render service '{srv_name}'. Bot credentials and keep-alive updated!" if already_existed else "Bot container created successfully on Render."
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
    app.router.add_get("/deploy", handle_deploy_page)
    app.router.add_post("/api/deploy-render", handle_deploy_render)
    app.router.add_get("/api/user-bots", handle_user_bots_api)
    app.router.add_get("/api/user-config", handle_user_config_api)
    app.router.add_post("/api/user-config", handle_user_config_api)
    app.router.add_get("/api/bot-health-check", handle_bot_health_check)
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
