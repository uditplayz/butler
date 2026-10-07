"""Frame-clocked switcher: live feed when healthy, a slate when not.

Why this exists: Twitch/YouTube drop the broadcast when the incoming RTMP feed
stops. A phone on cellular *will* lose signal now and then. So the phone never
talks to Twitch directly. Instead:

    phone --SRT/SRTLA--> MediaMTX "live"
         --> decoder ffmpeg (raw video + PCM audio)           [reconnects on its own]
         --> this compositor, ticking at exactly VIDEO_FPS    [never stops]
         --> encoder ffmpeg (x264 CBR + AAC) --> MediaMTX "program"
         --> one relay ffmpeg per platform (-c copy)

The compositor emits one video frame and one audio chunk per tick no matter what:
the newest live frame, a frozen frame while rebuffering, or the slate. The encoder
therefore sees a perfectly continuous, constant-frame-rate input and the platforms
never see a gap, even if the phone disappears for minutes.
"""

from __future__ import annotations

import collections
import fcntl
import logging
import os
import select
import subprocess
import threading
import time

from .config import Config
from .util import Backoff, MediaMTX, pump_lines, terminate

F_SETPIPE_SZ = 1031


def _bigger_pipe(fd: int, size: int) -> None:
    try:
        fcntl.fcntl(fd, F_SETPIPE_SZ, size)
    except OSError:
        pass  # capped by /proc/sys/fs/pipe-max-size; works fine with the default too


def _write_all(fd: int, data: bytes | bytearray) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


