#!/usr/bin/env bash
# One-time setup for running Butler natively on macOS (no Docker needed).
set -euo pipefail
cd "$(dirname "$0")/.."

command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh" >&2; exit 1; }
xcode-select -p >/dev/null 2>&1 || { echo "Run: xcode-select --install   (then re-run this script)" >&2; exit 1; }

brew install ffmpeg mediamtx python   # srtla_rec is Linux-only; on a Mac you use plain SRT
./scripts/init.sh
echo
echo "Next: edit .env (add TWITCH_STREAM_KEY / YOUTUBE_STREAM_KEY), then run ./scripts/run-local.sh"
