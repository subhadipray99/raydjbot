import asyncio
import logging
import os
import subprocess
import time
import imageio_ffmpeg
from music import music_manager, Track
import config

logger = logging.getLogger("Broadcaster")


def _generate_silence_chunk() -> bytes:
    """Generate 1 second of silent 44.1kHz 128kbps stereo MP3 frames."""
    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [
            ffmpeg_exe,
            "-f", "lavfi",
            "-i", "anullsrc=r=44100:cl=stereo",
            "-t", "1",
            "-ar", "44100",
            "-ac", "2",
            "-b:a", "128k",
            "-f", "mp3",
            "pipe:1"
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out, _ = proc.communicate(timeout=5)
        if out and len(out) > 1000:
            return out
    except Exception as e:
        logger.debug(f"Error generating silence with ffmpeg: {e}")
    # Fallback minimal MP3 silence frame (417 bytes)
    return b"\xff\xfb\x90d" + b"\x00" * 413


class RadioBroadcaster:
    """24/7 Autonomous Live Radio Station engine for Highrise room.
    Continuously feeds audio to connected clients. Streams queued songs without gaps
    and maintains live connection with silence when idle."""

    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
        self._runner_task: asyncio.Task | None = None
        self._active_proc: asyncio.subprocess.Process | None = None
        self._skip_flag: bool = False
        self._silence_chunk: bytes | None = None
        self.bot = None

    def subscribe(self) -> asyncio.Queue:
        """Register a new HTTP listener (Highrise in-room radio player or browser)."""
        q = asyncio.Queue(maxsize=50)
        self._subscribers.add(q)
        logger.info(f"Listener connected to /radio.mp3. Active listeners: {len(self._subscribers)}")
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Unregister a listener upon disconnect."""
        self._subscribers.discard(q)
        logger.info(f"Listener disconnected from /radio.mp3. Active listeners: {len(self._subscribers)}")

    def broadcast(self, chunk: bytes):
        """Send an MP3 chunk to all connected listeners."""
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

    def _get_silence_chunk(self) -> bytes:
        if not self._silence_chunk:
            self._silence_chunk = _generate_silence_chunk()
        return self._silence_chunk

    async def _run(self):
        """Main autonomous radio station loop."""
        logger.info("24/7 Live Radio Station loop running.")
        while True:
            try:
                # 1. If no track currently playing, check if there is one in the queue
                if not music_manager.current and music_manager.queue:
                    music_manager.current = music_manager.queue.pop(0)
                    music_manager.started_at = time.time()
                    self._skip_flag = False
                    await self._announce_now_playing(music_manager.current)

                track = music_manager.current
                if track:
                    # Stream the current track
                    await self._play_track(track)
                    # Once finished or skipped, reset current
                    music_manager.current = None
                    music_manager.started_at = 0.0
                    self._skip_flag = False
                else:
                    # Queue is empty: stream silence keeping the station alive 24/7
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
                announcement = (
                    f"🎶 Now Playing: \"{track.title}\" [{track.duration_str}]\n"
                    f"👤 Requested by: @{track.requester}\n"
                    f"📻 Room Radio: {base_url}/radio.mp3"
                )
                await self.bot.highrise.chat(announcement)
            except Exception as e:
                logger.error(f"Error announcing track in room chat: {e}")

    async def _ensure_audio_file_while_streaming(self, track: Track) -> str | None:
        """Download or retrieve audio file while continuously streaming silence so the connection never starves."""
        silence = self._get_silence_chunk()
        chunk_size = max(len(silence) // 4, 417)
        slices = [silence[i:i + chunk_size] for i in range(0, len(silence), chunk_size)]

        download_task = asyncio.create_task(music_manager.ensure_track_audio_file(track))
        slice_idx = 0

        while not download_task.done():
            if self._skip_flag:
                download_task.cancel()
                return None
            # Broadcast silence slice so Highrise never times out
            self.broadcast(slices[slice_idx % len(slices)])
            slice_idx += 1
            await asyncio.sleep(0.25)

        try:
            return await download_task
        except Exception as e:
            logger.error(f"Error in audio download for '{track.title}': {e}")
            return None

    async def _play_track(self, track: Track):
        """Stream audio file for the track through ffmpeg."""
        file_path = await self._ensure_audio_file_while_streaming(track)

        if not file_path or not os.path.exists(file_path):
            if not self._skip_flag:
                logger.warning(f"Could not load audio file for '{track.title}'. Skipping.")
                if self.bot and hasattr(self.bot, "highrise") and self.bot.highrise:
                    try:
                        await self.bot.highrise.chat(f"⚠️ Could not load audio for \"{track.title}\". Skipping to next song.")
                    except Exception:
                        pass
            return

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [
            ffmpeg_exe,
            "-re",               # Read local file at native 1x real-time rate
            "-i", file_path,     # Local audio file
            "-vn",               # Disable video
            "-ar", "44100",      # 44.1 kHz
            "-ac", "2",          # Stereo
            "-b:a", "128k",      # 128 kbps constant bitrate
            "-f", "mp3",         # MP3 container
            "pipe:1"             # Pipe to stdout
        ]

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
                if self._skip_flag:
                    logger.info(f"Track '{track.title}' was skipped.")
                    break

                chunk = await proc.stdout.read(4096)
                if not chunk:
                    logger.info(f"Track '{track.title}' completed playback.")
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

            # Clean up local cache file
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass

    async def _stream_idle_silence_tick(self):
        """Stream silence keeping the radio connection alive 24/7 when no songs are queued."""
        silence = self._get_silence_chunk()
        chunk_size = max(len(silence) // 4, 417)
        slices = [silence[i:i + chunk_size] for i in range(0, len(silence), chunk_size)]

        for s in slices:
            self.broadcast(s)
            try:
                await asyncio.wait_for(music_manager._new_track_event.wait(), timeout=0.25)
                music_manager._new_track_event.clear()
                break  # New track queued! Break immediately to play it
            except asyncio.TimeoutError:
                pass


# Global singleton broadcaster instance
broadcaster = RadioBroadcaster()
