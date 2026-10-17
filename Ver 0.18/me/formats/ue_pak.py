# -*- coding: utf-8 -*-
"""Unreal Engine ``.pak`` 读取（自研，V0.17）。

为什么要自己写
--------------
``tools/repak/repak.exe`` 是第三方通道；自研通道不启动任何外部程序，
只用标准库读索引、按条目切数据、自己解 zlib / gzip 块。

格式（实测自 repak 的写出结果，逐字节核对见 ``selftest/test_ue_pak.py``）
-----------------------------------------------------------------------
文件尾是 ``FPakInfo``，从文件末尾往前找魔数 ``0x5A6F12E1``
（磁盘上是 ``E1 12 6F 5A``）::

    magic  u32      0x5A6F12E1
    version u32     0..11
    index_offset i64
    index_size   i64
    index_hash   u8[20]

之后还有若干版本相关字段（V8 起是压缩方法名表、V10 起是加密索引标志等），
本模块不解析它们 —— 只要 ``index_offset/index_size`` 能用，尾部剩下的
多少字节都无所谓。所以「找魔数 + 校验区间」就够了，不用管每个版本尾巴多长。

索引（版本 0–9，单层扁平索引）::

    mount  FString
    count  u32
    每个条目：
        name        FString
        offset      i64
        size        i64         存储长度（压缩后）
        uncompress  i64         解压后长度
        method      u32 或 u8   压缩方式（0=不压缩 1=zlib 2=gzip），版本 0–2 没有
        timestamp   i64         只有版本 1 有
        hash        u8[20]      SHA1
        blocks      u32 count + count×(start i64, end i64)   仅当 method != 0
        block_size  u32         （版本 >= 3）
        encrypted   u8          （版本 >= 3）

同一个版本号下 repak 会写出两种条目长度（V8A 把压缩方式写成 1 字节，
V8B 写成 4 字节），真实 UE 的版本号也不保证只有一种写法。所以这里不写死，
而是**试**几种字段宽度组合，要求恰好把整个索引读完 —— 读不完或读过头
都算不成立，换下一个组合。没有魔数、区间越界、组合都不成立时直接报错，
不猜。

版本 10/11（UE5）的索引换成了「主索引 + 路径哈希索引 + 目录索引 + 位压缩
条目」，本模块还没做 —— 遇到会抛 :class:`UnsupportedVersion`，
引擎据此退回第三方 repak，并在 notes 里说清楚，不假装支持。
"""

import gzip
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

MAGIC = 0x5A6F12E1
MAGIC_BYTES = b"\xE1\x12\x6F\x5A"

# 文件尾最多往回找这么多字节（最长的 FPakInfo 也就 300 字节上下）
FOOTER_WINDOW = 512

# 版本上限：10/11 是 UE5 的多层索引，尚未实现
MAX_SUPPORTED_VERSION = 9
LATEST_KNOWN_VERSION = 11

HASH_SIZE = 20

# 压缩方式编号 -> 名字。1/2 能自己解；3/4/5 分别是 Oodle/Zstd/LZ4，
# 标准库解不了（也是实测 repak 的编号顺序）。
COMPRESSION_NAMES = {
    0: "none",
    1: "zlib",
    2: "gzip",
    3: "oodle",
    4: "zstd",
    5: "lz4",
}
SELF_DECOMPRESSIBLE = {0, 1, 2}

# 单个条目最大解压体积（防御性上限，避免一个畸形条目把内存吃光）
MAX_ENTRY_BYTES = 512 * 1024 * 1024


class PakError(Exception):
    """不是合法 pak，或结构自相矛盾。"""


class UnsupportedVersion(PakError):
    """版本能认出来，但布局还没实现。"""


# ---------------------------------------------------------------- 基础读
class _Reader:
    """小端读取器；读不够就抛 _Mismatch，让外层去试别的布局。"""

    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes):
        self.buf = buf
        self.pos = 0

    def take(self, n: int) -> bytes:
        end = self.pos + n
        if n < 0 or end > len(self.buf):
            raise _Mismatch(f"需要 {n} 字节，只剩 {len(self.buf) - self.pos}")
        out = self.buf[self.pos:end]
        self.pos = end
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.take(8))[0]

    def fstring(self) -> str:
        """UE 的 FString：长度含结尾 NUL；负数表示 UTF-16LE。"""
        n = struct.unpack("<i", self.take(4))[0]
        if n == 0:
            return ""
        if n > 0:
            if n > 8192:
                raise _Mismatch(f"字符串长度异常: {n}")
            raw = self.take(n)
            return raw.rstrip(b"\x00").decode("utf-8", "replace")
        n = -n
        if n > 8192:
            raise _Mismatch(f"字符串长度异常: {n}")
        raw = self.take(n * 2)
        return raw.decode("utf-16-le", "replace").rstrip("\x00")

    @property
    def rest(self) -> int:
        return len(self.buf) - self.pos


class _Mismatch(Exception):
    """这个字段宽度组合不成立。"""


