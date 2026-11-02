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
  <title>ZevRay &bull; Highrise 24/7 DJ Bot Cloud Deployer</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <!-- Supabase JS Client v2 -->
  <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
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
      padding: 20px 16px 64px;
    }
    .top-nav {
      width: 100%;
      max-width: 720px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px 18px;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.08);
      backdrop-filter: blur(12px);
      border-radius: 16px;
      margin-bottom: 24px;
    }
    .nav-brand {
      display: flex;
      align-items: center;
      gap: 10px;
      text-decoration: none;
      color: #fff;
    }
    .brand-logo {
      font-size: 22px;
      background: linear-gradient(135deg, #a855f7 0%, #ec4899 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .brand-title {
      font-size: 17px;
      font-weight: 800;
      letter-spacing: -0.02em;
    }
    .brand-sub {
      font-size: 11px;
      color: #9ca3af;
    }
    .nav-actions {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .btn-nav-outline {
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid rgba(255, 255, 255, 0.15);
      color: #e5e7eb;
      padding: 7px 14px;
      border-radius: 10px;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
      font-family: inherit;
      transition: all 0.2s ease;
    }
    .btn-nav-outline:hover {
      background: rgba(255, 255, 255, 0.12);
      border-color: rgba(255, 255, 255, 0.25);
    }
    .btn-nav-primary {
      background: linear-gradient(135deg, #8b5cf6 0%, #d946ef 100%);
      border: none;
      color: #fff;
      padding: 7px 14px;
      border-radius: 10px;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
      font-family: inherit;
      box-shadow: 0 4px 12px rgba(139, 92, 246, 0.35);
      transition: all 0.2s ease;
    }
    .btn-nav-primary:hover {
      opacity: 0.9;
      transform: translateY(-1px);
    }
    .user-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(139, 92, 246, 0.15);
      border: 1px solid rgba(139, 92, 246, 0.35);
      color: #c084fc;
      padding: 5px 12px;
      border-radius: 9999px;
      font-size: 12px;
      font-weight: 700;
      max-width: 190px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .container {
      width: 100%;
      max-width: 720px;
    }
    .header {
      text-align: center;
      margin-bottom: 24px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(139, 92, 246, 0.2);
      border: 1px solid rgba(139, 92, 246, 0.4);
      color: #c084fc;
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 12px;
    }
    h1 {
      font-size: 26px;
      font-weight: 800;
      line-height: 1.25;
      margin-bottom: 10px;
      background: linear-gradient(135deg, #ffffff 0%, #c084fc 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .subtitle {
      font-size: 14px;
      color: #9ca3af;
      line-height: 1.5;
    }
    .features-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 10px;
      margin-bottom: 24px;
    }
    @media (max-width: 520px) {
      .features-grid { grid-template-columns: 1fr; }
      .top-nav { flex-direction: column; gap: 12px; }
    }
    .feature-item {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 12px;
      padding: 10px 14px;
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      font-weight: 600;
      color: #e5e7eb;
    }
    .card {
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.1);
      backdrop-filter: blur(16px);
      border-radius: 20px;
      padding: 26px;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.4);
      margin-bottom: 20px;
    }
    .dashboard-card {
      background: rgba(20, 15, 38, 0.7);
      border: 1px solid rgba(139, 92, 246, 0.3);
      border-radius: 20px;
      padding: 22px;
      margin-bottom: 24px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.3);
    }
    .dashboard-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
      padding-bottom: 12px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
    }
    .dashboard-title {
      font-size: 16px;
      font-weight: 800;
      color: #f3f4f6;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .bot-item-card {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.09);
      border-radius: 14px;
      padding: 14px 16px;
      margin-bottom: 10px;
      transition: border-color 0.2s ease;
    }
    .bot-item-card:hover {
      border-color: rgba(168, 85, 247, 0.4);
    }
    .bot-item-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 8px;
    }
    .bot-item-title {
      font-size: 14px;
      font-weight: 700;
      color: #fff;
    }
    .status-active-pill {
      font-size: 11px;
      font-weight: 700;
      color: #34d399;
      background: rgba(16, 185, 129, 0.15);
      border: 1px solid rgba(16, 185, 129, 0.35);
      padding: 3px 8px;
      border-radius: 9999px;
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }
    .bot-links-row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 10px;
    }
    .form-group {
      margin-bottom: 18px;
    }
    .form-label {
      display: block;
      font-size: 13px;
      font-weight: 700;
      color: #e5e7eb;
      margin-bottom: 6px;
    }
    .form-hint {
      font-size: 11px;
      color: #9ca3af;
      margin-top: 5px;
      line-height: 1.4;
    }
    .form-hint a {
      color: #c084fc;
      text-decoration: underline;
    }
    .form-input {
      width: 100%;
      background: rgba(13, 10, 26, 0.7);
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 12px;
      padding: 12px 14px;
      color: #ffffff;
      font-family: inherit;
      font-size: 14px;
      outline: none;
      transition: border-color 0.2s ease, box-shadow 0.2s ease;
    }
    .form-input:focus {
      border-color: #a855f7;
      box-shadow: 0 0 0 3px rgba(168, 85, 247, 0.25);
    }
    .btn-deploy {
      width: 100%;
      background: linear-gradient(135deg, #8b5cf6 0%, #ec4899 100%);
      color: #ffffff;
      border: none;
      border-radius: 14px;
      padding: 16px;
      font-family: inherit;
      font-size: 16px;
      font-weight: 800;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      box-shadow: 0 10px 25px rgba(139, 92, 246, 0.4);
      transition: all 0.2s ease;
      margin-top: 8px;
    }
    .btn-deploy:hover {
      transform: translateY(-2px);
      box-shadow: 0 14px 30px rgba(139, 92, 246, 0.5);
    }
    .btn-deploy:active {
      transform: translateY(0);
    }
    .btn-deploy:disabled {
      opacity: 0.6;
      cursor: not-allowed;
      transform: none;
    }
    .status-box {
      display: none;
      background: rgba(139, 92, 246, 0.15);
      border: 1px solid rgba(139, 92, 246, 0.3);
      color: #c084fc;
      border-radius: 14px;
      padding: 16px;
      margin-top: 18px;
      text-align: center;
      font-size: 14px;
      font-weight: 600;
      gap: 10px;
      align-items: center;
      justify-content: center;
    }
    .error-box {
      display: none;
      background: rgba(239, 68, 68, 0.15);
      border: 1px solid rgba(239, 68, 68, 0.4);
      color: #f87171;
      border-radius: 12px;
      padding: 14px;
      margin-top: 16px;
      font-size: 13px;
      line-height: 1.4;
    }
    .success-card {
      display: none;
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.35);
      border-radius: 20px;
      padding: 24px;
      margin-top: 24px;
    }
    .success-title {
      color: #34d399;
      font-size: 18px;
      font-weight: 800;
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 14px;
    }
    .info-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 10px 0;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      font-size: 13px;
      gap: 12px;
    }
    .info-label { color: #9ca3af; font-weight: 600; flex-shrink: 0; }
    .info-val a { color: #38bdf8; text-decoration: none; font-weight: 700; word-break: break-all; }
    .info-val a:hover { text-decoration: underline; }
    .copy-btn {
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 8px;
      padding: 4px 10px;
      font-size: 12px;
      color: #f3f4f6;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      transition: all 0.2s ease;
      font-family: inherit;
    }
    .copy-btn:hover {
      background: rgba(255, 255, 255, 0.15);
    }
    .stream-card {
      background: rgba(168, 85, 247, 0.12);
      border: 1.5px solid rgba(168, 85, 247, 0.4);
      border-radius: 14px;
      padding: 16px;
      margin: 16px 0;
    }
    .url-input-group {
      display: flex;
      gap: 8px;
      margin-top: 10px;
      align-items: center;
    }
    .url-input {
      flex: 1;
      background: rgba(0, 0, 0, 0.45);
      border: 1px solid rgba(255, 255, 255, 0.18);
      border-radius: 10px;
      padding: 10px 14px;
      color: #38bdf8;
      font-family: monospace;
      font-size: 13px;
      font-weight: 700;
      outline: none;
      word-break: break-all;
    }
    .btn-action-copy {
      background: linear-gradient(135deg, #9333ea 0%, #c026d3 100%);
      color: white;
      border: 1px solid rgba(255, 255, 255, 0.2);
      border-radius: 10px;
      padding: 10px 16px;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      white-space: nowrap;
      transition: all 0.2s ease;
    }
    .btn-action-copy:hover {
      opacity: 0.9;
      transform: translateY(-1px);
    }
    .setup-steps {
      margin-top: 18px;
      background: rgba(0, 0, 0, 0.3);
      border-radius: 14px;
      padding: 16px 18px;
    }
    .setup-steps h4 {
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 8px;
      color: #e5e7eb;
    }
    .setup-steps ol {
      margin-left: 20px;
      font-size: 12px;
      color: #9ca3af;
      line-height: 1.6;
    }
    .spinner {
      width: 18px;
      height: 18px;
      border: 3px solid rgba(255, 255, 255, 0.3);
      border-radius: 50%;
      border-top-color: #fff;
      animation: spin 0.8s linear infinite;
      display: inline-block;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* Auth Modal */
    .modal-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.75);
      backdrop-filter: blur(8px);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1000;
      padding: 16px;
    }
    .modal-card {
      background: #15102a;
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 20px;
      width: 100%;
      max-width: 400px;
      padding: 24px;
      box-shadow: 0 25px 50px rgba(0, 0, 0, 0.6);
      position: relative;
    }
    .modal-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
    }
    .modal-tabs {
      display: flex;
      gap: 6px;
      background: rgba(255, 255, 255, 0.05);
      padding: 4px;
      border-radius: 12px;
    }
    .tab-btn {
      background: none;
      border: none;
      color: #9ca3af;
      padding: 6px 14px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      font-family: inherit;
      transition: all 0.2s ease;
    }
    .tab-btn.active {
      background: rgba(139, 92, 246, 0.3);
      color: #fff;
    }
    .modal-close {
      background: none;
      border: none;
      color: #9ca3af;
      font-size: 18px;
      cursor: pointer;
      padding: 4px;
      line-height: 1;
    }
    .modal-close:hover { color: #fff; }
  </style>
</head>
<body>
  <!-- Top Navigation with Supabase Auth Status -->
  <header class="top-nav">
    <div class="nav-brand">
      <span class="brand-logo">⚡</span>
      <div>
        <div class="brand-title">ZevRay</div>
        <div class="brand-sub">Cloud Highrise SaaS</div>
      </div>
    </div>
    <div class="nav-actions">
      <div id="nav-auth-logged-out" style="display: flex; gap: 8px;">
        <button class="btn-nav-outline" onclick="openAuthModal('login')">Sign In</button>
        <button class="btn-nav-primary" onclick="openAuthModal('signup')">Create Account</button>
      </div>
      <div id="nav-auth-logged-in" style="display: none; align-items: center; gap: 10px;">
        <span class="user-pill" id="user-email-display">👤 user</span>
        <button class="btn-nav-outline" onclick="toggleDashboard()">🤖 My Bots (<span id="my-bots-count">0</span>)</button>
        <button class="btn-nav-outline" onclick="handleLogout()" style="color:#f87171;">Sign Out</button>
      </div>
    </div>
  </header>

  <div class="container">
    <div class="header">
      <div class="badge">🚀 Automated 1-Click Deployer</div>
      <h1>ZevRay 24/7 Highrise DJ Bot Cloud Deployer</h1>
      <p class="subtitle">Deploy your own 24/7 Music DJ, Multi-Floor Spawner, Tip Leaderboard & Room Assistant directly to your Render account in 60 seconds with zero coding.</p>
    </div>

    <!-- Authenticated User Bots Dashboard -->
    <div class="dashboard-card" id="user-dashboard" style="display: none;">
      <div class="dashboard-header">
        <div class="dashboard-title">
          <span>🤖</span> <span>My Deployed Bots</span>
        </div>
        <button class="copy-btn" onclick="loadUserBots()">🔄 Refresh</button>
      </div>
      <div id="bots-list-container">
        <div style="text-align: center; color: #9ca3af; font-size: 13px; padding: 16px 0;">
          Loading your deployed bots...
        </div>
      </div>
    </div>

    <div class="features-grid">
      <div class="feature-item"><span class="feature-icon">📻</span> 24/7 Music with /play takeover</div>
      <div class="feature-item"><span class="feature-icon">🏢</span> 4-Floor Spawner (/f1 to /f4)</div>
      <div class="feature-item"><span class="feature-icon">🏆</span> Live Tip Tracker & Leaderboard</div>
      <div class="feature-item"><span class="feature-icon">👗</span> Wardrobe & Presets (!fit)</div>
    </div>

    <div style="background: rgba(168, 85, 247, 0.1); border: 1px solid rgba(168, 85, 247, 0.3); border-radius: 12px; padding: 12px 16px; margin-bottom: 20px; font-size: 13px; color: #e9d5ff; display: flex; align-items: center; gap: 10px;">
      <span style="font-size: 20px;">📻</span>
      <span><b>In-Room Audio:</b> Once deployed, this portal gives you a direct <b>.mp3 Stream URL</b> (<code>https://...onrender.com/radio.mp3</code>) to paste directly into Highrise <b>Room Settings &rarr; Custom Radio Station</b>!</span>
    </div>

    <div class="card">
      <form id="deploy-form">
        <div class="form-group">
          <label class="form-label">Highrise Bot API Token</label>
          <input type="password" id="highrise-token" class="form-input" placeholder="e.g. 5928a3f890b..." required autocomplete="off">
          <div class="form-hint">Get your token from <a href="https://create.highrise.game" target="_blank">create.highrise.game</a> &rarr; Dashboard &rarr; Bots & API Keys &rarr; Create Bot.</div>
        </div>

        <div class="form-group">
          <label class="form-label">Highrise Room ID or URL</label>
          <input type="text" id="room-id" class="form-input" placeholder="e.g. 64005ecfb3d3644258e0040e or paste Room Link" required>
          <div class="form-hint">Open Highrise app &rarr; Room info panel &rarr; Tap "Share this Room" &rarr; Copy Link.</div>
        </div>

        <div class="form-group">
          <label class="form-label">Your Highrise Username</label>
          <input type="text" id="room-owner" class="form-input" placeholder="e.g. Sarah" required>
          <div class="form-hint">Your exact handle. Gives you full admin rights in-game for <code>!fit</code>, <code>!setpos</code>, and <code>!setfloor</code>.</div>
        </div>

        <div class="form-group">
          <label class="form-label">Room Display Name</label>
          <input type="text" id="room-title" class="form-input" placeholder="e.g. Club Neon or Chill Lounge" value="My Lounge">
          <div class="form-hint">Used in automated welcome whisper messages when players enter your room.</div>
        </div>

        <div class="form-group">
          <label class="form-label">Render API Key</label>
          <input type="password" id="render-key" class="form-input" placeholder="e.g. rnd_xxxx..." required autocomplete="off">
          <div class="form-hint">Log into <a href="https://dashboard.render.com" target="_blank">Render Dashboard</a> &rarr; Account Settings &rarr; API Keys &rarr; Create API Key. (Deploys directly on your account).</div>
        </div>

        <div class="form-group">
          <label class="form-label">cron-job.org API Key <span style="font-size: 11px; color: #c084fc; font-weight: normal; margin-left: 4px;">(Optional &mdash; Automatic 10-Min Setup)</span></label>
          <input type="password" id="cron-key" class="form-input" placeholder="e.g. crons_xxxx... (leave blank for Auto-Pinger)" autocomplete="off">
          <div class="form-hint">Optional: If provided, a 10-minute keep-alive ping is automatically created on your cron-job.org account. If left blank, our master network automatically keeps your bot awake every 10 minutes!</div>
        </div>

        <button type="submit" id="submit-btn" class="btn-deploy">
          <span>🚀 Deploy 24/7 Bot to Render</span>
        </button>
      </form>

      <div id="status-box" class="status-box">
        <span class="spinner"></span>
        <span id="status-text">Validating credentials & deploying to Render...</span>
      </div>

      <div id="error-box" class="error-box"></div>
    </div>

    <div id="success-card" class="success-card">
      <div class="success-title">
        <span>🎉</span> <span>Deployment Initiated Successfully!</span>
      </div>
      <p style="font-size: 13px; color: #9ca3af; margin-bottom: 16px;">Render is now building and deploying your bot container. It will connect to your room automatically in about 1-2 minutes!</p>

      <!-- Prominent Highrise In-Room Radio (.mp3) Section -->
      <div class="stream-card">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
          <span style="font-size: 13px; font-weight: 800; color: #f3f4f6; display: flex; align-items: center; gap: 6px;">
            <span>📻</span> <span>Highrise In-Room Custom Radio (.mp3 URL)</span>
          </span>
          <span style="font-size: 11px; background: rgba(168, 85, 247, 0.25); color: #d8b4fe; padding: 2px 8px; border-radius: 6px; font-weight: 700;">Paste into Room Radio</span>
        </div>
        <p style="font-size: 12px; color: #c084fc; line-height: 1.4;">
          This is your direct <b>.mp3 audio stream link</b>. Paste this into Highrise <b>Room Settings &rarr; Custom Radio Station</b> so music plays through your room speakers:
        </p>
        <div class="url-input-group">
          <input type="text" id="res-radio-input" class="url-input" readonly value="" placeholder="https://...onrender.com/radio.mp3">
          <button type="button" class="btn-action-copy" id="copy-radio-btn">📋 Copy .mp3 URL</button>
        </div>
      </div>

      <div class="info-row" style="flex-wrap: wrap;">
        <span class="info-label">⏱️ 24/7 Keep-Alive (10-Min):</span>
        <div style="display: flex; gap: 6px; align-items: center; flex: 1; justify-content: flex-end; min-width: 260px;">
          <input type="text" id="res-health-input" class="url-input" style="padding: 6px 10px; font-size: 12px;" readonly value="" placeholder="https://...onrender.com/health">
          <button type="button" class="copy-btn" id="copy-health-btn">📋 Copy</button>
        </div>
      </div>
      <div id="cron-status-badge" style="font-size: 12px; margin-top: -4px; margin-bottom: 12px; padding: 8px 12px; border-radius: 8px; background: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.3); color: #7dd3fc; line-height: 1.4;">
        ⚡ <b>Master Auto-Pinger Active:</b> This bot is automatically enrolled in our 10-minute master keep-alive network so Render never sleeps.
      </div>

      <div class="info-row">
        <span class="info-label">🌐 Web Jukebox Dashboard:</span>
        <div style="text-align: right;">
          <a id="res-player-url" href="#" target="_blank">Open /player Dashboard &rarr;</a>
          <div style="font-size: 11px; color: #6b7280; font-weight: normal; margin-top: 2px;">(Optional browser visualizer & queue viewer &mdash; do not paste this into room radio)</div>
        </div>
      </div>

      <div class="info-row">
        <span class="info-label">Render Service:</span>
        <span id="res-name" class="info-val" style="font-weight:700; color:#fff;"></span>
      </div>

      <div class="setup-steps">
        <h4>⚡ 2-Minute In-Room Setup Checklist:</h4>
        <ol>
          <li><b>Invite Bot:</b> Go to your room in Highrise and invite your bot account.</li>
          <li><b>Give Permissions:</b> Room Settings &rarr; Privileges &rarr; Give your bot <b>Designer</b> rights (required for teleporting players).</li>
          <li><b>Enable Room Audio:</b> In Highrise Room Settings &rarr; Custom Radio Station &rarr; Paste your copied <b>.mp3 Stream URL</b> (ends in <code>/radio.mp3</code>) and tap Save!</li>
          <li><b>Lock DJ Booth:</b> Walk into your DJ booth and type <code>!setpos</code> in chat.</li>
          <li><b>Set Floor Spawns:</b> Walk to each floor and type <code>!setfloor f1</code>, <code>!setfloor f2</code>, etc.</li>
          <li><b>24/7 Keep-Alive:</b> Your bot is already pinged every 10 minutes automatically by our network! If using your own cron-job account, run it every 10 mins.</li>
        </ol>
      </div>
    </div>

    <div style="text-align: center; margin-top: 36px; padding: 20px 0; border-top: 1px solid rgba(255,255,255,0.08); font-size: 12px; color: #6b7280;">
      <div>&copy; 2026 <b>ZevRay</b> (zevray.com). All rights reserved.</div>
      <div style="margin-top: 8px; display: flex; gap: 16px; justify-content: center; flex-wrap: wrap;">
        <a href="https://zevray.com" style="color: #9ca3af; text-decoration: none;">Home</a>
        <a href="mailto:support@zevray.com" style="color: #9ca3af; text-decoration: none;">Contact Support</a>
        <a href="https://create.highrise.game" target="_blank" style="color: #9ca3af; text-decoration: none;">Highrise Developer Hub</a>
      </div>
    </div>
  </div>

  <!-- Supabase Auth Modal -->
  <div class="modal-overlay" id="auth-modal" style="display: none;" onclick="handleModalBackdropClick(event)">
    <div class="modal-card">
      <div class="modal-header">
        <div class="modal-tabs">
          <button type="button" class="tab-btn active" id="tab-login" onclick="switchAuthTab('login')">Sign In</button>
          <button type="button" class="tab-btn" id="tab-signup" onclick="switchAuthTab('signup')">Create Account</button>
        </div>
        <button type="button" class="modal-close" onclick="closeAuthModal()">✕</button>
      </div>

      <form id="auth-form" onsubmit="handleAuthSubmit(event)">
        <div class="form-group">
          <label class="form-label">Email Address</label>
          <input type="email" id="auth-email" class="form-input" placeholder="you@example.com" required>
        </div>

        <div class="form-group">
          <label class="form-label">Password</label>
          <input type="password" id="auth-password" class="form-input" placeholder="••••••••" required minlength="6">
        </div>

        <div class="form-group" id="group-hr-username" style="display: none;">
          <label class="form-label">Highrise Username <span style="font-size:11px;color:#9ca3af;font-weight:normal;">(Optional)</span></label>
          <input type="text" id="auth-hr-username" class="form-input" placeholder="e.g. Sarah">
        </div>

        <div id="auth-error" class="error-box" style="display: none; margin-bottom: 14px;"></div>
        <div id="auth-success" class="status-box" style="display: none; margin-bottom: 14px; background: rgba(16,185,129,0.15); border-color: rgba(16,185,129,0.4); color: #34d399;"></div>

        <button type="submit" id="auth-submit-btn" class="btn-deploy" style="margin-top: 8px;">
          <span id="auth-submit-text">Sign In</span>
        </button>
      </form>
    </div>
  </div>

  <script>
    // ---------------- SUPABASE AUTH INTEGRATION ----------------
    const SUPABASE_URL = "__SUPABASE_URL__";
    const SUPABASE_ANON_KEY = "__SUPABASE_ANON_KEY__";
    const sb = window.supabase ? window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY) : null;

    let currentUser = null;
    let authMode = 'login'; // 'login' or 'signup'

    async function initAuth() {
      if (!sb) return;
      try {
        const { data: { session } } = await sb.auth.getSession();
        updateAuthUI(session ? session.user : null);

        sb.auth.onAuthStateChange((_event, session) => {
          updateAuthUI(session ? session.user : null);
        });
      } catch (err) {
        console.error("Auth init error:", err);
      }
    }

    function updateAuthUI(user) {
      currentUser = user;
      const loggedOutNav = document.getElementById('nav-auth-logged-out');
      const loggedInNav = document.getElementById('nav-auth-logged-in');
      const emailDisplay = document.getElementById('user-email-display');
      const dashboard = document.getElementById('user-dashboard');

      if (user) {
        loggedOutNav.style.display = 'none';
        loggedInNav.style.display = 'flex';
        emailDisplay.innerText = "👤 " + (user.email || "Account");
        dashboard.style.display = 'block';
        loadUserBots();
      } else {
        loggedOutNav.style.display = 'flex';
        loggedInNav.style.display = 'none';
        dashboard.style.display = 'none';
      }
    }

    function openAuthModal(mode = 'login') {
      authMode = mode;
      switchAuthTab(mode);
      document.getElementById('auth-error').style.display = 'none';
      document.getElementById('auth-success').style.display = 'none';
      document.getElementById('auth-modal').style.display = 'flex';
    }

    function closeAuthModal() {
      document.getElementById('auth-modal').style.display = 'none';
    }

    function handleModalBackdropClick(e) {
      if (e.target.id === 'auth-modal') {
        closeAuthModal();
      }
    }

    function switchAuthTab(mode) {
      authMode = mode;
      const tabLogin = document.getElementById('tab-login');
      const tabSignup = document.getElementById('tab-signup');
      const groupHr = document.getElementById('group-hr-username');
      const submitText = document.getElementById('auth-submit-text');

      if (mode === 'signup') {
        tabLogin.classList.remove('active');
        tabSignup.classList.add('active');
        groupHr.style.display = 'block';
        submitText.innerText = 'Create Account';
      } else {
        tabSignup.classList.remove('active');
        tabLogin.classList.add('active');
        groupHr.style.display = 'none';
        submitText.innerText = 'Sign In';
      }
      document.getElementById('auth-error').style.display = 'none';
      document.getElementById('auth-success').style.display = 'none';
    }

    async function handleAuthSubmit(e) {
      e.preventDefault();
      if (!sb) return;

      const email = document.getElementById('auth-email').value.trim();
      const password = document.getElementById('auth-password').value;
      const hrUsername = document.getElementById('auth-hr-username').value.trim();
      const errBox = document.getElementById('auth-error');
      const succBox = document.getElementById('auth-success');
      const submitBtn = document.getElementById('auth-submit-btn');

      errBox.style.display = 'none';
      succBox.style.display = 'none';
      submitBtn.disabled = true;

      try {
        if (authMode === 'signup') {
          const { data, error } = await sb.auth.signUp({
            email,
            password,
            options: {
              data: { highrise_username: hrUsername }
            }
          });
          if (error) throw error;
          if (data.session) {
            closeAuthModal();
          } else {
            succBox.innerText = "Check your email for confirmation link to complete registration!";
            succBox.style.display = 'block';
          }
        } else {
          const { error } = await sb.auth.signInWithPassword({ email, password });
          if (error) throw error;
          closeAuthModal();
        }
      } catch (err) {
        errBox.innerText = err.message || "Authentication failed.";
        errBox.style.display = 'block';
      } finally {
        submitBtn.disabled = false;
      }
    }

    async function handleLogout() {
      if (sb) {
        await sb.auth.signOut();
      }
      updateAuthUI(null);
    }

    function toggleDashboard() {
      const dash = document.getElementById('user-dashboard');
      if (dash.style.display === 'none') {
        dash.style.display = 'block';
        dash.scrollIntoView({ behavior: 'smooth' });
        loadUserBots();
      } else {
        dash.style.display = 'none';
      }
    }

    async function loadUserBots() {
      if (!currentUser) return;
      const container = document.getElementById('bots-list-container');
      const countBadge = document.getElementById('my-bots-count');

      try {
        const res = await fetch(`/api/user-bots?user_id=${currentUser.id}`);
        const data = await res.json();
        if (!data.success || !data.bots || data.bots.length === 0) {
          container.innerHTML = `
            <div style="text-align: center; color: #9ca3af; font-size: 13px; padding: 18px 0;">
              You have no active bots deployed yet. Fill the deployer form below to launch your first bot in 60s!
            </div>
          `;
          countBadge.innerText = '0';
          return;
        }

        countBadge.innerText = data.bots.length;
        let html = '';
        data.bots.forEach(bot => {
          const streamUrl = bot.radio_stream_url || `${bot.render_service_url}/radio.mp3`;
          const playerUrl = `${bot.render_service_url}/player`;
          html += `
            <div class="bot-item-card">
              <div class="bot-item-top">
                <div>
                  <span class="bot-item-title">${bot.room_title || 'Highrise Bot'}</span>
                  <span style="font-size: 12px; color: #9ca3af; margin-left: 8px;">Room: <code>${bot.room_id}</code></span>
                </div>
                <span class="status-active-pill">🟢 24/7 Active</span>
              </div>
              <div style="font-size: 12px; color: #c084fc; margin-bottom: 6px;">
                Owner: <b>@${bot.room_owner}</b> &bull; Service: <code>${bot.render_service_name || 'render-bot'}</code>
              </div>
              <div class="bot-links-row">
                <button class="copy-btn" onclick="navigator.clipboard.writeText('${streamUrl}'); alert('Copied .mp3 stream link!');">
                  📻 Copy In-Room .mp3 Link
                </button>
                <a href="${playerUrl}" target="_blank" class="copy-btn" style="text-decoration: none;">
                  🌐 Open Player Dashboard
                </a>
              </div>
            </div>
          `;
        });
        container.innerHTML = html;
      } catch (err) {
        container.innerHTML = `<div style="color: #f87171; font-size: 12px; padding: 10px 0;">Failed to load bots: ${err.message}</div>`;
      }
    }

    // ---------------- DEPLOY FORM LOGIC ----------------
    const form = document.getElementById('deploy-form');
    const submitBtn = document.getElementById('submit-btn');
    const statusBox = document.getElementById('status-box');
    const statusText = document.getElementById('status-text');
    const errorBox = document.getElementById('error-box');
    const successCard = document.getElementById('success-card');
    let healthUrl = '';
    let radioUrl = '';

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      errorBox.style.display = 'none';
      errorBox.innerText = '';
      successCard.style.display = 'none';

      submitBtn.disabled = true;
      statusBox.style.display = 'flex';
      statusText.innerText = "Connecting to Render API & creating service...";

      const payload = {
        highrise_token: document.getElementById('highrise-token').value.trim(),
        room_id: document.getElementById('room-id').value.trim(),
        room_owner: document.getElementById('room-owner').value.trim(),
        room_title: document.getElementById('room-title').value.trim(),
        render_api_key: document.getElementById('render-key').value.trim(),
        cron_api_key: document.getElementById('cron-key') ? document.getElementById('cron-key').value.trim() : '',
        user_id: currentUser ? currentUser.id : ''
      };

      try {
        const res = await fetch('/api/deploy-render', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        const data = await res.json();
        submitBtn.disabled = false;
        statusBox.style.display = 'none';

        if (!res.ok || !data.success) {
          errorBox.innerText = data.error || "Deployment failed. Please check your credentials.";
          errorBox.style.display = 'block';
          return;
        }

        // Display success card
        document.getElementById('res-name').innerText = data.service_name;
        document.getElementById('res-radio-input').value = data.radio_url;
        document.getElementById('res-health-input').value = data.health_url;
        document.getElementById('res-player-url').href = data.player_url;
        document.getElementById('res-player-url').innerText = data.player_url;
        healthUrl = data.health_url;
        radioUrl = data.radio_url;

        const badge = document.getElementById('cron-status-badge');
        if (data.cron_job_created) {
          badge.innerHTML = '✅ <b>Auto-Pinger Created:</b> A 10-minute cron job was automatically configured in your personal cron-job.org account!';
          badge.style.background = 'rgba(52, 211, 153, 0.15)';
          badge.style.borderColor = 'rgba(52, 211, 153, 0.4)';
          badge.style.color = '#6ee7b7';
        } else {
          badge.innerHTML = '⚡ <b>Master Auto-Pinger Active:</b> Enrolled in our 10-minute master keep-alive network so your bot never sleeps! (Or use the copied URL above in your own cron-job account).';
          badge.style.background = 'rgba(56, 189, 248, 0.12)';
          badge.style.borderColor = 'rgba(56, 189, 248, 0.3)';
          badge.style.color = '#7dd3fc';
        }

        successCard.style.display = 'block';
        successCard.scrollIntoView({ behavior: 'smooth' });

        // If user logged in, refresh dashboard
        if (currentUser) {
          loadUserBots();
          document.getElementById('user-dashboard').style.display = 'block';
        }
      } catch (err) {
        submitBtn.disabled = false;
        statusBox.style.display = 'none';
        errorBox.innerText = "Network error: " + err.message;
        errorBox.style.display = 'block';
      }
    });

    document.getElementById('copy-radio-btn').addEventListener('click', () => {
      const urlToCopy = radioUrl || document.getElementById('res-radio-input').value;
      if (urlToCopy) {
        navigator.clipboard.writeText(urlToCopy).then(() => {
          document.getElementById('copy-radio-btn').innerText = "✅ Copied .mp3 URL!";
          setTimeout(() => {
            document.getElementById('copy-radio-btn').innerText = "📋 Copy .mp3 URL";
          }, 2500);
        });
      }
    });

    document.getElementById('copy-health-btn').addEventListener('click', () => {
      const urlToCopy = healthUrl || document.getElementById('res-health-input').value;
      if (urlToCopy) {
        navigator.clipboard.writeText(urlToCopy).then(() => {
          document.getElementById('copy-health-btn').innerText = "✅ Copied!";
          setTimeout(() => {
            document.getElementById('copy-health-btn').innerText = "📋 Copy";
          }, 2500);
        });
      }
    });

    // Start auth check
    initAuth();
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
