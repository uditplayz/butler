#!/usr/bin/env bash
# Builds srtla_rec (the SRTLA bonding receiver) into runtime/bin/. Works on macOS and Linux.
# Needs: git, cmake, a C++ compiler (macOS: `xcode-select --install`, `brew install cmake`).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(uname)" = Darwin ]; then
  echo "srtla_rec uses Linux epoll and cannot run on macOS; skipping." >&2
  echo "On a Mac use plain SRT (port 8890). For SRTLA bonding, run Butler on a Linux host." >&2
  exit 0
fi

SRTLA_REPO=https://github.com/irlserver/srtla.git
SRTLA_REF=7fa6985dedd5028b8939e2ac3b32136ca7ad8d4c   # pinned; bump deliberately

src=runtime/srtla-src
mkdir -p runtime/bin
if [ ! -d "$src/.git" ]; then
  git clone --quiet "$SRTLA_REPO" "$src"
fi
git -C "$src" fetch --quiet origin "$SRTLA_REF" 2>/dev/null || true
git -C "$src" checkout --quiet "$SRTLA_REF"
git -C "$src" submodule update --init --quiet

cmake -S "$src" -B "$src/build" -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build "$src/build" --target srtla_rec -j "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"
cp "$src/build/srtla_rec" runtime/bin/srtla_rec
echo "Built runtime/bin/srtla_rec"