# ---------------------------------------------------------------- 数据模型
@dataclass
class PakEntry:
    name: str
    offset: int
    size: int              # 文件里的存储长度
    uncompressed: int      # 解压后长度
    method: int            # 0 none / 1 zlib / 2 gzip / 3..5 其它
    hash: bytes = b""
    blocks: Tuple[Tuple[int, int], ...] = ()
    block_size: int = 0
    encrypted: bool = False
    header: int = 0        # 数据前面那段 FPakEntry 头的长度（未压缩条目要用）

    @property
    def compression_name(self) -> str:
        return COMPRESSION_NAMES.get(self.method, f"#{self.method}")

    @property
    def stored(self) -> int:
        return self.size


@dataclass
class PakIndex:
    path: Path
    version: int
    mount: str
    entries: List[PakEntry] = field(default_factory=list)
    layout: str = ""           # 实际命中的字段宽度组合，排错时有用

    def names(self) -> List[str]:
        return [e.name for e in self.entries]

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.entries)


@dataclass
class ExtractReport:
    written: int = 0
    total: int = 0
    skipped_compression: int = 0
    skipped_encrypted: int = 0
    skipped_broken: int = 0
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- 文件尾
def find_footer(blob: bytes) -> Tuple[int, int, int, int]:
    """往前找 FPakInfo，返回 (魔数位置, 版本, 索引偏移, 索引长度)。

    按字节扫，不假设 4 字节对齐 —— 索引长度本身不是 4 的倍数时，
    魔数的位置相对文件尾也就不是 4 的倍数（实测 V0 的索引 142 字节）。
    """
    n = len(blob)
    if n < 44:
        raise PakError(f"文件太短（{n} 字节），不可能是 pak")
    lo = max(0, n - FOOTER_WINDOW - 44)
    for pos in range(lo, n - 43):
        if blob[pos:pos + 4] != MAGIC_BYTES:
            continue
        version, = struct.unpack_from("<I", blob, pos + 4)
        if version > LATEST_KNOWN_VERSION:
            continue
        off, size = struct.unpack_from("<qq", blob, pos + 8)
        if off <= 0 or size <= 0:
            continue
        if off + size > n:
            continue
        return pos, version, off, size
    raise PakError("文件尾没有 pak 魔数（不是 .pak，或文件被截断/加密）")


def check_version(version: int) -> None:
    if version > MAX_SUPPORTED_VERSION:
        raise UnsupportedVersion(
            f"pak 版本 {version}（UE5 的多层索引）", version)


# ---------------------------------------------------------------- 索引
def _parse_entries(r: _Reader, version: int, comp_size: int, ts_size: int,
                   count: int) -> List[PakEntry]:
    has_tail = version >= 3
    out: List[PakEntry] = []
    for _ in range(count):
        name = r.fstring()
        off = r.i64()
        size = r.i64()
        unc = r.i64()
        if comp_size == 4:
            method = r.u32()
        elif comp_size == 1:
            method = r.u8()
        else:
            method = 0
        if ts_size:
            r.take(ts_size)
        hash_ = r.take(HASH_SIZE)
        blocks: List[Tuple[int, int]] = []
        if method and not has_tail:
            # 压缩是版本 3 才引入的。走到这里说明这个字段宽度组合不成立
            # （多半是把别的字段当成了压缩方式），换下一个组合再试。
            raise _Mismatch(
                f"版本 {version} 不该有压缩方式，读到 {method}")
        if has_tail and method:
            nblocks = r.u32()
            if nblocks > 1_000_000:
                raise _Mismatch(f"压缩块数异常: {nblocks}")
            # 版本 5（RelativeChunkOffsets）起，压缩块的起止是**相对条目偏移**的；
            # 更老的版本是文件内绝对偏移。这里当场换算成绝对，后面提取就不用再管版本。
            for _b in range(nblocks):
                start = r.i64()
                end = r.i64()
                if version >= 5:
                    start += off
                    end += off
                blocks.append((start, end))
        block_size = 0
        encrypted = False
        if has_tail:
            block_size = r.u32()
            encrypted = bool(r.u8())
        # 每个文件的数据前面还有一段 FPakEntry 头（内容与索引里的条目一样，
        # 只是不带名字）。未压缩条目的 payload 在「偏移 + 这段头」之后；
        # 压缩条目用块表定位，不需要它。实测 V3 头 = 24+4+20+5 = 53 字节。
        header = 24 + comp_size + ts_size + HASH_SIZE
        if has_tail:
            header += 5
            if blocks:
                header += 4 + 16 * len(blocks)
        out.append(PakEntry(name=name, offset=off, size=size, uncompressed=unc,
                            method=method, hash=hash_, blocks=tuple(blocks),
                            block_size=block_size, encrypted=encrypted,
                            header=header))
    return out


def _sane(index: PakIndex, filesize: int, consumed: int, size: int) -> bool:
    if consumed != size:
        return False
    if len(index.entries) > 2_000_000:
        return False
    for e in index.entries:
        if e.offset < 0 or e.size < 0 or e.uncompressed < 0:
            return False
        if e.offset + e.size > filesize:
            return False
        if e.method and not e.blocks:
            return False
        for start, end in e.blocks:
            if not (0 <= start <= end <= filesize):
                return False
    return True


