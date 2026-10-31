import asyncio
import logging
import os
import subprocess
from music import music_manager, Track

logger = logging.getLogger("Broadcaster")


def _generate_silence_chunk() -> bytes:
    """Generate 1 second of silent 44.1kHz 128kbps stereo MP3 frames."""
    import imageio_ffmpeg
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
    """Central broadcast engine feeding continuous audio into Highrise room's Custom Radio Station.
    Streams user requested songs from local cache; keeps the stream alive with silence when idle."""

    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
        self._runner_task: asyncio.Task | None = None
        self._active_proc: asyncio.subprocess.Process | None = None
        self._silence_chunk: bytes | None = None
        self.bot = None

    def subscribe(self) -> asyncio.Queue:
        """Register a new HTTP listener queue (e.g. Highrise room client or browser)."""
        q = asyncio.Queue(maxsize=40)
        self._subscribers.add(q)
        logger.info(f"Listener connected to /radio.mp3. Active listeners: {len(self._subscribers)}")
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Unregister a listener queue upon client disconnect."""
        self._subscribers.discard(q)
        logger.info(f"Listener disconnected from /radio.mp3. Active listeners: {len(self._subscribers)}")

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

    def start(self, bot_instance=None):
        """Launch the persistent audio broadcast engine."""
        if bot_instance:
            self.bot = bot_instance
        if not self._runner_task or self._runner_task.done():
            self._runner_task = asyncio.create_task(self._run())
            logger.info("Custom radio broadcast engine initialized.")

    async def _run(self):
        """Main broadcast loop: streams requested song when active, otherwise keeps stream alive with silence."""
        logger.info("Custom radio broadcast loop running.")
        while True:
            try:
                track = music_manager.current
                if track:
                    # Retrieve or wait for local cached MP3 audio file
                    file_path = await music_manager.ensure_track_audio_file(track)
                    if file_path and os.path.exists(file_path) and music_manager.current == track:
                        await self._stream_local_file_ffmpeg(track, file_path)
                    elif music_manager.current == track:
                        logger.warning(f"Could not prepare audio file for '{track.title}'. Skipping.")
                        if self.bot and hasattr(self.bot, "highrise") and self.bot.highrise:
                            try:
                                await self.bot.highrise.chat(f"⚠️ Could not load audio for \"{track.title}\". Skipping.")
                            except Exception:
                                pass
                        music_manager.notify_track_finished(track)
                        await asyncio.sleep(1)
                else:
                    # Queue is idle: keep the radio connection open 24/7 with silence
                    await self._stream_idle_silence()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in broadcast loop: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _stream_local_file_ffmpeg(self, track: Track, file_path: str):
        """Stream locally cached audio file in real-time as standard MP3."""
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

        cmd = [
            ffmpeg_exe,
            "-re",               # Read local file at real-time native frame rate
            "-i", file_path,     # Local audio file
            "-vn",               # Disable video
            "-ar", "44100",      # Standard 44.1 kHz audio
            "-ac", "2",          # Stereo channels
            "-b:a", "128k",      # 128 kbps constant bitrate
            "-f", "mp3",         # MP3 output format
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
            logger.info(f"Broadcasting requested song from local audio: \"{track.title}\" [{track.duration_str}]")

            while True:
                # Break immediately if skip was pressed or track changed
                if music_manager._skip_event.is_set() or music_manager.current != track:
                    logger.info(f"Track '{track.title}' interrupted or skipped.")
                    break

                chunk = await proc.stdout.read(4096)
                if not chunk:
                    logger.info(f"Track '{track.title}' finished playback.")
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

            # Clean up the file after playback finishes
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass

            # If track naturally completed and wasn't skipped, signal music_manager
            if music_manager.current == track and not music_manager._skip_event.is_set():
                music_manager.notify_track_finished(track)

    async def _stream_idle_silence(self):
        """Keep the Highrise radio station connected 24/7 with silence while waiting for requested songs."""
        if not self._silence_chunk:
            self._silence_chunk = _generate_silence_chunk()

        # Stream half-second slices of silence every 0.5s to maintain steady 128kbps flow
        chunk_size = max(len(self._silence_chunk) // 2, 417)
        part1 = self._silence_chunk[:chunk_size]
        part2 = self._silence_chunk[chunk_size:]

        while not music_manager.current:
            self.broadcast(part1)
            await asyncio.sleep(0.5)
            if music_manager.current:
                break
            self.broadcast(part2)
            await asyncio.sleep(0.5)


# Global singleton broadcaster instance
broadcaster = RadioBroadcaster()
