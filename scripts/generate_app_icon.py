#!/usr/bin/env python3
from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path


SIZE = 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICNS_ICONSET_ENTRIES = (
    ("icp4", "icon_16x16.png", 16, 16),
    ("ic11", "icon_16x16@2x.png", 32, 32),
    ("icp5", "icon_32x32.png", 32, 32),
    ("ic12", "icon_32x32@2x.png", 64, 64),
    ("ic07", "icon_128x128.png", 128, 128),
    ("ic13", "icon_128x128@2x.png", 256, 256),
    ("ic08", "icon_256x256.png", 256, 256),
    ("ic14", "icon_256x256@2x.png", 512, 512),
    ("ic09", "icon_512x512.png", 512, 512),
    ("ic10", "icon_512x512@2x.png", 1024, 1024),
)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def smoothstep(edge0: float, edge1: float, value: float) -> float:
    t = clamp((value - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def mix(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def blend_pixel(canvas: bytearray, width: int, x: int, y: int, color: tuple[int, int, int, int]) -> None:
    if x < 0 or y < 0 or x >= width or y >= width:
        return
    offset = (y * width + x) * 4
    src_a = color[3] / 255.0
    if src_a <= 0:
        return
    dst_a = canvas[offset + 3] / 255.0
    out_a = src_a + dst_a * (1.0 - src_a)
    if out_a <= 0:
        return
    for index in range(3):
        src = color[index] / 255.0
        dst = canvas[offset + index] / 255.0
        out = (src * src_a + dst * dst_a * (1.0 - src_a)) / out_a
        canvas[offset + index] = int(round(out * 255))
    canvas[offset + 3] = int(round(out_a * 255))


def rounded_rect_alpha(x: float, y: float, left: float, top: float, right: float, bottom: float, radius: float) -> float:
    cx = max(left + radius, min(x, right - radius))
    cy = max(top + radius, min(y, bottom - radius))
    distance = math.hypot(x - cx, y - cy)
    if left + radius <= x <= right - radius or top + radius <= y <= bottom - radius:
        inside_box = left <= x <= right and top <= y <= bottom
        if inside_box and distance <= radius:
            return 1.0
    return 1.0 - smoothstep(radius - 1.5, radius + 1.5, distance)


def distance_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    vx = bx - ax
    vy = by - ay
    wx = px - ax
    wy = py - ay
    length_sq = vx * vx + vy * vy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = clamp((wx * vx + wy * vy) / length_sq)
    cx = ax + t * vx
    cy = ay + t * vy
    return math.hypot(px - cx, py - cy)


def draw_capsule_line(
    canvas: bytearray,
    start: tuple[float, float],
    end: tuple[float, float],
    radius: float,
    color: tuple[int, int, int, int],
) -> None:
    ax, ay = start
    bx, by = end
    pad = int(radius + 4)
    min_x = int(max(0, min(ax, bx) - pad))
    max_x = int(min(SIZE - 1, max(ax, bx) + pad))
    min_y = int(max(0, min(ay, by) - pad))
    max_y = int(min(SIZE - 1, max(ay, by) + pad))
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            distance = distance_to_segment(x + 0.5, y + 0.5, ax, ay, bx, by)
            alpha = color[3] * (1.0 - smoothstep(radius - 1.2, radius + 1.2, distance))
            if alpha > 0:
                blend_pixel(canvas, SIZE, x, y, (color[0], color[1], color[2], int(alpha)))


def draw_circle(canvas: bytearray, center: tuple[float, float], radius: float, color: tuple[int, int, int, int]) -> None:
    cx, cy = center
    min_x = int(max(0, cx - radius - 4))
    max_x = int(min(SIZE - 1, cx + radius + 4))
    min_y = int(max(0, cy - radius - 4))
    max_y = int(min(SIZE - 1, cy + radius + 4))
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            distance = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
            alpha = color[3] * (1.0 - smoothstep(radius - 1.2, radius + 1.2, distance))
            if alpha > 0:
                blend_pixel(canvas, SIZE, x, y, (color[0], color[1], color[2], int(alpha)))


def draw_background(canvas: bytearray) -> None:
    for y in range(SIZE):
        ny = y / (SIZE - 1)
        for x in range(SIZE):
            nx = x / (SIZE - 1)
            alpha = rounded_rect_alpha(x + 0.5, y + 0.5, 32, 32, 992, 992, 224)
            if alpha <= 0:
                continue

            diagonal = (nx + ny) * 0.5
            r = mix(16, 21, diagonal)
            g = mix(31, 47, diagonal)
            b = mix(47, 72, diagonal)

            teal_glow = clamp(1.0 - math.hypot(nx - 0.25, ny - 0.18) / 0.72)
            violet_glow = clamp(1.0 - math.hypot(nx - 0.78, ny - 0.82) / 0.62)
            amber_glow = clamp(1.0 - math.hypot(nx - 0.22, ny - 0.84) / 0.55)
            r += 25 * teal_glow + 35 * violet_glow + 18 * amber_glow
            g += 86 * teal_glow + 20 * violet_glow + 18 * amber_glow
            b += 94 * teal_glow + 55 * violet_glow

            # Subtle deterministic texture prevents flat-band gradients in Dock scaling.
            grain = ((x * 17 + y * 31) % 19 - 9) * 0.38
            offset = (y * SIZE + x) * 4
            canvas[offset] = int(clamp((r + grain) / 255) * 255)
            canvas[offset + 1] = int(clamp((g + grain) / 255) * 255)
            canvas[offset + 2] = int(clamp((b + grain) / 255) * 255)
            canvas[offset + 3] = int(alpha * 255)


def draw_mark(canvas: bytearray) -> None:
    # Soft shadow under the mark.
    draw_capsule_line(canvas, (318, 724), (494, 254), 72, (0, 0, 0, 72))
    draw_capsule_line(canvas, (494, 254), (710, 724), 72, (0, 0, 0, 72))
    draw_capsule_line(canvas, (392, 552), (632, 552), 53, (0, 0, 0, 54))

    # Memory graph traces.
    draw_capsule_line(canvas, (320, 722), (494, 266), 18, (101, 228, 213, 150))
    draw_capsule_line(canvas, (494, 266), (708, 722), 18, (143, 179, 255, 140))
    draw_capsule_line(canvas, (390, 554), (632, 554), 15, (255, 190, 116, 138))

    # Primary abstract A mark.
    draw_capsule_line(canvas, (318, 724), (494, 254), 48, (244, 250, 255, 236))
    draw_capsule_line(canvas, (494, 254), (710, 724), 48, (244, 250, 255, 232))
    draw_capsule_line(canvas, (392, 552), (632, 552), 36, (244, 250, 255, 218))

    # Inner cut highlights make the mark feel like a folded workspace ribbon.
    draw_capsule_line(canvas, (408, 684), (512, 394), 13, (38, 64, 88, 92))
    draw_capsule_line(canvas, (564, 396), (660, 684), 13, (38, 64, 88, 80))

    # Nodes: capture, organize, remember.
    draw_circle(canvas, (494, 254), 52, (101, 228, 213, 255))
    draw_circle(canvas, (318, 724), 44, (255, 188, 111, 248))
    draw_circle(canvas, (710, 724), 44, (143, 179, 255, 248))
    draw_circle(canvas, (494, 254), 22, (250, 255, 255, 220))
    draw_circle(canvas, (318, 724), 16, (255, 250, 238, 215))
    draw_circle(canvas, (710, 724), 16, (250, 252, 255, 215))

    # Small top-right star for agent assistance without adding text.
    draw_circle(canvas, (744, 302), 17, (255, 222, 132, 230))
    draw_capsule_line(canvas, (744, 270), (744, 334), 5, (255, 238, 182, 210))
    draw_capsule_line(canvas, (712, 302), (776, 302), 5, (255, 238, 182, 210))


def write_png(path: Path, width: int, height: int, rgba: bytearray) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    rows = bytearray()
    stride = width * 4
    for y in range(height):
        rows.append(0)
        start = y * stride
        rows.extend(rgba[start : start + stride])

    payload = b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(bytes(rows), 9)),
            chunk(b"IEND", b""),
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def generate(path: Path) -> None:
    canvas = bytearray(SIZE * SIZE * 4)
    draw_background(canvas)
    draw_mark(canvas)
    write_png(path, SIZE, SIZE, canvas)


def png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        raise ValueError("not a PNG file")
    return struct.unpack(">II", data[16:24])


def write_icns_from_iconset(iconset: Path, output: Path) -> None:
    chunks = []
    for chunk_type, filename, expected_width, expected_height in ICNS_ICONSET_ENTRIES:
        png_path = iconset / filename
        data = png_path.read_bytes()
        width, height = png_dimensions(data)
        if (width, height) != (expected_width, expected_height):
            raise ValueError(f"{png_path} is {width}x{height}, expected {expected_width}x{expected_height}")
        payload_length = len(data) + 8
        chunks.append(chunk_type.encode("ascii") + struct.pack(">I", payload_length) + data)

    body = b"".join(chunks)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--icns-from-iconset":
        if len(sys.argv) != 4:
            print("Usage: generate_app_icon.py --icns-from-iconset ICONSET_PATH OUTPUT_ICNS", file=sys.stderr)
            return 2
        write_icns_from_iconset(Path(sys.argv[2]), Path(sys.argv[3]))
        print(sys.argv[3])
        return 0

    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("macos/AylaClient/AppIcon.png")
    generate(output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
