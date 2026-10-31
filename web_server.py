import logging
import time
import asyncio
import aiohttp
from aiohttp import web
import config
from music import music_manager

logger = logging.getLogger("WebServer")
start_time = time.time()


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
    #youtube-player-container {
      position: absolute;
      top: -9999px;
      left: -9999px;
      width: 1px;
      height: 1px;
      opacity: 0;
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

    <button class="btn-sync" id="btn-audio-control" onclick="toggleAudio()">
      <span id="btn-icon">🔊</span> <span id="btn-text">Click to Listen Live</span>
    </button>
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
    💡 In-room commands: <b>/play &lt;song&gt;</b> &bull; <b>/skip</b> &bull; <b>/queue</b> &bull; <b>/np</b>
  </div>

  <div id="youtube-player-container">
    <div id="yt-player"></div>
  </div>

  <!-- YouTube IFrame Player API -->
  <script src="https://www.youtube.com/iframe_api"></script>
  <script>
    let player = null;
    let isApiReady = false;
    let currentVideoId = null;
    let isAudioActive = false;
    let totalDuration = 180;
    let currentPosition = 0;

    function onYouTubeIframeAPIReady() {
      isApiReady = true;
      player = new YT.Player('yt-player', {
        height: '180',
        width: '320',
        playerVars: {
          'autoplay': 1,
          'controls': 0,
          'disablekb': 1,
          'fs': 0,
          'rel': 0,
          'playsinline': 1
        },
        events: {
          'onReady': onPlayerReady,
          'onStateChange': onPlayerStateChange
        }
      });
    }

    function onPlayerReady(event) {
      console.log("YouTube player ready.");
      fetchState();
      setInterval(fetchState, 2500);
      setInterval(updateProgressTick, 1000);
    }

    function onPlayerStateChange(event) {
      if (event.data === YT.PlayerState.ENDED) {
        fetchState();
      }
    }

    function formatTime(secs) {
      secs = Math.max(0, Math.floor(secs));
      const m = Math.floor(secs / 60);
      const s = secs % 60;
      return `${m}:${s < 10 ? '0' : ''}${s}`;
    }

    function toggleAudio() {
      if (!player) return;
      isAudioActive = true;
      player.unMute();
      player.playVideo();
      document.getElementById('btn-icon').innerText = '🎵';
      document.getElementById('btn-text').innerText = 'Listening In Sync';
      document.getElementById('btn-audio-control').style.background = 'linear-gradient(135deg, #10b981 0%, #059669 100%)';
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

    function renderState(state) {
      const now = state.now_playing;
      if (!now) {
        document.getElementById('track-title').innerText = "No Music Playing";
        document.getElementById('track-requester').innerText = "Type /play <song> in Highrise chat!";
        document.getElementById('track-art').src = "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=500";
        document.getElementById('progress-bar').style.width = '0%';
        document.getElementById('time-current').innerText = "0:00";
        document.getElementById('time-total').innerText = "0:00";
        if (player && player.stopVideo) player.stopVideo();
        currentVideoId = null;
        renderQueue(state.queue);
        return;
      }

      document.getElementById('track-title').innerText = now.title;
      document.getElementById('track-requester').innerText = "👤 Requested by @" + now.requester;
      document.getElementById('track-art').src = now.thumbnail;
      totalDuration = now.duration_sec || 180;
      currentPosition = state.position_sec || 0;
      document.getElementById('time-total').innerText = now.duration_str;

      // Handle video playback and synchronization
      if (player && player.loadVideoById) {
        if (currentVideoId !== now.id) {
          currentVideoId = now.id;
          player.loadVideoById({
            videoId: now.id,
            startSeconds: currentPosition
          });
          if (isAudioActive) {
            player.unMute();
            player.playVideo();
          }
        } else {
          // Check if sync drift is > 3 seconds
          const localTime = player.getCurrentTime ? player.getCurrentTime() : currentPosition;
          if (Math.abs(localTime - currentPosition) > 3) {
            player.seekTo(currentPosition, true);
          }
        }
      }

      renderQueue(state.queue);
    }

    function updateProgressTick() {
      if (!currentVideoId) return;
      currentPosition += 1;
      const pct = Math.min(100, (currentPosition / totalDuration) * 100);
      document.getElementById('progress-bar').style.width = pct + '%';
      document.getElementById('time-current').innerText = formatTime(currentPosition);
    }

    function renderQueue(queue) {
      const listEl = document.getElementById('queue-list');
      document.getElementById('queue-header').innerText = `Upcoming Queue (${queue.length})`;
      if (!queue || queue.length === 0) {
        listEl.innerHTML = `<p style="font-size: 12px; color: #6b7280; text-align: center; padding: 12px 0;">
          The queue is empty. Request songs in room chat using <b>/play &lt;song&gt;</b> or <b>/p &lt;song&gt;</b>!
        </p>`;
        return;
      }
      let html = '';
      queue.forEach((t, i) => {
        html += `
          <div class="queue-item">
            <img class="queue-thumb" src="${t.thumbnail}" alt="Thumbnail">
            <div class="queue-info">
              <div class="queue-title">#${i + 1} &bull; ${t.title}</div>
              <div class="queue-sub">${t.duration_str} &bull; Requested by @${t.requester}</div>
            </div>
          </div>
        `;
      });
      listEl.innerHTML = html;
    }
  </script>
</body>
</html>
"""


async def handle_player_page(request: web.Request) -> web.Response:
    """Serve the synchronized live jukebox player page."""
    return web.Response(text=PLAYER_HTML, content_type="text/html")


async def handle_audio_current(request: web.Request) -> web.Response:
    """Direct audio stream endpoint for Highrise Studio's PlayMusic component."""
    stream_url, _ = await music_manager.get_current_stream_url()
    if not stream_url:
        return web.Response(text="No active music stream", status=404)
    raise web.HTTPFound(location=stream_url, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    })


async def handle_radio_stream(request: web.Request) -> web.StreamResponse:
    """Stream live audio bytes for browser audio players and Highrise custom radio."""
    if not music_manager.current:
        return web.Response(
            text="No music is currently playing! Request a song in your Highrise room using /play <song> first.",
            status=404,
            content_type="text/plain",
        )

    stream_url, upstream_headers = await music_manager.get_current_stream_url()
    if not stream_url:
        return web.Response(text="Failed to extract audio stream for current track.", status=502)

    headers = dict(upstream_headers or {})
    range_header = request.headers.get("Range")
    if range_header:
        headers["Range"] = range_header

    async with aiohttp.ClientSession() as session:
        async with session.get(stream_url, headers=headers) as upstream:
            content_type = upstream.headers.get("Content-Type", "audio/mp4")
            resp_headers = {
                "Content-Type": content_type,
                "Accept-Ranges": "bytes",
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-cache",
            }
            if "Content-Length" in upstream.headers:
                resp_headers["Content-Length"] = upstream.headers["Content-Length"]
            if "Content-Range" in upstream.headers:
                resp_headers["Content-Range"] = upstream.headers["Content-Range"]

            response = web.StreamResponse(status=upstream.status, headers=resp_headers)
            await response.prepare(request)

            try:
                async for chunk in upstream.content.iter_chunked(16384):
                    await response.write(chunk)
            except (ConnectionResetError, asyncio.CancelledError):
                pass

            return response


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
    return app


async def start_web_server(port: int) -> web.AppRunner:
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"[Web] Keep-alive Web Server listening on http://0.0.0.0:{port}")
    return runner