def parse_index(blob: bytes, path: Path, footer=None) -> PakIndex:
    """从整份文件里解析索引。"""
    pos, version, off, size = footer or find_footer(blob)
    check_version(version)
    buf = blob[off:off + size]

    attempts = []
    for comp_size in (4, 1, 0):
        for ts_size in (0, 8):
            attempts.append((comp_size, ts_size))

    errors = []
    for comp_size, ts_size in attempts:
        try:
            r = _Reader(buf)
            mount = r.fstring()
            count = r.u32()
            if count > 2_000_000:
                raise _Mismatch(f"条目数异常: {count}")
            entries = _parse_entries(r, version, comp_size, ts_size, count)
            idx = PakIndex(path=path, version=version, mount=mount,
                           entries=entries,
                           layout=f"comp={comp_size},ts={ts_size}")
            if _sane(idx, len(blob), r.pos, size):
                return idx
            errors.append(f"{idx.layout}: 读掉 {r.pos}/{size} 字节或数值越界")
        except _Mismatch as exc:
            errors.append(f"comp={comp_size},ts={ts_size}: {exc}")

    raise PakError(
        f"索引解析不出（版本 {version}，{size} 字节，{len(buf)} 可用）: "
        + "; ".join(errors[:4]))


def read_index(path) -> PakIndex:
    """读一个 .pak 的索引。"""
    path = Path(path)
    blob = path.read_bytes()
    return parse_index(blob, path)


# ---------------------------------------------------------------- 解压
def _decompress(method: int, raw: bytes) -> bytes:
    """按压缩方式解一块。

    先按索引里写的解；解不开再试另一种 —— 实测有 pak 把 gzip 写成 zlib
    （repak 的 V9+Gzip 就是这样：索引写 method=1，数据是 gzip 流）。
    两种都试不出来才报错，不猜第三种。
    """
    order = [method] + [m for m in (1, 2) if m != method]
    last = None
    for m in order:
        try:
            if m == 1:
                return zlib.decompress(raw)
            if m == 2:
                return gzip.decompress(raw)
        except (zlib.error, OSError) as exc:
            last = exc
            continue
    raise PakError(
        f"压缩块解不开（索引写的是 {COMPRESSION_NAMES.get(method, method)}）: {last}")


def entry_bytes(blob: bytes, entry: PakEntry) -> bytes:
    """取一个条目的原始数据（压缩的在这里解开）。"""
    if entry.method == 0:
        start = entry.offset + entry.header
        return blob[start:start + entry.size]
    if not entry.blocks:
        raise PakError(f"{entry.name}: 标记压缩但没有压缩块表")
    parts = []
    for start, end in entry.blocks:
        parts.append(_decompress(entry.method, blob[start:end]))
    data = b"".join(parts)
    if entry.uncompressed and len(data) != entry.uncompressed:
        raise PakError(
            f"{entry.name}: 解压得到 {len(data)} 字节，索引写的是 "
            f"{entry.uncompressed}")
    return data


# ---------------------------------------------------------------- 提取
def extract(path, writer, origin: str = "", progress: Optional[Callable] = None,
            rel_prefix: str = "") -> ExtractReport:
    """把一个 .pak 里的条目全部写进 writer。"""
    from me.collect import kinds

    path = Path(path)
    blob = path.read_bytes()
    idx = parse_index(blob, path)

    rep = ExtractReport(total=len(idx.entries))
    if not idx.entries:
        rep.warnings.append(f"{path.name}: 索引里 0 个条目")
        return rep

    prefix = rel_prefix or path.stem
    for i, entry in enumerate(idx.entries):
        if progress:
            progress(i + 1, rep.total, entry.name[-32:])
        rel = f"{prefix}/{entry.name}"

        if not writer.wants(kinds.classify(rel)):
            continue
        if entry.encrypted:
            rep.skipped_encrypted += 1
            continue
        if entry.method not in SELF_DECOMPRESSIBLE:
            rep.skipped_compression += 1
            if rep.skipped_compression <= 3:
                rep.warnings.append(
                    f"{path.name}: {entry.name} 用 {entry.compression_name} 压缩，"
                    "自研通道解不了")
            continue
        if entry.uncompressed > MAX_ENTRY_BYTES:
            rep.skipped_broken += 1
            rep.warnings.append(
                f"{path.name}: {entry.name} 解压后 {entry.uncompressed:,} 字节，"
                "超过自研通道上限，跳过")
            continue
        try:
            data = entry_bytes(blob, entry)
        except (PakError, zlib.error, OSError) as exc:
            rep.skipped_broken += 1
            if rep.skipped_broken <= 3:
                rep.warnings.append(f"{path.name}: {entry.name} 读不出: {exc}")
            continue
        if writer.write_auto(rel, data, origin=origin or str(path)):
            rep.written += 1

    return rep


def describe(index: PakIndex) -> str:
    """一行摘要，给 notes 用。"""
    return (f"{index.path.name}: pak v{index.version}, "
            f"{len(index.entries)} 个条目 / {index.total_bytes / 1024 / 1024:.1f} MB"
            f"，挂载点 {index.mount!r}")
