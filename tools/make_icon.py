#!/usr/bin/env python3
"""Generate webapp/icon.png — the MIA Glance app icon.

Meta's Web Apps toolkit wants a PNG favicon (>= 52x52); SVGs are not
supported there. This renders the same mark as icon.svg (three rising
bars + status dot, white on transparent) at 256x256 using only the
Python standard library.

Usage:
    python tools/make_icon.py
"""

import struct
import zlib
from pathlib import Path

SIZE = 256
SS = 4  # supersample factor for smooth edges


def in_round_rect(x, y, x0, y0, x1, y1, r):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def in_circle(x, y, cx, cy, r):
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def render():
    # Work in a 64x64 design space (same as icon.svg), supersampled.
    big = SIZE * SS
    px = [[0] * big for _ in range(big)]

    def draw(fn):
        for by in range(big):
            for bx in range(big):
                gx = (bx + 0.5) * 64.0 / big
                gy = (by + 0.5) * 64.0 / big
                if fn(gx, gy):
                    px[by][bx] = 255

    # Three rising bars (from icon.svg) + status dot.
    draw(lambda x, y: in_round_rect(x, y, 12, 36, 22, 52, 5))
    draw(lambda x, y: in_round_rect(x, y, 27, 26, 37, 52, 5))
    draw(lambda x, y: in_round_rect(x, y, 42, 16, 52, 52, 5))
    draw(lambda x, y: in_circle(x, y, 47, 8, 5))

    # Downsample to SIZE x SIZE grayscale (white, alpha from coverage).
    out = bytearray()
    for y in range(SIZE):
        out.append(0)  # filter: none
        for x in range(SIZE):
            s = 0
            for dy in range(SS):
                for dx in range(SS):
                    s += px[y * SS + dy][x * SS + dx]
            a = s // (SS * SS)
            out += bytes((255, 255, 255, a))
    return bytes(out)


def chunk(typ, data):
    c = struct.pack(">I", len(data)) + typ + data
    c += struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
    return c


def main():
    raw = render()
    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)  # RGBA
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    out = Path(__file__).resolve().parent.parent / "webapp" / "icon.png"
    out.write_bytes(png)
    print(f"Wrote {out} ({len(png)} bytes)")


if __name__ == "__main__":
    main()
