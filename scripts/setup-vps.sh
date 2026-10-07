#!/usr/bin/env bash
# Ubuntu/Debian VPS prep: Docker, firewall, and network tuning that matters for SRT. Run as root.
set -euo pipefail
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh

cat > /etc/sysctl.d/99-butler.conf <<'SYS'
net.core.rmem_max=26214400
net.core.wmem_max=26214400
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
SYS
sysctl --system >/dev/null

if command -v ufw >/dev/null; then
  ufw allow OpenSSH
  ufw allow 5000/udp comment 'SRTLA (Moblin bonding)'
  ufw allow 8890/udp comment 'SRT'
  ufw allow 1935/tcp  comment 'RTMP (OBS)'
  ufw --force enable
fi
echo "Done. Next: ./scripts/init.sh, edit .env, docker compose up -d --build"
