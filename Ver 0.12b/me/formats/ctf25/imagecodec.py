# -*- coding: utf-8 -*-
"""把 CTF 的原始像素数据转成 RGBA。

Fusion 的像素数据是 **BGR(A)** 序（蓝色在前），而 PNG 要 RGBA，所以要换通道。

各 graphicMode 的含义（来自 FusionImage.cs 的注释）::

    4  24bpp BGR，若 flags 的 Alpha 位置位，尾部还有一段 8bit alpha 平面
    6  15bpp（实际占 16 位）
    7  16bpp（5-6-5）
    8  Fusion 2.5+ 32bpp BGRA
    0~5 Android 专用，本工具不做

只要有 numpy 就用向量化（快很多），没有就退回纯 Python，功能一致。
"""

import struct

from me.formats.ctf25.imagebank import ImageEntry

try:  # numpy 是可选加速，不是硬依赖
    import numpy as _np
except Exception:  # noqa: BLE001
    _np = None

HAS_NUMPY = _np is not None


class ImageCodecError(Exception):
    pass


def _padding(width: int, point_size: int, byte_align: int = 2) -> int:
    """每行末尾要补多少「像素」。

    对应 CTFAK 的 GetPadding(width, pointSize, bytes)：
    行字节数按 byte_align 对齐，返回需要补的像素个数。
    """
    pad = byte_align - (width * point_size) % byte_align
    if pad == byte_align:
        return 0
    return -(-pad // point_size)


def to_rgba(entry: ImageEntry) -> bytes:
    """把一条图像记录解成紧凑的 RGBA 缓冲（长度 = w*h*4）。"""
    mode = entry.graphic_mode
    if mode == 8:
        return _mode8(entry)
    if mode == 4:
        return _mode4(entry)
    if mode == 7:
        return _mode16(entry, five_bit=False)
    if mode == 6:
        return _mode16(entry, five_bit=True)
    raise ImageCodecError(
        f"暂不支持 graphicMode={mode}（Android 或更老的格式），图像 #{entry.handle}")


# ------------------------------------------------------------------ mode 8
def _mode8(e: ImageEntry) -> bytes:
    """Fusion 2.5+：32 位 BGRA。"""
    w, h = e.width, e.height
    need = w * h * 4
    if len(e.pixels) < need:
        raise ImageCodecError(
            f"图像 #{e.handle} 像素数据不足: {len(e.pixels)} < {need}")

    keyed = not (e.alpha_flag or e.rgba_flag)
    tr, tg, tb, _ta = e.transparent

    if _np is not None:
        a = _np.frombuffer(e.pixels[:need], dtype=_np.uint8).reshape(h, w, 4)
        out = _np.empty((h, w, 4), dtype=_np.uint8)
        out[:, :, 0] = a[:, :, 2]      # R
        out[:, :, 1] = a[:, :, 1]      # G
        out[:, :, 2] = a[:, :, 0]      # B
        if keyed:
            hit = ((a[:, :, 0] == tb) & (a[:, :, 1] == tg) & (a[:, :, 2] == tr))
            out[:, :, 3] = _np.where(hit, 0, 255).astype(_np.uint8)
        else:
            out[:, :, 3] = a[:, :, 3]
        return out.tobytes()

    # 纯 Python：通道交换用切片赋值（C 层实现，很快）
    src = e.pixels[:need]
    out = bytearray(need)
    out[0::4] = src[2::4]
    out[1::4] = src[1::4]
    out[2::4] = src[0::4]
    out[3::4] = src[3::4]
    if keyed:
        for i in range(0, need, 4):
            if src[i] == tb and src[i + 1] == tg and src[i + 2] == tr:
                out[i + 3] = 0
    return bytes(out)


# ------------------------------------------------------------------ mode 4
def _mode4(e: ImageEntry) -> bytes:
    """24bpp BGR + 可选 8bit alpha 平面。"""
    w, h = e.width, e.height
    pad = _padding(w, 3)
    stride = w * 3 + pad * 3
    need_rgb = stride * h

    if len(e.pixels) < need_rgb:
        raise ImageCodecError(
            f"图像 #{e.handle} 像素数据不足: {len(e.pixels)} < {need_rgb}")

    tr, tg, tb, _ta = e.transparent
    keyed = not e.alpha_flag

    if _np is not None:
        raw = _np.frombuffer(e.pixels[:need_rgb], dtype=_np.uint8)
        rows = raw.reshape(h, stride)[:, :w * 3].reshape(h, w, 3)
        out = _np.empty((h, w, 4), dtype=_np.uint8)
        out[:, :, 0] = rows[:, :, 2]
        out[:, :, 1] = rows[:, :, 1]
        out[:, :, 2] = rows[:, :, 0]

        if e.alpha_flag:
            a_pad = _padding(w, 1, 4)
            a_stride = w + a_pad
            start = need_rgb
            plane = _np.frombuffer(e.pixels[start:start + a_stride * h],
                                   dtype=_np.uint8)
            if plane.size >= a_stride * h:
                out[:, :, 3] = plane.reshape(h, a_stride)[:, :w]
            else:
                out[:, :, 3] = 255
        elif keyed:
            hit = ((rows[:, :, 0] == tb) & (rows[:, :, 1] == tg)
                   & (rows[:, :, 2] == tr))
            out[:, :, 3] = _np.where(hit, 0, 255).astype(_np.uint8)
        else:
            out[:, :, 3] = 255
        return out.tobytes()

    out = bytearray(w * h * 4)
    a_start = need_rgb
    a_pad = _padding(w, 1, 4) if e.alpha_flag else 0
    for y in range(h):
        row = y * stride
        o = y * w * 4
        for x in range(w):
            s = row + x * 3
            b, g, r = e.pixels[s], e.pixels[s + 1], e.pixels[s + 2]
            d = o + x * 4
            out[d], out[d + 1], out[d + 2] = r, g, b
            if e.alpha_flag:
                ai = a_start + y * (w + a_pad) + x
                out[d + 3] = e.pixels[ai] if ai < len(e.pixels) else 255
            elif keyed and r == tr and g == tg and b == tb:
                out[d + 3] = 0
            else:
                out[d + 3] = 255
    return bytes(out)


# ------------------------------------------------------------------ mode 6/7
def _mode16(e: ImageEntry, five_bit: bool) -> bytes:
    """16bpp 5-6-5（mode 7）/ 15bpp（mode 6）。"""
    w, h = e.width, e.height
    if len(e.pixels) < w * h * 2:
        raise ImageCodecError(f"图像 #{e.handle} 像素数据不足")

    tr, tg, tb, _ta = e.transparent
    keyed = not e.alpha_flag

    if _np is not None:
        px = _np.frombuffer(e.pixels[:w * h * 2], dtype="<u2").reshape(h, w)
        if five_bit:      # 15bpp: 5-5-5，位在 10/5/0
            r5 = (px >> 10) & 0x1F
            g5 = (px >> 5) & 0x1F
            b5 = px & 0x1F
            r = (r5 << 3) | (r5 >> 2)
            g = (g5 << 3) | (g5 >> 2)
            b = (b5 << 3) | (b5 >> 2)
        else:             # 16bpp: 5-6-5
            r5 = (px >> 11) & 0x1F
            g6 = (px >> 5) & 0x3F
            b5 = px & 0x1F
            r = (r5 << 3) | (r5 >> 2)
            g = (g6 << 2) | (g6 >> 4)
            b = (b5 << 3) | (b5 >> 2)
        out = _np.empty((h, w, 4), dtype=_np.uint8)
        out[:, :, 0] = r
        out[:, :, 1] = g
        out[:, :, 2] = b
        if keyed:
            hit = (r == tr) & (g == tg) & (b == tb)
            out[:, :, 3] = _np.where(hit, 0, 255).astype(_np.uint8)
        else:
            out[:, :, 3] = 255
        return out.tobytes()

    out = bytearray(w * h * 4)
    for i in range(w * h):
        px = struct.unpack_from("<H", e.pixels, i * 2)[0]
        if five_bit:
            r = ((px & 31744) >> 10) << 3
            g = ((px & 992) >> 5) << 3
            b = (px & 31) << 3
        else:
            r = ((px & 63488) >> 11) << 3
            g = ((px & 2016) >> 5) << 2
            b = (px & 31) << 3
        d = i * 4
        out[d], out[d + 1], out[d + 2] = r, g, b
        if keyed and r == tr and g == tg and b == tb:
            out[d + 3] = 0
        else:
            out[d + 3] = 255
    return bytes(out)
