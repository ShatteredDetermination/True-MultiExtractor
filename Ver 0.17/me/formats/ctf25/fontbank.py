# -*- coding: utf-8 -*-
"""CTF 字体解析。

有两个不同的东西，别搞混：

* chunk ``0x6667``（Fonts）—— 只有字体**元数据**（LogFont：字体名、字号、粗体等），
  没有字体文件本身。用来出清单。

* chunk ``0x2259``（Fusion 2.5+ 的 Fonts）—— 真正内嵌的 **TTF 字体文件**，
  一条接一条，每条是 ``[u32 解压后大小][u32 压缩后大小][zlib 数据]``。
  这才是我们要导出的东西。
"""

import struct
import zlib
from dataclasses import dataclass
from typing import Iterator, List

from me.formats.ctf25.binary import Reader

MAX_FONT = 128 * 1024 * 1024


class FontBankError(Exception):
    pass


@dataclass
class FontMeta:
    """字体元数据（来自 0x6667）。"""
    handle: int = 0
    checksum: int = 0
    references: int = 0
    height: int = 0
    width: int = 0
    weight: int = 0
    italic: int = 0
    underline: int = 0
    strikeout: int = 0
    charset: int = 0
    face_name: str = ""

    def __repr__(self) -> str:
        return f"<Font {self.face_name!r} {self.height}px>"


def parse_font_meta(data: bytes, compressed: bool = True,
                    unicode: bool = True) -> List[FontMeta]:
    """解析 0x6667 字体元数据 chunk。"""
    r = Reader(data)
    if r.remaining() < 4:
        return []
    count = r.i32()
    if count < 0 or count > 100000:
        return []

    out: List[FontMeta] = []
    for _ in range(count):
        if r.remaining() < 4:
            break
        try:
            item = _read_font_item(r, compressed, unicode)
        except (struct.error, IndexError, zlib.error):
            break
        if item is not None:
            out.append(item)
    return out


def _read_font_item(r: Reader, compressed: bool, unicode: bool):
    handle = r.u32()
    if compressed:
        if r.remaining() < 8:
            return None
        _decl = r.i32()
        comp = r.i32()
        if comp < 0 or comp > MAX_FONT or comp > r.remaining():
            return None
        payload = r.read(comp)
        try:
            inner = zlib.decompress(payload)
        except zlib.error:
            return None
    else:
        inner = r.read(r.remaining())

    ir = Reader(inner)
    m = FontMeta(handle=handle)
    m.checksum = ir.i32()
    m.references = ir.i32()
    ir.i32()                        # 长度字段，用不上
    m.height = ir.i32()
    m.width = ir.i32()
    ir.i32()                        # escapement
    ir.i32()                        # orientation
    m.weight = ir.i32()
    m.italic = ir.u8()
    m.underline = ir.u8()
    m.strikeout = ir.u8()
    m.charset = ir.u8()
    ir.skip(4)                      # outPrecision/clipPrecision/quality/pitch
    m.face_name = (ir.yuniversal(32, unicode) if ir.remaining() >= 32
                   else "").replace("\x00", "").strip()
    return m


def parse_truetype(data: bytes) -> Iterator[bytes]:
    """解析 0x2259 里内嵌的 TTF 字体文件，逐个 yield 文件内容。"""
    r = Reader(data)
    while r.remaining() >= 8:
        _decl = r.i32()
        comp = r.i32()
        if comp <= 0 or comp > MAX_FONT or comp > r.remaining():
            break
        payload = r.read(comp)
        try:
            font = zlib.decompress(payload)
        except zlib.error:
            break
        if font:
            yield font


def font_ext(data: bytes) -> str:
    """按文件头判断字体格式。"""
    if len(data) < 4:
        return ".bin"
    head = data[:4]
    if head == b"OTTO":
        return ".otf"
    if head == b"ttcf":
        return ".ttc"
    if head == b"wOFF":
        return ".woff"
    if head == b"wOF2":
        return ".woff2"
    if head == b"\x00\x01\x00\x00" or head == b"true" or head == b"ttcf":
        return ".ttf"
    return ".ttf"
