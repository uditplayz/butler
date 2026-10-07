import unittest

from butler.config import ConfigError, load
from butler.util import Redactor


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        c = load({})
        self.assertEqual((c.width, c.height, c.fps, c.mode), (1920, 1080, 30, "resilient"))
        self.assertEqual(c.destinations, ())
        self.assertEqual(c.relay_source_path, "program")

    def test_destinations_and_secrets(self):
        c = load({"TWITCH_STREAM_KEY": "live_123_abcDEF", "YOUTUBE_STREAM_KEY": "abcd-efgh-ijkl",
                  "CUSTOM_RTMP_URLS": "rtmps://example.com:443/app/secretkey"})
        self.assertEqual([d.name for d in c.destinations], ["twitch", "youtube", "custom1"])
        self.assertEqual(c.destinations[0].url, "rtmp://live.twitch.tv/app/live_123_abcDEF")
        self.assertEqual(c.destinations[0].display, "rtmp://live.twitch.tv")
        self.assertIn("live_123_abcDEF", c.secrets)

    def test_passthrough_reads_live_path(self):
        self.assertEqual(load({"MODE": "passthrough"}).relay_source_path, "live")

    def test_rejects_bad_values(self):
        for env in ({"MODE": "x"}, {"VIDEO_SIZE": "big"}, {"VIDEO_SIZE": "1921x1080"},
                    {"VIDEO_FPS": "29"}, {"VIDEO_BITRATE_KBPS": "abc"},
                    {"TWITCH_STREAM_KEY": "a b;rm"}, {"CUSTOM_RTMP_URLS": "http://x/y"}):
            with self.assertRaises(ConfigError, msg=env):
                load(env)

    def test_redactor_hides_keys(self):
        r = Redactor(["live_123_abcDEF"])
        self.assertEqual(r.redact("Failed rtmp://x/app/live_123_abcDEF"), "Failed rtmp://x/app/***")


if __name__ == "__main__":
    unittest.main()
