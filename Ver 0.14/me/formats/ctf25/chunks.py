# -*- coding: utf-8 -*-
"""CCN chunk 流遍历。

每个 chunk 的头是 8 字节::

    i16 id
    i16 flag      0=未压缩 1=zlib压缩 2=加密 3=压缩且加密
    i32 size      紧跟其后的数据长度

flag == 1 时，那 size 字节的布局是::

    u32 解压后大小
    u32 压缩后大小       ( = size - 8 )
    zlib 流

实测验证：chunk 0x2223 的 size=67 = 8 + 59，解压出 112 字节，与头里声明一致。
"""

import struct
import zlib
from dataclasses import dataclass
from typing import List, Optional

from me.formats.ctf25 import ids

FLAG_RAW = 0
FLAG_ZLIB = 1
FLAG_ENCRYPTED = 2
FLAG_ZLIB_ENCRYPTED = 3

# 单个 chunk 的合理上限，防止遇到坏数据时申请几个 GB
MAX_CHUNK_SIZE = 512 * 1024 * 1024


class ChunkError(Exception):
    pass


@dataclass
class Chunk:
    """一个 chunk 的头部信息（不含数据本体）。"""
    id: int
    flag: int
    size: int
    data_offset: int          # size 字节数据的起点
    decl_size: int = 0        # flag==1 时的解压后大小
    comp_size: int = 0        # flag==1 时的压缩后大小

    @property
    def name(self) -> str:
        return ids.name_of(self.id)

    def __repr__(self) -> str:
        return (f"<Chunk 0x{self.id & 0xFFFF:04X} {self.name} "
                f"flag={self.flag} size={self.size}>")


def scan(buf, start: int, end: Optional[int] = None,
         limit: int = 200000) -> List[Chunk]:
    """扫出所有 chunk 头。

    只读头，不读数据，所以很快。
    """
    if end is None:
        end = len(buf)
    chunks: List[Chunk] = []
    pos = start
    guard = 0

    while pos + 8 <= end:
        guard += 1
        if guard > limit:
            break
        chunk_id, flag, size = struct.unpack_from("<hhI", buf, pos)
        if size < 0 or size > MAX_CHUNK_SIZE:
            break
        body = pos + 8
        if body + size > end:
            # 数据超出范围，说明后面不是合法 chunk 了
            break

        ch = Chunk(id=chunk_id, flag=flag, size=size, data_offset=body)
        if flag == FLAG_ZLIB and size >= 8:
            ch.decl_size, ch.comp_size = struct.unpack_from("<II", buf, body)
        chunks.append(ch)

        if chunk_id == ids.CHUNK_LAST:
            break
        pos = body + size

    return chunks


def load(buf, chunk: Chunk) -> bytes:
    """取出一个 chunk 解压后的数据。"""
    body = bytes(buf[chunk.data_offset:chunk.data_offset + chunk.size])

    if chunk.flag == FLAG_RAW:
        return body

    if chunk.flag == FLAG_ZLIB:
        if len(body) < 8:
            raise ChunkError(f"chunk {chunk.name} 数据过短")
        payload = body[8:8 + chunk.comp_size]
        try:
            return zlib.decompress(payload)
        except zlib.error as exc:
            raise ChunkError(f"chunk {chunk.name} zlib 解压失败: {exc}") from exc

    if chunk.flag in (FLAG_ENCRYPTED, FLAG_ZLIB_ENCRYPTED):
        raise ChunkError(
            f"chunk {chunk.name} 已加密（flag={chunk.flag}），暂不支持")

    raise ChunkError(f"未知 chunk flag: {chunk.flag}")


def find(chunks, chunk_id: int) -> Optional[Chunk]:
    """按编号找第一个 chunk。"""
    for ch in chunks:
        if ch.id == chunk_id:
            return ch
    return None


def find_all(chunks, chunk_id: int) -> List[Chunk]:
    return [ch for ch in chunks if ch.id == chunk_id]


def summary(chunks) -> str:
    """给日志用的概览。"""
    from collections import Counter
    c = Counter(ch.name for ch in chunks)
    return ", ".join(f"{n}×{k}" for n, k in c.most_common(12))
