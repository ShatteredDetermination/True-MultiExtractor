# -*- coding: utf-8 -*-
"""GameMaker 纹理页解码与裁切。

TXTR 里的 blob 有三种形态，实测都遇到过：

    \\x89PNG…   裸 PNG
    2zoq        QOI + BZ2：magic + u16 w + u16 h + u32 解压尺寸 + BZh 流
    qoif        裸 QOI
    BZh…        BZ2 里裹着 QOI

按 TPAG 坐标切精灵必须拿到 RGBA 像素，PNG 不能只转发原始字节，
所以这里自带一个 PNG 解码器（非隔行、8/16 位、色型 0/2/3/4/6）。
只依赖标准库。
"""

import struct
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"
QOI_MAGIC = b"qoif"
QOZ2_MAGIC = b"2zoq"
BZ2_MAGIC = b"BZh"


class ImageError(Exception):
    """纹理数据没法解。"""


# ------------------------------------------------------------------ PNG
def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def png_info(data, off=0):
    """读出 PNG 的宽高，并返回 IEND 之后的位置。不解码像素。"""
    if data[off:off + 8] != PNG_SIG:
        raise ImageError("不是 PNG")
    p = off + 8
    w = h = None
    n = len(data)
    while p + 8 <= n:
        ln = struct.unpack_from(">I", data, p)[0]
        typ = data[p + 4:p + 8]
        body = p + 8
        if body + ln + 4 > n:
            raise ImageError("PNG chunk 越界")
        if typ == b"IHDR":
            w, h = struct.unpack_from(">II", data, body)
        elif typ == b"IEND":
            return w, h, body + ln + 4
        p = body + ln + 4
    raise ImageError("PNG 没有 IEND")


