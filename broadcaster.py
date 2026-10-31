import asyncio
import logging
import time
import aiohttp
from music import music_manager, Track

logger = logging.getLogger("Broadcaster")


class RadioBroadcaster:
    """Central broadcast hub feeding continuous audio (requested songs + 24/7 radio fallback)
    into Highrise room's Custom Radio Station and browser listeners."""

    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
        self._runner_task: asyncio.Task | None = None
        self._active_proc: asyncio.subprocess.Process | None = None

    def subscribe(self) -> asyncio.Queue:
        """Register a new HTTP listener queue (e.g. Highrise client or browser)."""
        q = asyncio.Queue(maxsize=40)
        self._subscribers.add(q)
        logger.info(f"New listener connected to /radio.mp3. Total active listeners: {len(self._subscribers)}")
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Unregister a listener queue upon client disconnect."""
        self._subscribers.discard(q)
        logger.info(f"Listener disconnected from /radio.mp3. Total active listeners: {len(self._subscribers)}")

    def broadcast(self, chunk: bytes):
        """Distribute an MP3 audio chunk to all active subscribers with overflow protection."""
        if not self._subscribers:
            return
        for q in list(self._subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(chunk)
            except asyncio.QueueFull:
                pass

    def start(self):
        """Launch the persistent audio broadcast engine."""
        if not self._runner_task or self._runner_task.done():
            self._runner_task = asyncio.create_task(self._run())
            logger.info("Radio broadcast engine initialized.")

    async def _run(self):
        """Main broadcast loop: streams requested song when active, otherwise relays 24/7 radio."""
        logger.info("Audio broadcast loop started.")
        while True:
            try:
                track = music_manager.current
                if track:
                    stream_url, headers = await music_manager.get_stream_url_for_track(track)
                    if stream_url and music_manager.current == track:
                        await self._stream_track_ffmpeg(track, stream_url, headers)
                    elif music_manager.current == track:
                        logger.warning(f"Could not resolve playable stream for '{track.title}', falling back to station.")
                        await self._stream_station(max_seconds=6.0)
                        if music_manager.current == track:
                            music_manager.notify_track_finished(track)
                else:
                    # Queue is idle: stream the 24/7 background radio station
                    await self._stream_station()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in broadcast loop: {e}", exc_info=True)
                await asyncio.sleep(2)

    async def _stream_track_ffmpeg(self, track: Track, stream_url: str, headers: dict):
        """Transcode and stream requested song audio in real-time as standard MP3."""
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

        cmd = [ffmpeg_exe]
        if headers:
            h_str = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
            cmd.extend(["-headers", h_str])

        cmd.extend([
            "-re",               # Read input at real-time native frame rate
            "-i", stream_url,
            "-vn",               # Disable video
            "-ar", "44100",      # Standard 44.1 kHz audio
            "-ac", "2",          # Stereo channels
            "-b:a", "128k",      # 128 kbps constant bitrate
            "-f", "mp3",         # MP3 output format
            "pipe:1"             # Pipe to stdout
        ])

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            self._active_proc = proc
            logger.info(f"Broadcasting requested song: \"{track.title}\" [{track.duration_str}]")

            while True:
                # Break immediately if skip was pressed or track changed
                if music_manager._skip_event.is_set() or music_manager.current != track:
                    logger.info(f"Track '{track.title}' interrupted or skipped.")
                    break

                chunk = await proc.stdout.read(4096)
                if not chunk:
                    logger.info(f"Track '{track.title}' completed audio playback.")
                    break

                self.broadcast(chunk)
                await asyncio.sleep(0)

        except Exception as e:
            logger.error(f"Error streaming track '{track.title}': {e}")
        finally:
            if proc:
                try:
                    proc.terminate()
                    await proc.wait()
                except Exception:
                    pass
            self._active_proc = None

            # If track naturally completed and wasn't skipped, signal music_manager
            if music_manager.current == track and not music_manager._skip_event.is_set():
                music_manager.notify_track_finished(track)

    async def _stream_station(self, max_seconds: float | None = None):
        """Relay continuous 24/7 internet radio chunks to all subscribers."""
        station_key = music_manager.current_station_key
        station_url = music_manager.get_current_station_url()
        music_manager._station_changed_event.clear()

        timeout = aiohttp.ClientTimeout(total=None, sock_read=15)
        start_t = time.time()
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(station_url) as upstream:
                    if upstream.status != 200:
                        logger.warning(f"Radio station '{station_key}' returned HTTP {upstream.status}")
                        await asyncio.sleep(2)
                        return

                    while not music_manager.current and not music_manager._station_changed_event.is_set():
                        if max_seconds and (time.time() - start_t) >= max_seconds:
                            break

                        chunk = await upstream.content.read(4096)
                        if not chunk:
                            break

                        self.broadcast(chunk)
                        await asyncio.sleep(0)

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.debug(f"Station relay notice ({station_key}): {e}")
            await asyncio.sleep(1)
        except Exception as e:
            logger.debug(f"Station relay error: {e}")
            await asyncio.sleep(1)


# Global singleton broadcaster instance
broadcaster = RadioBroadcaster()
