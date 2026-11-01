import asyncio
import logging
import os
import subprocess
import time
import imageio_ffmpeg
from music import music_manager, Track
import config

logger = logging.getLogger("Broadcaster")


def _generate_pure_silence() -> tuple[list[bytes], bytes]:
    """Generate pure, frame-aligned 44.1kHz 128kbps stereo silent MP3 frames with NO ID3 tags."""
    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [
            ffmpeg_exe,
            "-f", "lavfi",
            "-i", "anullsrc=r=44100:cl=stereo",
            "-t", "4",
            "-ar", "44100",
            "-ac", "2",
            "-b:a", "128k",
            "-write_xing", "0",
            "-id3v2_version", "0",
            "-f", "mp3",
            "pipe:1"
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out, _ = proc.communicate(timeout=5)
        if out and len(out) > 10000:
            # Locate all MP3 frame syncwords (0xFF, 0xFB)
            syncs = [i for i in range(len(out) - 1) if out[i] == 0xff and (out[i + 1] & 0xfe) == 0xfa]
            if len(syncs) >= 16:
                # Group into 4 frame-aligned slices (approx 0.25s each) from the first second
                step = len(syncs) // 16
                slices = []
                for s_idx in range(4):
                    start = syncs[s_idx * step]
                    end = syncs[(s_idx + 1) * step]
                    slices.append(out[start:end])
                burst = out[:syncs[min(len(syncs) - 1, 160)]] if len(syncs) > 160 else out
                return slices, burst
    except Exception as e:
        logger.debug(f"Error generating pure silence with ffmpeg: {e}")

    # Fallback minimal 417-byte silent frame (MPEG-1 Layer 3, 128kbps, 44.1kHz stereo)
    minimal_frame = b"\xff\xfb\x90d" + b"\x00" * 413
    return [minimal_frame * 10] * 4, minimal_frame * 160


class RadioBroadcaster:
    """24/7 Autonomous Live Radio Station engine for Highrise room.
    Continuously feeds audio to connected clients. Streams queued songs without gaps
    and maintains live connection with frame-aligned silence and a burst buffer when idle."""

    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
        self._runner_task: asyncio.Task | None = None
        self._active_proc: asyncio.subprocess.Process | None = None
        self._skip_flag: bool = False
        self._silence_slices: list[bytes] = []
        self._burst_buffer = bytearray()
        self.bot = None

        self._init_silence()

    def _init_silence(self):
        slices, burst = _generate_pure_silence()
        self._silence_slices = slices
        self._burst_buffer = bytearray(burst)

    def subscribe(self) -> asyncio.Queue:
        """Register a new HTTP listener (Highrise in-room radio player or browser)."""
        q = asyncio.Queue(maxsize=100)
        # Pre-buffer: Send recent audio burst immediately so listener decodes without buffering delay
        if self._burst_buffer:
            q.put_nowait(bytes(self._burst_buffer))
        self._subscribers.add(q)
        logger.info(f"Listener connected to /radio.mp3. Active listeners: {len(self._subscribers)}")
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Unregister a listener upon disconnect."""
        self._subscribers.discard(q)
        logger.info(f"Listener disconnected from /radio.mp3. Active listeners: {len(self._subscribers)}")

    def broadcast(self, chunk: bytes):
        """Send an MP3 chunk to all connected listeners and maintain the burst buffer."""
        if not chunk:
            return
        # Append to burst buffer (keep last 64 KB of live audio, aligned to MP3 frame sync)
        self._burst_buffer.extend(chunk)
        if len(self._burst_buffer) > 65536:
            cutoff = len(self._burst_buffer) - 65536
            idx = self._burst_buffer.find(b"\xff\xfb", cutoff)
            if idx != -1:
                self._burst_buffer = self._burst_buffer[idx:]
            else:
                self._burst_buffer = self._burst_buffer[-65536:]

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

    def skip_current(self) -> tuple[bool, str]:
        """Skip currently playing track immediately and advance to next."""
        if not music_manager.current:
            return False, "No music is currently playing on the queue."
        title = music_manager.current.title
        logger.info(f"Skipping track requested: '{title}'")
        self._skip_flag = True
        music_manager._queue_updated_event.set()
        if self._active_proc:
            try:
                self._active_proc.terminate()
            except Exception:
                pass
        return True, f"⏭️ Skipped: \"{title}\""

    def start(self, bot_instance=None):
        """Launch the persistent 24/7 radio broadcast engine."""
        if bot_instance:
            self.bot = bot_instance
        if not self._runner_task or self._runner_task.done():
            self._runner_task = asyncio.create_task(self._run())
            logger.info("24/7 Live Radio Station engine initialized.")

    async def _run(self):
        """Main autonomous radio station loop."""
        logger.info("24/7 Live Radio Station loop running.")
        while True:
            try:
                # 1. If no track currently playing, check for next track (user queue first, or autoplay)
                if not music_manager.current:
                    next_track = await music_manager.pop_next_track()
                    if next_track:
                        music_manager.current = next_track
                        self._skip_flag = False

                track = music_manager.current
                if track:
                    # Stream the current track
                    await self._play_track(track)
                    # Once finished or skipped, reset current
                    music_manager.current = None
                    music_manager.started_at = 0.0
                    music_manager._queue_updated_event.set()
                    self._skip_flag = False
                else:
                    # Queue is empty and autoplay disabled: stream silence keeping the station alive 24/7
                    await self._stream_idle_silence_tick()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in radio broadcast loop: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _announce_now_playing(self, track: Track):
        """Announce new track in room chat."""
        if self.bot and hasattr(self.bot, "highrise") and self.bot.highrise:
            try:
                base_url = config.RENDER_EXTERNAL_URL or f"http://localhost:{config.PORT}"
                if track.is_autoplay:
                    if config.AUTOPLAY_CHAT_ANNOUNCEMENTS:
                        announcement = (
                            f"📻 Radio AutoDJ: \"{track.title}\" [{track.duration_str}]\n"
                            f"💡 Type /play <song> to queue your request next!"
                        )
                        await self.bot.highrise.chat(announcement)
                else:
                    announcement = (
                        f"🎶 Now Playing: \"{track.title}\" [{track.duration_str}]\n"
                        f"👤 Requested by: @{track.requester}\n"
                        f"📻 Room Radio: {base_url}/radio.mp3"
                    )
                    await self.bot.highrise.chat(announcement)
            except Exception as e:
                logger.error(f"Error announcing track in room chat: {e}")

    async def _ensure_audio_file_while_streaming(self, track: Track) -> str | None:
        """Download audio file while continuously streaming silence so the connection never starves."""
        if not self._silence_slices:
            self._init_silence()

        download_task = asyncio.create_task(music_manager.ensure_track_audio_file(track))
        slice_idx = 0

        while not download_task.done():
            if self._skip_flag:
                download_task.cancel()
                return None
            # Broadcast frame-aligned silence slice so Highrise never times out
            self.broadcast(self._silence_slices[slice_idx % len(self._silence_slices)])
            slice_idx += 1
            await asyncio.sleep(0.25)

        try:
            return await download_task
        except Exception as e:
            logger.error(f"Error in audio download for '{track.title}': {e}")
            return None

    async def _play_track(self, track: Track):
        """Stream audio for the track through ffmpeg (instant stream piping or disk cache)."""
        source = await music_manager.resolve_audio_stream_source(track)

        if not source:
            if not self._skip_flag:
                logger.warning(f"Could not resolve audio source for '{track.title}'. Skipping.")
                if self.bot and hasattr(self.bot, "highrise") and self.bot.highrise:
                    try:
                        await self.bot.highrise.chat(f"⚠️ Could not load audio for \"{track.title}\". Skipping to next song.")
                    except Exception:
                        pass
            return

        is_url = source.startswith("http://") or source.startswith("https://")
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

        cmd = [ffmpeg_exe]
        if is_url:
            cmd.extend([
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
            ])
        cmd.extend([
            "-re",                 # Read at native 1x real-time rate
            "-i", source,          # Stream URL or local cache file
            "-vn",                 # Disable video
            "-ar", "44100",        # 44.1 kHz
            "-ac", "2",            # Stereo
            "-b:a", "128k",        # 128 kbps constant bitrate
            "-write_xing", "0",    # No Xing header in continuous stream
            "-id3v2_version", "0", # No ID3v2 in stream body
            "-f", "mp3",           # MP3 container
            "pipe:1"               # Pipe to stdout
        ])

        cache_file = os.path.join("cache", f"{track.id}.mp3")
        cache_part = cache_file + ".part"
        cache_f = None
        # If streaming from network and not already cached on disk, write stream chunks to cache
        if is_url and not os.path.exists(cache_file):
            try:
                os.makedirs("cache", exist_ok=True)
                cache_f = open(cache_part, "wb")
            except Exception as e:
                logger.debug(f"Could not open cache part file: {e}")

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            self._active_proc = proc
            music_manager.started_at = time.time()
            await self._announce_now_playing(track)
            logger.info(f"Broadcasting song: \"{track.title}\" [{track.duration_str}] from {'stream URL' if is_url else 'local cache'}")

            while True:
                if self._skip_flag:
                    logger.info(f"Track '{track.title}' was skipped.")
                    break

                chunk = await proc.stdout.read(4096)
                if not chunk:
                    logger.info(f"Track '{track.title}' completed playback.")
                    break

                self.broadcast(chunk)
                if cache_f:
                    try:
                        cache_f.write(chunk)
                    except Exception:
                        pass
                await asyncio.sleep(0)

        except Exception as e:
            logger.error(f"Error streaming track '{track.title}': {e}")
        finally:
            if cache_f:
                try:
                    cache_f.close()
                except Exception:
                    pass
                if not self._skip_flag and os.path.exists(cache_part) and os.path.getsize(cache_part) > 10000:
                    try:
                        os.replace(cache_part, cache_file)
                        logger.info(f"Cached complete broadcast audio: {cache_file}")
                    except Exception as e:
                        logger.debug(f"Cache rename error: {e}")
                else:
                    try:
                        if os.path.exists(cache_part):
                            os.remove(cache_part)
                    except Exception:
                        pass

            if proc:
                try:
                    proc.terminate()
                    await proc.wait()
                except Exception:
                    pass
            self._active_proc = None

    async def _stream_idle_silence_tick(self):
        """Stream silence keeping the radio connection alive 24/7 when no songs are queued."""
        if not self._silence_slices:
            self._init_silence()

        for s in self._silence_slices:
            self.broadcast(s)
            try:
                await asyncio.wait_for(music_manager._new_track_event.wait(), timeout=0.25)
                music_manager._new_track_event.clear()
                break  # New track queued! Break immediately to play it
            except asyncio.TimeoutError:
                pass


# Global singleton broadcaster instance
broadcaster = RadioBroadcaster()
