#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""从 icon.svg 的几何定义生成 PNG 图标（纯标准库，无第三方依赖）。

用法：python3 tools/make-icons.py
输出：web/icons/icon-192.png、icon-512.png、apple-touch-icon.png

设计说明：图形为纯几何（圆角矩形 + 三条圆角横杠 + 一个圆点），
直接用超采样栅格化即可获得平滑边缘，无需 ImageMagick/rsvg 等外部工具。
PNG 采用不透明满幅底色（iOS 主屏幕图标会自行裁圆角，自带透明边会被切成白角）。
"""

import os
import struct
import zlib

SS = 4  # 超采样倍数
BG = (0x14, 0x16, 0x1A)
WHITE = (0xFF, 0xFF, 0xFF)
DOT = (0x4F, 0x7E, 0xF7)

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "icons")


def rounded_rect(px, py, x, y, w, h, r, size):
    """判断点是否落在圆角矩形内（坐标已缩放到画布尺寸）"""
    if px < x or px > x + w or py < y or py > y + h:
        return False
    cx = min(max(px, x + r), x + w - r)
    cy = min(max(py, y + r), y + h - r)
    dx = px - cx
    dy = py - cy
    return dx * dx + dy * dy <= r * r


def gradient_alpha(x, y, w, h, base):
    """保持与 SVG 一致的白色不透明度（0.55 / 0.3 叠加在深底上）"""
    return base


def blend(bg, fg, alpha):
    return tuple(int(round(bg[i] * (1 - alpha) + fg[i] * alpha)) for i in range(3))


def render(size):
    """返回 size×size 的 RGB 字节串"""
    n = size * SS
    scale = n / 512.0

    # 与 icon.svg 一致的几何参数（以 512 为基准）
    bars = [
        (128, 150, 164, 26, 13, 1.0),
        (128, 243, 256, 26, 13, 0.55),
        (128, 336, 200, 26, 13, 0.30),
    ]
    bars = [(x * scale, y * scale, w * scale, h * scale, r * scale, a) for x, y, w, h, r, a in bars]
    dot_cx, dot_cy, dot_r = 336 * scale, 163 * scale, 17 * scale

    # 先渲染超高分辨率像素
    rows = []
    for py in range(n):
        line = bytearray()
        for px in range(n):
            color = BG
            for x, y, w, h, r, a in bars:
                if rounded_rect(px + 0.5, py + 0.5, x, y, w, h, r, n):
                    color = blend(BG, WHITE, a)
                    break
            else:
                dx = px + 0.5 - dot_cx
                dy = py + 0.5 - dot_cy
                if dx * dx + dy * dy <= dot_r * dot_r:
                    color = DOT
            line.extend(color)
        rows.append(line)

    # 盒式降采样
    out = bytearray()
    cnt = SS * SS
    for by in range(size):
        out.append(0)  # PNG filter type 0
        for bx in range(size):
            r = g = b = 0
            for dy in range(SS):
                row = rows[by * SS + dy]
                for dx in range(SS):
                    i = ((bx * SS + dx) * 3)
                    r += row[i]
                    g += row[i + 1]
                    b += row[i + 2]
            out.extend((r // cnt, g // cnt, b // cnt))
    return bytes(out)


def write_png(path, size, raw):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as fh:
        fh.write(png)
    return len(png)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for size, name in ((192, "icon-192.png"), (512, "icon-512.png"), (180, "apple-touch-icon.png")):
        raw = render(size)
        path = os.path.join(OUT_DIR, name)
        n = write_png(path, size, raw)
        print("%-24s %dx%d  %d 字节" % (name, size, size, n))


if __name__ == "__main__":
    main()
