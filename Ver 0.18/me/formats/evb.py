# -*- coding: utf-8 -*-
"""Enigma Virtual Box 容器读取（自研，V0.17）。

Enigma VB 把原始 exe + 一堆资源打包进一个 exe：PE 里多出两个节
``.enigma1``（虚拟文件系统 + 文件数据）和 ``.enigma2``（脱壳用的地址表）。
自研通道只读 ``.enigma1``，**不做 PE 还原** —— 还原原始 exe 是另一个问题，
对「把游戏资源拿出来」没有帮助。

``.enigma1`` 的布局（实测 cage-of-abyss 样本，见 selftest/test_evb.py）::

    [EVB_ENIGMA1_HEADER  指向自身各表的地址/长度]
    [包名等头部节点]
    [文件表：一条一条记录，紧挨着]
    [文件数据：按记录顺序紧密排列，第一条紧接文件表末尾]
    [之后到节末是空的]

记录（实测长度 = 名字字节数 + 70）::

    u32 序号     1,2,3… 递增（只用来核对顺序）
    u32 类型     2 = 文件；1/3 = 目录等其他节点
    u32 保留     0
    name         UTF-16LE，以 0x0000 结尾（长度 = (字符数+1)*2）
    u8  类型     2 = 文件
    u16 保留
    u64 原始长度
    f64 mtime/atime/ctime  三个时间戳（本样本三个相同）
    u64 属性     0x80 = FILE_ATTRIBUTE_NORMAL
    u8  7 个保留字节
    u64 存储长度  压缩时比原始长度小

字段顺序是从字节里量出来的：名字到名字的间距恒等于「名字字节数 + 70」，
四个条目的长度字段各自落在名字结束后第 5 字节，且数据起点恰好等于
最后一条记录的结束位置（0x325 = data.win 的 FORM 魔数所在处）。

压缩：存储长度 < 原始长度时按 LZMA 解（Enigma 用的是裸 LZMA 流）。
自研通道只做这一种；不认识的节点类型直接报错，让引擎退回第三方。

诚实边界：只在这个样本和它的版本上验证过。表结构对不上时抛
``EvbError``，由引擎退回 evbunpack —— 不猜、不硬解。
"""

import lzma
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# 虚拟文件系统的节名（Enigma 也允许改名，改了就认不出 —— 这时退回第三方）
SECTION = b".enigma1"
# 记录里名字之前的固定前缀长度（u32 序号 + u32 类型 + u32 保留）
PREAMBLE = 12
# 名字之后到记录结束的固定长度：type(1) + pad(2) + original(8) + 3×f64(24)
#                            + attrs(8) + 7 个保留 + stored(8)
TAIL = 58
# 名字之后的字段偏移（相对名字 NUL 之后）
OFF_TYPE = 0
OFF_ORIGINAL = 3
OFF_ATTRS = 35
OFF_STORED = 50

MAX_ENTRIES = 200_000


class EvbError(Exception):
    """不是 Enigma VB 容器，或表结构与已知版本不符。"""


@dataclass
class EvbEntry:
    name: str
    offset: int            # 在文件里的绝对偏移
    size: int              # 原始长度
    stored: int            # 存储长度（压缩时不同）
    is_file: bool
    attrs: int = 0
    seq: int = 0

    @property
    def compressed(self) -> bool:
        return self.is_file and self.stored != self.size


@dataclass
class EvbIndex:
    path: Path
    section_offset: int
    section_size: int
    data_start: int
    entries: List[EvbEntry] = field(default_factory=list)

    def files(self) -> List[EvbEntry]:
        return [e for e in self.entries if e.is_file]

    @property
    def total_bytes(self) -> int:
        return sum(e.stored for e in self.entries if e.is_file)


