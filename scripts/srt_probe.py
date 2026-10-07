#!/usr/bin/env python3
"""Sends an SRT 'induction' handshake (what Moblin sends first) and reports whether the server answers.

Usage: python3 scripts/srt_probe.py [host] [port]      (default 127.0.0.1 8890)
"""
import socket
import struct
import sys

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
port = int(sys.argv[2]) if len(sys.argv) > 2 else 8890

header = struct.pack(">IIII", 0x80000000, 0, 0, 0)  # control packet, type 0 = handshake, dest socket 0
cif = struct.pack(">IHHIIIIII", 4, 0, 2, 0, 1500, 8192, 1, 0x1234, 0) + bytes(16)
packet = header + cif
assert len(packet) == 64

for family, target in ((socket.AF_INET6 if ":" in host else socket.AF_INET, host),):
    s = socket.socket(family, socket.SOCK_DGRAM)
    s.settimeout(3)
    s.sendto(packet, (target, port))
    try:
        data, addr = s.recvfrom(2048)
        kind = struct.unpack(">I", data[:4])[0]
        print(f"REPLY from {addr[0]}:{addr[1]} ({len(data)} bytes, first word 0x{kind:08x}): the server answers SRT on {host}:{port}")
    except socket.timeout:
        print(f"NO REPLY from {host}:{port} within 3s: the server did not answer")
