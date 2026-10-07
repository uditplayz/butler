# Butler: a stable IRL streaming server

Stream from **Moblin** (or OBS, Larix, ...) to **Twitch and YouTube** through a server you control.

```
Moblin ──SRT──► MediaMTX ──► decoder ──► compositor (constant 1080p30) ──► x264 ──► MediaMTX
(iPhone)                         │              ▲ slate if the phone is gone            │
                                 └── phone drops? viewers see "Be right back", not a dead stream
                                                                        ├─► ffmpeg -c copy ─► Twitch
                                                                        └─► ffmpeg -c copy ─► YouTube
```

**Why it is stable.** Twitch/YouTube end your broadcast when the incoming feed stops. A phone on
cellular will drop now and then. So the phone never talks to Twitch directly:

- The **compositor** emits exactly one frame + one audio chunk every 1/30 s no matter what: the live
  frame, a frozen frame during a short blip (< 3 s), or a "Be right back" slate. The encoder and the
  platforms never see a gap, so **Twitch and YouTube stay connected while your phone reconnects**, and
  the live feed returns automatically.
- Output is a **constant-bitrate 1080p30 H.264/AAC** stream with a 2 s keyframe interval, which is what
  platforms want, regardless of how bumpy the phone's feed is (adaptive bitrate, HEVC, odd resolutions).
- Every process (MediaMTX, decoder, encoder, each destination) is supervised: crashes, stalls and
  dead connections are detected and restarted independently. A broken YouTube link never touches Twitch.
- Stream keys are scrubbed from logs; only the publisher account can push to the server.

Tested here end to end (real MediaMTX + ffmpeg, simulated phone, local RTMP receiver standing in for Twitch):
phone killed for 12 s → slate after 3 s → phone back → live again, **0 relay reconnects**, output
duration matched wall-clock, 1920x1080 @ 30 fps. HEVC input decodes correctly. Not tested: a real
Twitch/YouTube ingest, real Moblin, SRTLA bonding (needs IPv6 + a Linux host, absent in my sandbox),
and the Docker files (no Docker daemon in my sandbox).

## Running it on your Mac

```bash
git clone <this repo> && cd butler
./scripts/setup-mac.sh        # brew install ffmpeg mediamtx python; creates .env with random credentials
nano .env                     # add TWITCH_STREAM_KEY (and YOUTUBE_STREAM_KEY if you want both)
./scripts/run-local.sh        # starts everything, restarts anything that dies, keeps the Mac awake
```

Dashboard: <http://127.0.0.1:8080/>. `init.sh` prints the SRT stream ID to paste into Moblin.

### The part that decides your real-world stability: reachability

Your phone is on cellular; the server is on your Mac, behind your home router. Something must make
**UDP port 8890** reachable from the internet:

1. **Port-forward** UDP 8890 on your router to the Mac (give the Mac a fixed LAN IP), and use your
   public IP or a dynamic-DNS name (e.g. DuckDNS) in Moblin. This will not work if your ISP uses
   CGNAT (common on mobile/fibre ISPs: your router's WAN IP differs from what whatismyip shows).
2. **Tailscale** on both Mac and phone: works through CGNAT with no port forwarding. Use the Mac's
   Tailscale IP in Moblin. Fine for single-link SRT.
3. **A small Linux VPS** (see below): the most robust; same software.

Also be realistic about what the Mac setup can't give you: **home upload must carry ~7 Mbps per
destination** (6 Mbps video + audio + overhead, so ~14 Mbps for Twitch + YouTube), and if your home
internet, power or Mac sleeps/updates, the stream dies, because that is the single point of failure.
CPU is not an issue: x264 `veryfast` 1080p30 is roughly one core on Apple Silicon. Don't close the lid, and
allow incoming connections for `mediamtx` when macOS asks (or in System Settings → Network → Firewall).

**macOS notes:** the SRT listener is bound to IPv4 (`0.0.0.0:8890`) because the default dual-stack socket ignores IPv4 clients on macOS. Homebrew's ffmpeg has no SRT support, so use `scripts/srt_probe.py` to test the server.

**macOS and SRTLA:** the SRTLA bonding receiver (`srtla_rec`) is Linux-only (it uses `epoll`), so on a
Mac you use plain SRT. SRT already retransmits lost packets; give it a generous latency. Bonding
(Wi-Fi + cellular together) needs a Linux host.

## Moblin settings

Settings → Streams → your stream (type SRT(LA)):

| Field | Value |
|---|---|
| URL | `srt://<ip-or-hostname>:8890?streamid=publish:live:<user>:<pass>` (the stream ID goes **in the URL**; Moblin sends an empty one otherwise and the server rejects it). Linux with bonding: `srtla://host:5000?streamid=...` |
| Latency | **3000 ms or more** for IRL on cellular (higher = more loss tolerance, a few seconds more delay) |
| Video | H.264 or **HEVC** (Butler converts to H.264 for the platforms), 1920x1080, 30 fps, keyframe interval 2 s |
| Bitrate | 5–8 Mbps, with Moblin's adaptive bitrate on; Butler re-encodes to a fixed `VIDEO_BITRATE_KBPS` |
| Audio | AAC |

(Setting names move between Moblin versions; match them by meaning.)

**OBS**: Settings → Stream → Custom, Server `srt://host:8890?streamid=publish:live:<user>:<pass>&latency=2000000`
(latency is in microseconds there), or RTMP: server `rtmp://host:1935/live?user=<user>&pass=<pass>`, empty key.

## Running it on a Linux VPS (the robust option)

```bash
sudo ./scripts/setup-vps.sh   # docker, firewall (5000/udp SRTLA, 8890/udp SRT, 1935/tcp RTMP), sysctl tuning
./scripts/init.sh && nano .env
docker compose up -d --build
docker compose logs -f butler
```

Pick a VPS with ≥ 4 vCPU (or 2 with `X264_PRESET=ultrafast`) and unmetered/ample bandwidth, in a
region near you or near Twitch's ingest. Then SRTLA works: Moblin `srtla://host:5000`.

## Configuration

Everything is in `.env` (documented inline in `.env.example`): destinations, `MODE`
(`resilient` default / `passthrough` = plain copy with no slate and no CPU), resolution/fps/bitrate,
`LIVE_STALL_SECONDS` (blip tolerance before the slate), `JITTER_MS`, slate text or `SLATE_IMAGE`.
Twitch 1080p30 at 6000 kbps is within Twitch's recommendations; for 60 fps set `VIDEO_FPS=60` and
`VIDEO_BITRATE_KBPS=8000` (needs ~2x CPU).

## Operations

- `curl localhost:8080/status`: ingest connected? compositor state (`live`/`slate`), per-destination state and bitrate.
- Logs show `live source lost -> showing slate` / `back on air`, and relay reconnects.
- Rotating the ingest password: edit `.env`, re-run `./scripts/init.sh`, restart.
- Tests: `python3 -m unittest discover -s tests -t .`

## Layout

`butler/compositor.py` (switcher + encoder) · `butler/relay.py` (per-destination ffmpeg) ·
`butler/config.py` · `mediamtx/` (config template) · `scripts/` · `docker-compose.yml` · `srtla/`