# ---------------------------------------------------------------- PE
def _sections(blob: bytes) -> List[Tuple[str, int, int, int, int]]:
    """返回 [(名字, 虚拟地址, 虚拟大小, 原始偏移, 原始大小)]。"""
    if len(blob) < 0x40 or blob[:2] != b"MZ":
        raise EvbError("不是 PE 文件（缺 MZ 头）")
    e_lfanew = struct.unpack_from("<I", blob, 0x3C)[0]
    if e_lfanew + 24 > len(blob) or blob[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        raise EvbError("不是 PE 文件（缺 PE 签名）")
    nt = e_lfanew + 4
    nsec, = struct.unpack_from("<H", blob, nt + 2)
    optsize, = struct.unpack_from("<H", blob, nt + 16)
    if nsec > 96:
        raise EvbError(f"节数量异常: {nsec}")
    sec = nt + 20 + optsize
    out = []
    for i in range(nsec):
        o = sec + i * 40
        if o + 40 > len(blob):
            raise EvbError("节表越界")
        name = blob[o:o + 8].rstrip(b"\x00").decode("latin-1")
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", blob, o + 8)
        out.append((name, vaddr, vsize, rawptr, rawsize))
    return out


def find_section(blob: bytes):
    """找 .enigma1，返回 (原始偏移, 原始大小)。"""
    for name, _vaddr, _vsize, rawptr, rawsize in _sections(blob):
        if name.lower() == SECTION.decode().lower():
            if rawptr + rawsize > len(blob):
                raise EvbError(".enigma1 声明的长度超出文件")
            return rawptr, rawsize
    raise EvbError("没有 .enigma1 节（不是 Enigma VB 壳，或节名被改过）")


# ---------------------------------------------------------------- 表
def _utf16_name_at(blob: bytes, pos: int, limit: int) -> Optional[Tuple[str, int]]:
    """从 pos 起读一个 UTF-16LE 名字，返回 (名字, 结束位置=含 NUL)。"""
    out = []
    p = pos
    while p + 1 < limit:
        ch = struct.unpack_from("<H", blob, p)[0]
        if ch == 0:
            if len(out) >= 3:
                return "".join(out), p + 2
            return None
        if ch < 0x20 and ch not in (0x09,):
            return None
        out.append(chr(ch))
        p += 2
        if len(out) > 512:
            return None
    return None


@dataclass
class _Record:
    name: str
    after_name: int
    ftype: int
    original: int
    attrs: int
    stored: int

    @property
    def name_bytes(self) -> int:
        return (len(self.name) + 1) * 2

    def end(self) -> int:
        """这条记录结束的位置（名字之后还有 58 字节固定字段）。"""
        return self.after_name + TAIL


def _record_at(blob: bytes, pos: int, sec_end: int) -> Optional[_Record]:
    """把 pos 当作名字起点试解一条记录；不像就返回 None。"""
    got = _utf16_name_at(blob, pos, sec_end)
    if got is None:
        return None
    name, after = got
    if after + TAIL > sec_end:
        return None
    ftype = blob[after + OFF_TYPE]
    if ftype not in (1, 2, 3):
        return None
    original, = struct.unpack_from("<Q", blob, after + OFF_ORIGINAL)
    attrs, = struct.unpack_from("<I", blob, after + OFF_ATTRS)
    stored, = struct.unpack_from("<Q", blob, after + OFF_STORED)
    if stored > (1 << 40) or original > (1 << 40):
        return None
    if ftype == 2 and (original <= 0 or stored <= 0):
        return None
    # 属性位只见过 0x80（普通文件）和 0（目录/特殊）
    if attrs & ~0xFFFF:
        return None
    return _Record(name=name, after_name=after, ftype=ftype,
                   original=original, attrs=attrs, stored=stored)


def _seq_near(blob: bytes, name_pos: int) -> Optional[int]:
    """记录序号。实测第一条（data.win）比建模位置偏 1 字节，所以试三个位置。"""
    for delta in (PREAMBLE, PREAMBLE + 1, PREAMBLE - 1):
        p = name_pos - delta
        if p < 0:
            continue
        seq, = struct.unpack_from("<I", blob, p)
        if 1 <= seq <= MAX_ENTRIES:
            return seq
    return None


def _walk(blob: bytes, sec_start: int, sec_end: int):
    """找出整张文件表，返回 (第一条记录的名字位置, 条目, 数据起点)。

    做法：先在节的前 64KB 里找一个“像第一条记录”的名字位置，然后按
    「下一条名字 = 本条名字 + 名字字节数 + 70」一路走；走不动了就是数据起点。
    序号必须逐条 +1，长度字段必须自洽 —— 靠这两条把数据区误认成记录的概率
    压到可以忽略（实测样本里数据区第一字节是 FORM 魔数，会立刻被类型/序号挡掉）。
    """
    limit = min(sec_end, sec_start + 0x10000)
    best = None
    for cand in range(sec_start, limit - PREAMBLE):
        rec = _record_at(blob, cand, sec_end)
        if rec is None:
            continue
        seq = _seq_near(blob, cand)
        if seq not in (1, 2):
            continue
        entries: List[EvbEntry] = []
        p = cand                       # p 是「名字」的位置，不是记录起点
        expect = seq
        while True:
            cur = _record_at(blob, p, sec_end)
            if cur is None:
                break
            if entries:
                nxt = _seq_near(blob, p)
                if nxt is not None and nxt != expect:
                    break
            entries.append(EvbEntry(name=cur.name, offset=0, size=cur.original,
                                    stored=cur.stored, is_file=cur.ftype == 2,
                                    attrs=cur.attrs, seq=expect))
            # 下一条记录的名字 = 本条名字 + 名字字节数 + 58（名字之后的固定字段）
            #                    + 12（下一条的 序号/类型/保留 三个 u32）
            p = p + cur.name_bytes + PREAMBLE + TAIL
            expect += 1
        if len(entries) < 1:
            continue
        # p 已经走到「下一条记录的名字位置」，而它前面 12 字节是序号/类型/保留。
        # 数据从最后一条记录的**结束**处开始，所以退掉这 12 字节。
        data_start = p - PREAMBLE
        total = sum(e.stored for e in entries if e.is_file)
        if data_start + total > sec_end:
            continue
        # 优先选条目最多的候选（header 节点也可能长得像记录）
        if best is None or len(entries) > len(best[1]):
            best = (cand, entries, data_start)
    if best is None:
        raise EvbError("找不到文件表（结构与本模块已知的版本不符）")
    return best


def read_index(path) -> EvbIndex:
    """读一个 Enigma VB exe 的虚拟文件系统索引。"""
    path = Path(path)
    blob = _read(path)
    sec_start, sec_size = find_section(blob)
    sec_end = sec_start + sec_size
    _first, entries, data_start = _walk(blob, sec_start, sec_end)
    cursor = data_start
    for e in entries:
        if e.is_file:
            e.offset = cursor
            cursor += e.stored
    return EvbIndex(path=path, section_offset=sec_start, section_size=sec_size,
                    data_start=data_start, entries=entries)


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise EvbError(f"读取失败: {exc}") from exc


# ---------------------------------------------------------------- 解压
def entry_bytes(blob: bytes, entry: EvbEntry) -> bytes:
    """取一条内容（LZMA 的在这里解开）。"""
    raw = blob[entry.offset:entry.offset + entry.stored]
    if not entry.compressed:
        return raw
    # Enigma 用裸 LZMA：先按 ALONE（带 13 字节头）试，再按裸流试
    for fmt in (lzma.FORMAT_ALONE, lzma.FORMAT_RAW):
        try:
            if fmt == lzma.FORMAT_RAW:
                dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW,
                                            filters=[{"id": lzma.FILTER_LZMA1}])
                out = dec.decompress(raw)
            else:
                out = lzma.decompress(raw, format=fmt)
        except (lzma.LZMAError, EOFError, ValueError):
            continue
        if len(out) == entry.size:
            return out
    raise EvbError(f"{entry.name}: LZMA 解不开（存储 {entry.stored} / 原始 {entry.size}）")


