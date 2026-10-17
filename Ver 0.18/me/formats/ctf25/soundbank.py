# -*- coding: utf-8 -*-
"""CTF 音频条目解析。

一条音频记录（Fusion 2.5）::

    u32 handle          (读出来减 1)
    i32 checksum
    u32 references
    i32 解压后大小
    u8  flags
    3B  保留
    i32 保留
    i32 名字长度（字符数）
    --- 若整条被压缩且 flags != 33 ---
    i32 压缩后大小
    zlib 数据
    --- 否则 ---
    直接跟「解压后大小」字节
    --- 之后 ---
    UTF-16 名字（名字长度 个字符）
    剩余字节 = 真正的音频文件内容（OGG / WAV / MP3）

flags == 33 比较特殊：数据里没有名字，整块就是音频。
"""

import struct
import zlib
from dataclasses import dataclass
from typing import Iterator, Optional

from me.formats.ctf25 import sniff_audio
from me.formats.ctf25.binary import Reader

MAX_ENTRY = 256 * 1024 * 1024
BINGO_NO_NAME = 33


class SoundBankError(Exception):
    pass


@dataclass
class SoundEntry:
    """一段音频。"""
    handle: int = 0
    checksum: int = 0
    references: int = 0
    flags: int = 0
    name: str = ""
    data: bytes = b""
    decompressed_size: int = 0

    @property
    def ext(self) -> str:
        return sniff_audio.guess_ext(self.data)

    def safe_name(self) -> str:
        base = self.name.strip() or f"sound_{self.handle}"
        for ch in '<>:"/\\|?*':
            base = base.replace(ch, "_")
        return base

    def __repr__(self) -> str:
        return f"<Sound #{self.handle} {self.name!r} {len(self.data)}B>"


def parse_sounds(data: bytes, compressed: bool = True) -> Iterator[SoundEntry]:
    """遍历 Sounds / Music chunk。"""
    r = Reader(data)
    if r.remaining() < 4:
        return
    count = r.i32()
    if count < 0 or count > 2_000_000:
        raise SoundBankError(f"音频数量不合理: {count}")

    for i in range(count):
        if r.remaining() < 8:
            break
        before = r.tell()
        try:
            entry = _read_sound(r, compressed)
        except (struct.error, IndexError, zlib.error) as exc:
            raise SoundBankError(
                f"第 {i} 段音频解析失败（偏移 {before}）: {exc}") from exc
        if entry is None:
            break
        yield entry
        if r.tell() <= before:
            raise SoundBankError("音频解析未前进")


def _read_sound(r: Reader, compressed: bool) -> Optional[SoundEntry]:
    e = SoundEntry()
    e.handle = (r.u32() - 1) & 0xFFFFFFFF
    e.checksum = r.i32()
    e.references = r.u32()
    e.decompressed_size = r.i32()
    e.flags = r.u8()
    r.skip(3)
    r.i32()                          # 保留
    name_len = r.i32()

    if e.decompressed_size < 0 or e.decompressed_size > MAX_ENTRY:
        raise SoundBankError(f"音频长度异常: {e.decompressed_size}")
    if name_len < 0 or name_len > 4096:
        raise SoundBankError(f"音频名字长度异常: {name_len}")

    if compressed and e.flags != BINGO_NO_NAME:
        size = r.i32()
        if size < 0 or size > MAX_ENTRY:
            raise SoundBankError(f"音频压缩块长度异常: {size}")
        payload = r.read(size)
        try:
            sound_data = zlib.decompress(payload)
        except zlib.error:
            # 有些文件标记了压缩其实没压，退回按原样处理
            sound_data = payload
    else:
        sound_data = r.read(e.decompressed_size)

    sr = Reader(sound_data)
    if e.flags == BINGO_NO_NAME:
        sr.seek(0)
    else:
        # 声明长度里常含 \0 填充，去掉
        e.name = sr.wide(name_len).replace("\x00", "").strip()
    e.data = sr.read(sr.remaining())
    return e
