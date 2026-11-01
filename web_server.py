import os
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


def get_uptime() -> str:
    elapsed_seconds = int(time.time() - start_time)
    hours, remainder = divmod(elapsed_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes}m {seconds}s"


async def handle_root(request: web.Request) -> web.Response:
    """Status endpoint for root URL."""
    base_url = config.RENDER_EXTERNAL_URL or f"http://localhost:{config.PORT}"
    return web.json_response({
        "status": "online",
        "service": "Highrise Bot Web Service & Jukebox",
        "uptime": get_uptime(),
        "room_configured": bool(config.ROOM_ID),
        "room_id": config.ROOM_ID,
        "saved_position": position.get_saved_position_dict(config.ROOM_ID),
        "player_url": f"{base_url}/player",
        "music_state": music_manager.get_state(),
    })


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


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/player", handle_player_page)
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
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"[Web] Keep-alive Web Server listening on http://0.0.0.0:{port}")
    return runner