# ---------------------------------------------------------------- 提取
@dataclass
class ExtractReport:
    written: int = 0
    total: int = 0
    bytes: int = 0
    warnings: List[str] = field(default_factory=list)


def extract_to(path, dest, progress: Optional[Callable] = None) -> ExtractReport:
    """把虚拟文件系统铺到 dest 目录（不碰 writer，和 evbunpack 的行为一致）。"""
    path = Path(path)
    dest = Path(dest)
    blob = _read(path)
    idx = read_index(path)
    rep = ExtractReport(total=len([e for e in idx.entries if e.is_file]))
    dest.mkdir(parents=True, exist_ok=True)
    for i, e in enumerate(idx.entries):
        if progress:
            progress(i + 1, len(idx.entries), e.name[-32:])
        target = dest / e.name.replace("\\", "/")
        # 防目录穿越：只允许落在 dest 里面
        try:
            target.resolve().relative_to(dest.resolve())
        except ValueError:
            rep.warnings.append(f"跳过越界路径: {e.name}")
            continue
        if not e.is_file:
            target.mkdir(parents=True, exist_ok=True)
            continue
        try:
            data = entry_bytes(blob, e)
        except EvbError as exc:
            rep.warnings.append(str(exc))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        rep.written += 1
        rep.bytes += len(data)
    return rep


def describe(index: EvbIndex) -> str:
    files = index.files()
    return (f"{index.path.name}: Enigma VFS {len(files)} 个文件 / "
            f"{index.total_bytes / 1024 / 1024:.1f} MB，"
            f"数据起点 {index.data_start:#x}")
