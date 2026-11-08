# -*- coding: utf-8 -*-
"""CTF 图像条目解析。

两种常见布局：

**Fusion 2.5+（TwoFivePlusImage）** —— 头部 40 字节，图像数据用 LZ4 block 压缩::

    i32 handle        (读出来要减 1)
    i32 checksum
    i32 references
    i32 (保留)
    i32 dataSize      = 4 + LZ4 数据长度
    i16 width
    i16 height
    u8  graphicMode
    u8  flags
    i16 (保留)
    i16 hotspotX / hotspotY
    i16 actionX  / actionY
    4B  transparent (r,g,b,a)
    i32 解压后大小
    LZ4 数据 (dataSize - 4 字节)

**MMF2 / 更老（NormalImage）** —— 整条记录先 zlib 压缩，内部再按 flags 决定图像是否压缩。

实测校验：Last Breath 第一条图 width=32 height=32，解压后大小字段 = 4096 = 32×32×4。
"""

import struct
import zlib
from dataclasses import dataclass
from typing import Iterator, Optional

from me.formats.ctf25 import lz4
from me.formats.ctf25.binary import Reader

# flags 位序（对应 CTFAK 的 BitDict，低位在前）
FLAG_BITS = ("RLE", "RLEW", "RLET", "LZX", "Alpha", "ACE", "Mac", "RGBA")

# 一条记录的合理上限
MAX_ENTRY = 256 * 1024 * 1024


class ImageBankError(Exception):
    pass


def flag_set(flags: int, index: int) -> bool:
    return bool(flags & (1 << index))


@dataclass
class ImageEntry:
    """一张图。"""
    handle: int = 0
    checksum: int = 0
    references: int = 0
    width: int = 0
    height: int = 0
    graphic_mode: int = 0
    flags: int = 0
    hotspot_x: int = 0
    hotspot_y: int = 0
    action_x: int = 0
    action_y: int = 0
    transparent: tuple = (0, 0, 0, 0)
    pixels: bytes = b""            # 解压后的原始像素数据

    @property
    def alpha_flag(self) -> bool:
        return flag_set(self.flags, 4)

    @property
    def rgba_flag(self) -> bool:
        return flag_set(self.flags, 7)

    @property
    def lzx_flag(self) -> bool:
        return flag_set(self.flags, 3)

    def __repr__(self) -> str:
        return (f"<Image #{self.handle} {self.width}x{self.height} "
                f"mode={self.graphic_mode} flags=0x{self.flags:02X}>")


def parse_images(data: bytes, twofiveplus: bool = True,
                 build: int = 0) -> Iterator[ImageEntry]:
    """遍历 Images chunk 里的所有图像。"""
    r = Reader(data)
    if r.remaining() < 4:
        return
    count = r.i32()
    # count 明显不合理时按 0 处理，避免死循环
    if count < 0 or count > 5_000_000:
        raise ImageBankError(f"图像数量不合理: {count}")

    for i in range(count):
        if r.remaining() < 8:
            break
        before = r.tell()
        try:
            entry = (_read_25plus(r, build) if twofiveplus
                     else _read_normal(r, build))
        except (struct.error, IndexError, lz4.Lz4Error, zlib.error) as exc:
            # 后面数据坏了就停在已解出的部分，不要把整个提取搞崩
            raise ImageBankError(
                f"第 {i} 张图解析失败（偏移 {before}）: {exc}") from exc
        if entry is None:
            break
        yield entry
        if r.tell() <= before:
            raise ImageBankError("图像解析未前进")


def _read_25plus(r: Reader, build: int) -> Optional[ImageEntry]:
    """Fusion 2.5+ 的一条图像记录。"""
    e = ImageEntry()
    e.handle = r.i32() - 1
    e.checksum = r.i32()
    e.references = r.i32()
    r.i32()                       # 保留
    data_size = r.i32()
    if data_size < 4 or data_size > MAX_ENTRY:
        raise ImageBankError(f"图像数据长度异常: {data_size}")

    e.width = r.i16()
    e.height = r.i16()
    e.graphic_mode = r.u8()
    e.flags = r.u8()
    r.i16()                       # 保留
    e.hotspot_x = r.i16()
    e.hotspot_y = r.i16()
    e.action_x = r.i16()
    e.action_y = r.i16()
    e.transparent = tuple(r.read(4))

    decl = r.i32()
    raw = r.read(data_size - 4)

    if decl > 0:
        if decl > MAX_ENTRY:
            raise ImageBankError(f"解压后大小异常: {decl}")
        e.pixels = lz4.decompress(raw, decl)
    else:
        e.pixels = raw

    if e.width <= 0 or e.height <= 0:
        raise ImageBankError(f"图像尺寸异常: {e.width}x{e.height}")
    return e


def _read_normal(r: Reader, build: int) -> Optional[ImageEntry]:
    """MMF2 / 1.5 的一条图像记录（整条先 zlib 压）。"""
    e = ImageEntry()
    handle = r.i32()
    e.handle = handle - 1 if build >= 284 else handle

    decl_size = r.i32()
    comp_size = r.i32()
    if comp_size < 0 or comp_size > MAX_ENTRY:
        raise ImageBankError(f"压缩块长度异常: {comp_size}")
    raw = r.read(comp_size)
    try:
        inner = zlib.decompress(raw)
    except zlib.error as exc:
        raise ImageBankError(f"zlib 解压失败: {exc}") from exc

    ir = Reader(inner)
    e.checksum = ir.i32()
    e.references = ir.i32()
    data_size = ir.i32()
    e.width = ir.i16()
    e.height = ir.i16()
    e.graphic_mode = ir.u8()
    e.flags = ir.u8()
    ir.i16()
    e.hotspot_x = ir.i16()
    e.hotspot_y = ir.i16()
    e.action_x = ir.i16()
    e.action_y = ir.i16()
    e.transparent = tuple(ir.read(4))

    if e.lzx_flag:
        ir.i32()                      # 解压后大小
        e.pixels = zlib.decompress(ir.read(ir.remaining()))
    else:
        e.pixels = ir.read(data_size)

    if e.width <= 0 or e.height <= 0:
        raise ImageBankError(f"图像尺寸异常: {e.width}x{e.height}")
    return e
