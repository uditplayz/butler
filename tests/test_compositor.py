import threading
import unittest

from butler.compositor import Compositor
from butler.config import load

SLATE = b"S"


def make(**env):
    cfg = load({"VIDEO_SIZE": "640x360", "VIDEO_FPS": "30", "JITTER_MS": "100",
                "LIVE_STALL_SECONDS": "3", "SLATE_MIN_HOLD_SECONDS": "2", **env})
    comp = Compositor(cfg, SLATE, None, threading.Event())
    comp._slate_since = 0.0  # tests use a fake clock starting at 100.0
    return comp, cfg


def feed(comp, cfg, now, frames):
    """Pretend the decoder delivered `frames` frames (and matching audio) at time `now`."""
    with comp._lock:
        comp._last_frame_t = now
        for i in range(frames):
            comp._vq.append(bytearray(b"L" * 4))
        comp._aq += bytes(frames * cfg.audio_bytes_per_tick)


class CompositorTests(unittest.TestCase):
    def test_starts_on_slate_and_never_returns_nothing(self):
        comp, cfg = make()
        video, audio = comp.next_tick(100.0)
        self.assertEqual(video, SLATE)
        self.assertEqual(len(audio), cfg.audio_bytes_per_tick)

    def test_goes_live_once_buffer_fills(self):
        comp, cfg = make()
        feed(comp, cfg, 100.0, cfg.jitter_frames)
        video, _ = comp.next_tick(100.0)
        self.assertEqual(comp.state, "live")
        self.assertEqual(bytes(video), b"LLLL")

    def test_freezes_then_slates_when_source_stops(self):
        comp, cfg = make()
        feed(comp, cfg, 100.0, cfg.jitter_frames)
        now = 100.0
        while comp._vq or comp.state == "slate":
            comp.next_tick(now)
            now += 1 / 30
            if comp.state == "slate":
                self.fail("dropped to slate too early")
        # Buffer empty: frame is frozen, not the slate, until LIVE_STALL_SECONDS passes.
        video, audio = comp.next_tick(now + 1.0)
        self.assertEqual(bytes(video), b"LLLL")
        self.assertEqual(audio, bytes(cfg.audio_bytes_per_tick))
        video, _ = comp.next_tick(now + 3.5)
        self.assertEqual(video, SLATE)
        self.assertEqual(comp.state, "slate")

    def test_short_blip_resumes_without_slate(self):
        comp, cfg = make()
        feed(comp, cfg, 100.0, cfg.jitter_frames)
        now = 100.0
        while comp._vq:
            comp.next_tick(now)
            now += 1 / 30
        comp.next_tick(now + 1.0)  # starved, frozen
        feed(comp, cfg, now + 1.5, cfg.jitter_frames + 2)  # phone reconnects
        video, _ = comp.next_tick(now + 1.5)
        self.assertEqual(comp.state, "live")
        self.assertEqual(bytes(video), b"LLLL")

    def test_slate_hold_prevents_flapping(self):
        comp, cfg = make()
        comp._to_slate(100.0)
        feed(comp, cfg, 100.5, cfg.jitter_frames)
        video, _ = comp.next_tick(100.5)  # only 0.5s since slate
        self.assertEqual(video, SLATE)
        feed(comp, cfg, 102.5, cfg.jitter_frames)
        comp.next_tick(102.5)
        self.assertEqual(comp.state, "live")

    def test_burst_is_trimmed_to_bound_latency(self):
        comp, cfg = make()
        feed(comp, cfg, 100.0, cfg.jitter_frames)
        comp.next_tick(100.0)
        feed(comp, cfg, 100.1, comp._v_cap)  # big burst after a network hiccup
        before = len(comp._vq)
        comp.next_tick(100.1)
        self.assertLessEqual(len(comp._vq), before - 2)  # popped one + caught up one


if __name__ == "__main__":
    unittest.main()