class Compositor:
    def __init__(self, cfg: Config, slate_frame: bytes, mtx: MediaMTX, stop: threading.Event):
        self.cfg, self.mtx, self.stop = cfg, mtx, stop
        self.log = logging.getLogger("compositor")
        self.slate_frame = slate_frame
        self.silence = bytes(cfg.audio_bytes_per_tick)

        self._lock = threading.Lock()
        self._vq: collections.deque[bytearray] = collections.deque()
        self._aq = bytearray()
        self._last_frame_t = 0.0
        self._v_cap = cfg.jitter_frames + cfg.fps // 2
        self._a_cap = self._v_cap * cfg.audio_bytes_per_tick

        # State owned by the tick loop (under _lock when it touches the queues).
        self.state = "slate"  # "slate" | "live"
        self._buffering = False
        self._last_live_frame: bytearray | bytes | None = None
        self._slate_since = time.monotonic() - cfg.slate_hold_s

        self._encoder: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._last_tick_done = time.monotonic()
        self.stats = {"switches": 0, "encoder_restarts": 0, "decoder_restarts": 0,
                      "frozen_ticks": 0, "ticks": 0}

    # ------------------------------------------------------------------ decoder
    def decoder_command(self, audio_fd: int) -> list[str]:
        c = self.cfg
        vf = (f"fps={c.fps},scale={c.width}:{c.height}:force_original_aspect_ratio=decrease,"
              f"pad={c.width}:{c.height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p")
        af = "aresample=48000:async=1000,asetpts=N/SR/TB,aformat=sample_fmts=s16:channel_layouts=stereo"
        return [
            c.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-rtsp_transport", "tcp", "-timeout", "10000000",
            "-analyzeduration", "2000000", "-probesize", "5000000",
            "-i", c.rtsp_url(c.live_path),
            "-map", "0:v:0", "-vf", vf, "-an", "-f", "rawvideo", "pipe:1",
            "-map", "0:a:0?", "-af", af, "-vn", "-ar", "48000", "-ac", "2", "-f", "s16le", f"pipe:{audio_fd}",
        ]

    def _decoder_loop(self) -> None:
        backoff = Backoff(1.0, 5.0, healthy_after=20.0)
        while not self.stop.is_set():
            if self.mtx.is_ready(self.cfg.live_path) is not True:
                self.stop.wait(0.5)
                continue
            started = time.monotonic()
            self._run_decoder()
            if self.stop.is_set():
                break
            self.stats["decoder_restarts"] += 1
            self.stop.wait(backoff.next(time.monotonic() - started))

    def _run_decoder(self) -> None:
        a_read, a_write = os.pipe()
        proc = subprocess.Popen(
            self.decoder_command(a_write), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, pass_fds=(a_write,), bufsize=0,
        )
        os.close(a_write)
        pump_lines(proc.stderr, logging.getLogger("decoder"))
        threading.Thread(target=self._audio_reader, args=(a_read,), daemon=True).start()
        try:
            self._video_reader(proc)
        finally:
            terminate(proc)
            # a_read is closed by the audio reader on EOF

    def _video_reader(self, proc: subprocess.Popen) -> None:
        fd, size, c = proc.stdout.fileno(), self.cfg.frame_bytes, self.cfg
        frames, last_data = 0, time.monotonic()
        while not self.stop.is_set():
            buf = bytearray(size)
            view, got = memoryview(buf), 0
            while got < size:
                ready, _, _ = select.select([fd], [], [], 1.0)
                if self.stop.is_set():
                    return
                if not ready:
                    # Alive but silent (e.g. the phone is gone but SRT hasn't timed out yet).
                    if proc.poll() is not None or time.monotonic() - last_data > 20:
                        return
                    continue
                n = os.readv(fd, [view[got:]])
                if n == 0:
                    return
                got += n
            frames += 1
            last_data = time.monotonic()
            with self._lock:
                if frames == 1:
                    self.log.info("live source connected")
                self._last_frame_t = time.monotonic()
                self._vq.append(buf)
                while len(self._vq) > self._v_cap:
                    self._vq.popleft()

    def _audio_reader(self, fd: int) -> None:
        try:
            while not self.stop.is_set():
                data = os.read(fd, 65536)
                if not data:
                    return
                with self._lock:
                    self._aq += data
                    if len(self._aq) > self._a_cap:
                        del self._aq[: len(self._aq) - self._a_cap]
        except OSError:
            pass
        finally:
            os.close(fd)

    # --------------------------------------------------------------------- tick
    def _take_audio(self) -> bytes | bytearray:
        n = self.cfg.audio_bytes_per_tick
        if len(self._aq) >= n:
            chunk = self._aq[:n]
            del self._aq[:n]
            return chunk
        chunk = bytes(self._aq) + bytes(n - len(self._aq))  # pad the shortfall with silence
        self._aq.clear()
        return chunk

    def _to_slate(self, now: float) -> None:
        self.state, self._buffering = "slate", False
        self._slate_since = now
        self._last_live_frame = None
        self._vq.clear()
        self._aq.clear()
        self.stats["switches"] += 1
        self.log.warning("live source lost -> showing slate")

    def _to_live(self) -> None:
        # Align audio with video: keep the same amount of recent audio as video.
        keep = min(len(self._vq), self.cfg.jitter_frames)
        while len(self._vq) > keep:
            self._vq.popleft()
        keep_audio = keep * self.cfg.audio_bytes_per_tick
        if len(self._aq) > keep_audio:
            del self._aq[: len(self._aq) - keep_audio]
        self.state, self._buffering = "live", False
        self.stats["switches"] += 1
        self.log.info("live source healthy -> back on air")

    def next_tick(self, now: float) -> tuple[bytes | bytearray, bytes | bytearray]:
        """Decide what the next frame and audio chunk are. Pure queue logic, no I/O."""
        c, target = self.cfg, self.cfg.jitter_frames
        with self._lock:
            if self.state == "slate":
                fresh = now - self._last_frame_t < 1.0
                if fresh and len(self._vq) >= target and now - self._slate_since >= c.slate_hold_s:
                    self._to_live()
                else:
                    return self.slate_frame, self.silence

            if self._buffering:
                if len(self._vq) >= target:
                    self._buffering = False
                    keep_audio = len(self._vq) * c.audio_bytes_per_tick
                    if len(self._aq) > keep_audio:
                        del self._aq[: len(self._aq) - keep_audio]
                else:
                    return self._hold(now)

            if not self._vq:
                self._buffering = True
                return self._hold(now)

            frame = self._vq.popleft()
            audio = self._take_audio()
            # Catch up gently after a burst by discarding one extra tick of both streams.
            if len(self._vq) > target + c.fps // 4:
                self._vq.popleft()
                self._take_audio()
            self._last_live_frame = frame
            return frame, audio

    def _hold(self, now: float) -> tuple[bytes | bytearray, bytes | bytearray]:
        """No fresh live data: freeze the last frame; give up and show the slate after a while."""
        self.stats["frozen_ticks"] += 1
        if now - self._last_frame_t > self.cfg.live_stall_s:
            self._to_slate(now)
            return self.slate_frame, self.silence
        return (self._last_live_frame or self.slate_frame), self.silence

    # ------------------------------------------------------------------ encoder
    def encoder_command(self, video_fd: int, audio_fd: int) -> list[str]:
        c = self.cfg
        gop = c.fps * 2  # Twitch/YouTube want a keyframe every 2 seconds
        rate = f"{c.video_kbps}k"
        return [
            c.ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-probesize", "32", "-analyzeduration", "0",
            "-thread_queue_size", "16", "-f", "rawvideo", "-pix_fmt", "yuv420p",
            "-video_size", f"{c.width}x{c.height}", "-framerate", str(c.fps), "-i", f"pipe:{video_fd}",
            "-probesize", "32", "-analyzeduration", "0",
            "-thread_queue_size", "512", "-f", "s16le", "-ar", "48000", "-ac", "2", "-i", f"pipe:{audio_fd}",
            "-c:v", "libx264", "-preset", c.x264_preset, "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-b:v", rate, "-minrate", rate, "-maxrate", rate, "-bufsize", f"{c.video_kbps * 2}k",
            "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
            "-x264-params", "nal-hrd=cbr:force-cfr=1",
            "-c:a", "aac", "-b:a", f"{c.audio_kbps}k", "-ar", "48000", "-ac", "2",
            "-flvflags", "no_duration_filesize", "-f", "flv",
            f"rtmp://{c.mtx_host}:{c.mtx_rtmp_port}/{c.program_path}",
        ]

    def _run_encoder(self) -> float:
        v_read, v_write = os.pipe()
        a_read, a_write = os.pipe()
        _bigger_pipe(v_write, 4 * 1024 * 1024)
        _bigger_pipe(a_write, 1024 * 1024)
        proc = subprocess.Popen(
            self.encoder_command(v_read, a_read), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, pass_fds=(v_read, a_read),
        )
        os.close(v_read)
        os.close(a_read)
        pump_lines(proc.stderr, logging.getLogger("encoder"))
        self._encoder = proc
        started = time.monotonic()
        period = 1.0 / self.cfg.fps
        next_t = started
        self._last_tick_done = started
        try:
            while not self.stop.is_set() and proc.poll() is None:
                now = time.monotonic()
                if now < next_t:
                    self.stop.wait(next_t - now)
                    continue
                video, audio = self.next_tick(now)
                _write_all(a_write, audio)
                _write_all(v_write, video)
                self.stats["ticks"] += 1
                self._last_tick_done = time.monotonic()
                next_t += period
                if self._last_tick_done - next_t > 0.5:  # fell far behind: don't burst to catch up
                    next_t = self._last_tick_done
        except (BrokenPipeError, OSError) as exc:
            if not self.stop.is_set():
                self.log.error("encoder pipe closed: %s", exc)
        finally:
            for fd in (v_write, a_write):
                try:
                    os.close(fd)
                except OSError:
                    pass
            terminate(proc)
            self._encoder = None
        return time.monotonic() - started

    def _watchdog(self) -> None:
        while not self.stop.wait(2.0):
            enc = self._encoder
            if enc and enc.poll() is None and time.monotonic() - self._last_tick_done > 10:
                self.log.error("encoder stalled for >10s; killing it")
                enc.kill()

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name="compositor", daemon=True)
        self._thread.start()

    def run(self) -> None:
        threading.Thread(target=self._decoder_loop, name="decoder", daemon=True).start()
        threading.Thread(target=self._watchdog, name="watchdog", daemon=True).start()
        backoff = Backoff(1.0, 10.0, healthy_after=30.0)
        while not self.stop.is_set():
            if self.mtx.paths() is None:
                self.log.info("waiting for MediaMTX")
                self.stop.wait(1.0)
                continue
            ran = self._run_encoder()
            if self.stop.is_set():
                break
            self.stats["encoder_restarts"] += 1
            delay = backoff.next(ran)
            self.log.error("encoder exited after %.0fs; restarting in %.1fs", ran, delay)
            self.stop.wait(delay)

    def snapshot(self) -> dict:
        with self._lock:
            depth = len(self._vq)
        return {"state": self.state, "buffer_frames": depth, **self.stats}
