# -*- coding: utf-8 -*-
"""在文件里定位 CCN 数据流。

Clickteam 的成品有两种形态：

1. ``.dat`` / ``.ccn`` 文件（或直接裸数据）
   开头是 ``wwww`` 包装头（32 字节），紧跟一个「打包文件表」，
   表后面才是 CCN 数据流的 magic（PAMU / PAME / CRUF）。

2. 单文件 exe
   exe 尾部拼了一整块 ``wwww`` 结构，表里装的是运行时 dll 之类的打包文件，
   表后面接 CCN 数据流。

两种情况的统一规则::

    tag_offset = wwww_offset + u32(wwww+12) - 32
    并且 buf[tag_offset:tag_offset+4] 必须是 PAMU / PAME / CRUF

PAMU = Unicode 字符串（Fusion 2.5）
PAME = ASCII 字符串（MMF2）
CRUF = Fusion 3
"""

import struct
from dataclasses import dataclass, field
from typing import List, Optional

MAGIC_UNICODE = b"PAMU"
MAGIC_ANSI = b"PAME"
MAGIC_FUSION3 = b"CRUF"
MAGICS = (MAGIC_UNICODE, MAGIC_ANSI, MAGIC_FUSION3)

WWWW = b"wwww"
WWWW_HEADER_SIZE = 32

# PackFile 里表示「这块数据被 zlib 压过」的哨兵值
BINGO_COMPRESSED = -9608


class CtfError(Exception):
    """CTF 结构不符合预期。"""


@dataclass
class PackEntry:
    """打包文件表里的一项（一般是运行时 dll，不是游戏资源）。"""
    name: str
    size: int
    data_offset: int
    compressed: bool


@dataclass
class CcnLocation:
    """CCN 数据流的位置和基本信息。"""
    ccn_offset: int                 # 数据流起点（PAMU/PAME/CRUF 这 4 字节的位置）
    magic: bytes = MAGIC_UNICODE
    wwww_offset: Optional[int] = None
    runtime_version: int = 0
    runtime_subversion: int = 0
    product_version: int = 0
    product_build: int = 0
    chunk_start: int = 0
    pack_entries: List[PackEntry] = field(default_factory=list)

    @property
    def unicode(self) -> bool:
        return self.magic == MAGIC_UNICODE

    @property
    def is_fusion3(self) -> bool:
        return self.magic == MAGIC_FUSION3

    @property
    def label(self) -> str:
        return self.magic.decode("latin-1")

    def describe(self) -> str:
        kind = {MAGIC_UNICODE: "Fusion 2.5 (Unicode)",
                MAGIC_ANSI: "MMF2 (ANSI)",
                MAGIC_FUSION3: "Fusion 3"}.get(self.magic, "未知")
        return (f"{kind}  build {self.product_build}  "
                f"ccn@{self.ccn_offset}  打包项 {len(self.pack_entries)}")


# ------------------------------------------------------------------ 定位
def locate(buf, size: Optional[int] = None) -> Optional[CcnLocation]:
    """在缓冲区里找 CCN 数据流。找不到返回 None。

    buf 可以是 bytes 也可以是 mmap 对象。
    """
    if size is None:
        size = len(buf)

    # 情况 0：本身就是 CCN 流
    if bytes(buf[0:4]) in MAGICS:
        return _from_tag(buf, 0, None, size)

    # 情况 1/2：找 wwww 包装头
    pos = buf.find(WWWW)
    while pos >= 0:
        loc = _try_wwww(buf, pos, size)
        if loc is not None:
            return loc
        pos = buf.find(WWWW, pos + 1)
    return None


def _try_wwww(buf, pos: int, size: int) -> Optional[CcnLocation]:
    """校验一个 wwww 候选位置。"""
    if pos + WWWW_HEADER_SIZE > size:
        return None
    header_size, data_size = struct.unpack_from("<II", buf, pos + 8)
    if header_size != WWWW_HEADER_SIZE or data_size < WWWW_HEADER_SIZE:
        return None

    tag = pos + data_size - WWWW_HEADER_SIZE
    if tag < 0 or tag + 16 > size:
        return None
    if bytes(buf[tag:tag + 4]) not in MAGICS:
        return None
    return _from_tag(buf, tag, pos, size)


def _from_tag(buf, tag: int, wwww: Optional[int], size: int) -> CcnLocation:
    """从 magic 位置读出头部信息。"""
    loc = CcnLocation(ccn_offset=tag, magic=bytes(buf[tag:tag + 4]),
                      wwww_offset=wwww)
    loc.runtime_version, loc.runtime_subversion = struct.unpack_from(
        "<HH", buf, tag + 4)
    loc.product_version, loc.product_build = struct.unpack_from(
        "<II", buf, tag + 8)
    loc.chunk_start = tag + 16

    if wwww is not None:
        count = struct.unpack_from("<I", buf, wwww + 28)[0]
        loc.pack_entries = _read_pack_table(
            buf, wwww + WWWW_HEADER_SIZE, count, loc.unicode, size, tag)
    return loc


def _read_pack_table(buf, start: int, count: int, unicode: bool,
                     size: int, limit: int) -> List[PackEntry]:
    """解析打包文件表（只记录名字和偏移，不读数据）。"""
    entries: List[PackEntry] = []
    pos = start
    for _ in range(max(0, min(count, 4096))):
        if pos + 2 > limit:
            break
        name_len = struct.unpack_from("<H", buf, pos)[0]
        pos += 2
        char_w = 2 if unicode else 1
        name_bytes = name_len * char_w
        if name_len > 4096 or pos + name_bytes + 8 > limit:
            break
        enc = "utf-16-le" if unicode else "latin-1"
        name = bytes(buf[pos:pos + name_bytes]).decode(enc, "replace")
        pos += name_bytes
        _bingo, data_size = struct.unpack_from("<ii", buf, pos)
        pos += 8
        compressed = False
        if pos + 2 <= limit:
            compressed = struct.unpack_from("<h", buf, pos)[0] == BINGO_COMPRESSED
        entries.append(PackEntry(name=name, size=data_size,
                                 data_offset=pos, compressed=compressed))
        pos += max(0, data_size)
    return entries


# ------------------------------------------------------------------ 打开
def open_file(path):
    """打开文件并返回 (mmap, location)。用完要 close()。"""
    import mmap
    f = open(path, "rb")
    try:
        size = f.seek(0, 2)
        if size == 0:
            raise CtfError("文件为空")
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    except Exception:
        f.close()
        raise
    loc = locate(mm, size)
    if loc is None:
        mm.close()
        f.close()
        return None, None
    return _Mapped(mm, f), loc


class _Mapped:
    """把 mmap 和文件句柄绑在一起，方便统一关闭。"""

    def __init__(self, mm, fh):
        self.mm = mm
        self.fh = fh

    def close(self):
        try:
            self.mm.close()
        finally:
            self.fh.close()

    def __enter__(self):
        return self.mm

    def __exit__(self, *exc):
        self.close()