def decode_png(data, off=0):
    """PNG -> (w, h, rgba)。非隔行，位深 8/16，色型 0/2/3/4/6。"""
    if data[off:off + 8] != PNG_SIG:
        raise ImageError("不是 PNG")
    p = off + 8
    n = len(data)
    w = h = depth = ctype = interlace = None
    palette = b""
    trns = b""
    idat = bytearray()

    while p + 8 <= n:
        ln = struct.unpack_from(">I", data, p)[0]
        typ = data[p + 4:p + 8]
        body = p + 8
        if body + ln + 4 > n:
            raise ImageError("PNG chunk 越界")
        chunk = data[body:body + ln]
        if typ == b"IHDR":
            w, h, depth, ctype, _comp, _filt, interlace = struct.unpack(
                ">IIBBBBB", chunk[:13])
        elif typ == b"PLTE":
            palette = chunk
        elif typ == b"tRNS":
            trns = chunk
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
        p = body + ln + 4

    if not w or not h:
        raise ImageError("PNG 缺 IHDR")
    if interlace:
        raise ImageError("隔行 PNG 未支持")
    if depth not in (8, 16):
        raise ImageError(f"PNG 位深 {depth} 未支持")
    ch = _CHANNELS.get(ctype)
    if ch is None:
        raise ImageError(f"PNG 色型 {ctype} 未支持")

    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ImageError(f"PNG IDAT 解压失败: {exc}")

    bpp = ch * (depth // 8)
    stride = w * bpp
    if len(raw) < (stride + 1) * h:
        raise ImageError("PNG 像素数据不完整")

    # 逐行反滤波
    out = bytearray(stride * h)
    prev = bytearray(stride)
    src = 0
    for y in range(h):
        ft = raw[src]
        src += 1
        line = bytearray(raw[src:src + stride])
        src += stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        elif ft != 0:
            raise ImageError(f"PNG 滤波类型 {ft} 非法")
        out[y * stride:(y + 1) * stride] = line
        prev = line

    # 取高位字节，统一压成 8 位
    if depth == 16:
        out = bytearray(out[0::2])

    rgba = bytearray(w * h * 4)
    if ctype == 6:
        rgba[:] = out[:w * h * 4]
    elif ctype == 2:
        for i in range(w * h):
            rgba[i * 4:i * 4 + 3] = out[i * 3:i * 3 + 3]
            rgba[i * 4 + 3] = 255
    elif ctype == 0:
        for i in range(w * h):
            g = out[i]
            rgba[i * 4:i * 4 + 4] = bytes((g, g, g, 255))
    elif ctype == 4:
        for i in range(w * h):
            g = out[i * 2]
            rgba[i * 4:i * 4 + 4] = bytes((g, g, g, out[i * 2 + 1]))
    elif ctype == 3:
        for i in range(w * h):
            idx = out[i]
            o = idx * 3
            if o + 3 > len(palette):
                rgba[i * 4:i * 4 + 4] = b"\x00\x00\x00\xff"
                continue
            a = trns[idx] if idx < len(trns) else 255
            rgba[i * 4:i * 4 + 4] = bytes(
                (palette[o], palette[o + 1], palette[o + 2], a))
    return w, h, bytes(rgba)


# ------------------------------------------------------------------ QOI
def decode_qoi_stream(data, off, w, h):
    """从 off 开始按 QOI 操作码流解出 w*h 的 RGBA。

    不含那 14 字节文件头 —— GameMaker 的 2zoq 里就是这种裸流，宽高只在
    2zoq 自己的头里，所以必须能把头和流分开解。
    """
    if not (0 < w <= 65536 and 0 < h <= 65536):
        raise ImageError(f"QOI 尺寸非法: {w}x{h}")
    p = off

    index = [(0, 0, 0, 0)] * 64
    r = g = b = 0
    a = 255
    out = bytearray(w * h * 4)
    o = 0
    total = w * h
    n = len(data)

    while o < total:
        if p >= n:
            raise ImageError("QOI 流提前结束")
        b1 = data[p]
        p += 1
        if b1 == 0xFE:
            if p + 3 > n:
                raise ImageError("QOI RGB 越界")
            r, g, b = data[p], data[p + 1], data[p + 2]
            p += 3
        elif b1 == 0xFF:
            if p + 4 > n:
                raise ImageError("QOI RGBA 越界")
            r, g, b, a = data[p], data[p + 1], data[p + 2], data[p + 3]
            p += 4
        else:
            tag = b1 >> 6
            if tag == 0:
                r, g, b, a = index[b1 & 0x3F]
            elif tag == 1:
                r = (r + ((b1 >> 4) & 3) - 2) & 0xFF
                g = (g + ((b1 >> 2) & 3) - 2) & 0xFF
                b = (b + (b1 & 3) - 2) & 0xFF
            elif tag == 2:
                if p >= n:
                    raise ImageError("QOI LUMA 越界")
                b2 = data[p]
                p += 1
                dg = (b1 & 0x3F) - 32
                r = (r + dg + ((b2 >> 4) & 0x0F) - 8) & 0xFF
                g = (g + dg) & 0xFF
                b = (b + dg + (b2 & 0x0F) - 8) & 0xFF
            else:
                run = (b1 & 0x3F) + 1
                for _ in range(run):
                    if o >= total:
                        break
                    out[o:o + 4] = bytes((r, g, b, a))
                    o += 4
                index[(r * 3 + g * 5 + b * 7 + a * 11) % 64] = (r, g, b, a)
                continue
        out[o:o + 4] = bytes((r, g, b, a))
        o += 4
        index[(r * 3 + g * 5 + b * 7 + a * 11) % 64] = (r, g, b, a)

    return bytes(out)


def decode_qoi(data, off=0):
    """带头的 QOI 文件 -> (w, h, rgba)。"""
    if data[off:off + 4] != QOI_MAGIC:
        raise ImageError("不是 QOI")
    w, h = struct.unpack_from(">II", data, off + 4)
    return w, h, decode_qoi_stream(data, off + 14, w, h)


# ------------------------------------------------------------------ 统一入口
def sniff(data, off=0):
    """返回 blob 的格式名，认不出返回 ""。"""
    head = bytes(data[off:off + 4])
    if head == PNG_SIG[:4]:
        return "png"
    if head == QOZ2_MAGIC:
        return "2zoq"
    if head == QOI_MAGIC:
        return "qoi"
    if head == BZ2_MAGIC:
        return "bz2"
    return ""


def decode_texture(data, off=0):
    """blob -> (w, h, rgba, kind)。kind 见 sniff()。

    data 可以是 bytes 或 mmap；off 是 blob 的文件偏移。
    """
    kind = sniff(data, off)
    if kind == "png":
        w, h, rgba = decode_png(data, off)
        return w, h, rgba, kind
    if kind == "2zoq":
        import bz2
        w, h = struct.unpack_from("<HH", data, off + 4)
        try:
            payload = bz2.decompress(bytes(data[off + 12:]))
        except (OSError, EOFError) as exc:
            raise ImageError(f"2zoq BZ2 解压失败: {exc}")
        if payload[:4] == QOI_MAGIC:
            qw, qh, rgba = decode_qoi(payload)
            return qw, qh, rgba, kind
        if not (w and h):
            raise ImageError("2zoq 头里的宽高为 0")
        # 长度正好等于裸 RGBA 就按裸 RGBA 收，否则当无头 QOI 流解。
        # GameMaker 的 2zoq 里就是剥掉 14 字节头的 QOI 操作码流。
        if len(payload) == w * h * 4:
            return w, h, payload, kind
        try:
            return w, h, decode_qoi_stream(payload, 0, w, h), kind
        except ImageError as exc:
            if len(payload) >= w * h * 4:
                return w, h, payload[:w * h * 4], kind
            raise ImageError(f"2zoq 载荷既不是裸 RGBA 也不是 QOI 流: {exc}")
    if kind == "qoi":
        w, h, rgba = decode_qoi(data, off)
        return w, h, rgba, kind
    if kind == "bz2":
        import bz2
        try:
            payload = bz2.decompress(bytes(data[off:]))
        except (OSError, EOFError) as exc:
            raise ImageError(f"BZ2 解压失败: {exc}")
        if payload[:4] == QOI_MAGIC:
            w, h, rgba = decode_qoi(payload)
            return w, h, rgba, kind
        if payload[:4] == PNG_SIG[:4]:
            w, h, rgba = decode_png(payload)
            return w, h, rgba, kind
        raise ImageError("BZ2 解压后不是 QOI 也不是 PNG")
    raise ImageError(f"认不出的纹理格式: {bytes(data[off:off + 4])!r}")


def crop(rgba, w, h, x, y, cw, ch):
    """从 RGBA 页里切一块出来。越界部分补透明。"""
    out = bytearray(cw * ch * 4)
    for row in range(ch):
        sy = y + row
        if sy < 0 or sy >= h:
            continue
        sx = max(0, x)
        ex = min(w, x + cw)
        if ex <= sx:
            continue
        seg = rgba[(sy * w + sx) * 4:(sy * w + ex) * 4]
        dx = (sx - x) * 4
        out[row * cw * 4 + dx:row * cw * 4 + dx + len(seg)] = seg
    return bytes(out)
