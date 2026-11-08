# -*- coding: utf-8 -*-
"""最小 PNG 写出器。

自己写是为了不依赖 Pillow —— 发布包只要装了 Python 就能跑。
只支持 8 位 RGBA（我们提取出来的原始像素正好是这个格式）。
"""

import struct
import zlib


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def encode_rgba(width: int, height: int, rgba: bytes, level: int = 6) -> bytes:
    """把紧凑的 RGBA 像素缓冲编码成 PNG 字节流。"""
    if width <= 0 or height <= 0:
        raise ValueError(f"PNG 尺寸非法: {width}x{height}")
    stride = width * 4
    need = stride * height
    if len(rgba) < need:
        # 数据不足时补零，宁可出一张有黑边的图也不要崩
        rgba = bytes(rgba) + b"\x00" * (need - len(rgba))

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0 (None)
        raw += rgba[y * stride:(y + 1) * stride]

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), level))
        + _chunk(b"IEND", b"")
    )


def write_rgba(path, width: int, height: int, rgba: bytes) -> None:
    """把 RGBA 缓冲写成 PNG 文件。"""
    with open(path, "wb") as f:
        f.write(encode_rgba(width, height, rgba))


def rgba_from_bgra(buf: bytes) -> bytes:
    """BGRA -> RGBA。Fusion 的图像数据是 BGRA 序。"""
    out = bytearray(len(buf))
    out[0::4] = buf[2::4]
    out[1::4] = buf[1::4]
    out[2::4] = buf[0::4]
    out[3::4] = buf[3::4]
    return bytes(out)
