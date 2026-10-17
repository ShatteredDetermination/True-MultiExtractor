# -*- coding: utf-8 -*-
"""Godot .pck 归档解析（纯 Python）。

布局（全部小端）::

    0    u32 magic 'GDPC'(0x43504447) | u32 format_version | u32 ver_major/minor/patch
    20   u32 flags（PACK_DIR_ENCRYPTED 等）
    版本区： v1 没有 file_base；v2 起是 u64 file_base（数据区在文件里的位置），
             之后一段预留区（真 Godot 64 字节，简化打包器 0 / 16 字节）
    目录区： u32 file_count，随后每条记录 ——
             u32 path_len（UTF-8 路径补齐到 4 字节，真 Godot 把补齐算进 path_len）
             u64 offset, u64 size, u8 md5[16]（v3/v4 起 sha256[32]），u32 flags（v2 起）
    数据区： 文件数据

现实里这个格式有三处不统一，本模块一处都不假设，全都「试出来」（判断依据写在
对应函数的 docstring 里）：目录起点在哪（真 Godot 在 84/96，简化写法在 24/48）、
offset 相对谁（Godot 4.x 相对 file_base，3.x 是绝对偏移）、数据区在目录前还是后。
第三方那种「相对数据区起点」的偏移变种不做猜测：宁可少写几个文件，也不写出错位
的数据；真读不通就抛 PckError，把原因原样摊给用户看。

证据等级（详见 FORMATS.md 第 5 节）：头部偏移 84/96、file_base 语义、path_len 补齐
规则来自 Godot 源码，属于【公开】；布局判定逻辑用合成样本往返验证，属于【自洽】。
本机没有真实 .pck 样本，所以没有【实测】；v3/v4 按 sha256(32) 猜，属于尽力而为。
"""

import os
import struct
from collections import namedtuple
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from me.collect import kinds
from me.collect.writer import sanitize_rel

MAGIC = 0x43504447
PACK_DIR_ENCRYPTED = 1 << 0        # 头部 flags
FILE_ENCRYPTED = 1 << 0            # 记录 flags
FILE_REMOVAL = 1 << 1

# 目录记录的哈希长度：v1/v2 是 md5，v3/v4 起换成 sha256
HASH_LEN = {1: 16, 2: 16, 3: 32, 4: 32}
# 每个版本「目录起点」的优先候选：真 Godot 的写法在前，简化写法在后
DIR_CANDIDATES = {1: (84, 24), 2: (96, 48), 3: (96, 48), 4: (96, 48)}
DIR_SCAN = tuple(range(24, 256, 4))    # 上面都不自洽时，头部按 4 字节步长再扫一遍

HEADER_MIN = 24            # 固定头部长度，数据不可能落在这里面
MAX_ENTRIES = 500_000      # 条数上限，防止把垃圾字节当地址用
MAX_PATH = 4096
MIN_RECORD = 40            # 一条记录的最小字节数，用于粗筛 file_count
MAX_FIELD = 1 << 44        # offset/size 的合理上限，挡住明显的垃圾值
TAIL_SLACK = 4096          # 数据区离它该到的地方超过这么多就算「对不上」
CHUNK = 1 << 20            # 大文件分块搬运的块大小
SMALL = 1 << 20            # 小于这个尺寸的文件直接整块读进内存

_Eval = namedtuple("_Eval", "base kind bad disjoint tail_ok")


class PckError(Exception):
    """PCK 解析失败：魔数不对、版本不支持、目录被加密、结构完全读不通等。"""


@dataclass
class Entry:
    """目录里的一条记录。"""
    path: str
    offset: int
    size: int
    flags: int = 0


@dataclass
class Index:
    """一份 PCK 的目录（不含文件数据）。"""
    version: int
    filesize: int
    flags: int
    file_base: int          # 头部记录的 file_base（v1 没有这个字段，恒为 0）
    dir_pos: int            # 目录起点（file_count 所在字节）
    hash_len: int
    base: int               # 实际采用的 offset 基准
    base_kind: str          # 基准的人话说明，写进报告给用户看
    base_confident: bool    # 基准是否完全自洽（False 说明数据可能错位）
    entries: List[Entry] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.entries)


@dataclass
class Report:
    """一次解包的结果。"""
    total: int = 0
    written: int = 0
    skipped_encrypted: int = 0
    skipped_removed: int = 0
    skipped_broken: int = 0
    skipped_filtered: int = 0
    escaped: List[str] = field(default_factory=list)   # 试图跳出输出目录的路径
    warnings: List[str] = field(default_factory=list)
    index: Optional[Index] = None


# ---------------------------------------------------------------- 读目录
def read_index(path) -> Index:
    """读目录结构（不碰文件数据）。"""
    path = Path(path)
    try:
        filesize = path.stat().st_size
    except OSError as exc:
        raise PckError(f"读不到 {path}: {exc}") from exc
    if filesize < 32:
        raise PckError(f"{path.name} 只有 {filesize} 字节，不可能是 PCK")

    with open(path, "rb") as f:
        head = f.read(24)
        if len(head) < 24:
            raise PckError(f"{path.name} 头部被截断")
        magic, version, _maj, _min, _pat, flags = struct.unpack("<IIIIII", head)
        if magic != MAGIC:
            raise PckError(f"{path.name} 不是 Godot PCK：魔数应为 GDPC(0x{MAGIC:08X})，"
                           f"实际是 0x{magic:08X}")
        if not 1 <= version <= 4:
            raise PckError(f"不支持的 PCK 版本 {version}（支持 1/2，3/4 只能尽力而为）")
        if flags & PACK_DIR_ENCRYPTED:
            raise PckError(f"{path.name} 的目录被加密（flags=0x{flags:X}）：文件名和"
                           "偏移都在加密块里，需要打包时用的那 32 字节密钥。")

        # v2 起头部才有 file_base，它在头部的位置固定（紧跟 flags）
        file_base = 0
        if version >= 2:
            f.seek(24)
            raw = f.read(8)
            if len(raw) == 8:
                file_base = struct.unpack("<Q", raw)[0]

        pos, entries, hash_len, ev, ok = _locate_dir(f, version, filesize, file_base)
        kind = ev.kind if ok else ev.kind + "（结构不完全自洽，数据可能错位）"
        return Index(version=version, filesize=filesize, flags=flags,
                     file_base=file_base, dir_pos=pos, hash_len=hash_len,
                     base=ev.base, base_kind=kind, base_confident=ok, entries=entries)


def _locate_dir(f, version, filesize, file_base):
    """找出目录起点，顺带把 offset 基准一起定下来。

    候选起点按「真 Godot 写法 -> 简化写法 -> 头部扫一遍」的顺序试，每个候选都用
    _eval_base 对着文件边界验一遍，取第一个完全自洽的；都不自洽就取最接近的，
    让上层把风险写进报告。
    """
    cands = [(p, HASH_LEN[version]) for p in DIR_CANDIDATES.get(version, (96, 48))]
    for pos in DIR_SCAN:
        for hlen in (HASH_LEN[version], 16, 32):
            cands.append((pos, hlen))

    best = None
    empty = None
    seen = set()
    for pos, hlen in cands:
        if (pos, hlen) in seen or pos + 4 > filesize:
            continue
        seen.add((pos, hlen))
        try:
            entries, dir_end = _read_dir(f, pos, version, hlen, filesize)
        except (PckError, struct.error):
            continue
        if not entries:
            # 空目录不算「读通了」：垃圾字节也经常解出 file_count=0，
            # 只在没有别的解释时才拿它当空包。
            empty = empty or (pos, entries, hlen)
            continue
        ev = _eval_base(entries, filesize, pos, dir_end, file_base, version)
        score = (ev.bad, 0 if ev.disjoint else 1, 0 if ev.tail_ok else 1)
        if score == (0, 0, 0):
            return pos, entries, hlen, ev, True
        if best is None or score < best[0]:
            best = (score, pos, entries, hlen, ev)
    if best is not None:
        _score, pos, entries, hlen, ev = best
        return pos, entries, hlen, ev, False
    if empty is not None:
        pos, entries, hlen = empty
        return pos, entries, hlen, _Eval(0, "空包", 0, True, True), True
    raise PckError(f"读不出 PCK 目录：头部这些位置全试过了，没有一个能解析出完整"
                   f"目录（版本 {version}，文件 {filesize} 字节）")


def _read_dir(f, pos, version, hash_len, filesize):
    """从 pos 读一份目录，返回 (条目表, 目录结束位置)。"""
    if pos + 4 > filesize:
        raise PckError("目录起点越界")
    f.seek(pos)
    count = struct.unpack("<I", f.read(4))[0]
    if count <= 0 or count > MAX_ENTRIES:
        raise PckError(f"file_count 不合理: {count}")
    if count * MIN_RECORD > filesize:
        raise PckError(f"file_count={count} 超出文件体积")

    fixed = 8 + 8 + hash_len + (4 if version >= 2 else 0)
    entries: List[Entry] = []
    for _ in range(count):
        raw = f.read(4)
        if len(raw) < 4:
            raise PckError("目录被截断")
        plen = struct.unpack("<I", raw)[0]
        if plen <= 0 or plen > MAX_PATH:
            raise PckError(f"path_len 不合理: {plen}")
        name = f.read(plen)
        if len(name) < plen:
            raise PckError("目录被截断（路径读不全）")
        # 补齐到 4 字节：真 Godot 把补齐字节算进 path_len（这里 pad=0，末尾的 0 由
        # _text 去掉），简化写法不算（这里 pad>0，跳过去）。两种都吃得下。
        pad = (-plen) % 4
        if pad:
            f.seek(pad, os.SEEK_CUR)
        raw = f.read(fixed)
        if len(raw) < fixed:
            raise PckError("目录被截断（记录不完整）")
        offset, size = struct.unpack_from("<QQ", raw, 0)
        flags = struct.unpack_from("<I", raw, 16 + hash_len)[0] if version >= 2 else 0
        # 这里只挡明显不合理的值；「数据够不够」交给 _eval_base 按文件大小判，
        # 这样被截断的包还能读出目录、跳过缺失的那几条，而不是整个包报废。
        if size > MAX_FIELD or offset > MAX_FIELD:
            raise PckError(f"条目数值不合理: offset={offset} size={size}")
        text = _text(name)
        if not text or any(ch < " " or ch == "\x7f" for ch in text):
            raise PckError("路径不像是路径，这个起点应该不是目录")
        entries.append(Entry(path=text, offset=offset, size=size, flags=flags))

    dir_end = f.tell()
    if dir_end > filesize:
        raise PckError("目录超出文件末尾")
    return entries, dir_end


def _text(raw: bytes) -> str:
    raw = raw.rstrip(b"\x00")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _eval_base(entries, filesize, dir_pos, dir_end, file_base, version):
    """挑 offset 基准：谁能让所有数据都落在文件内、互不重叠就信谁。

    Godot 4.x 存相对 file_base 的偏移，Godot 3.x 存文件绝对偏移，两种都算一遍，
    版本只决定先试哪个。数据区还要和目录区互不交叉（真 Godot 两种摆放都有）。
    都不成立时取越界最少的那个，由调用方把「数据可能错位」写进报告。
    """
    cands = []
    if version >= 2 and file_base:
        cands.append((f"相对 file_base=0x{file_base:X}（Godot 4 写法）", file_base))
    cands.append(("文件绝对偏移（Godot 3 写法）", 0))
    if file_base and version < 2:
        cands.append((f"相对 file_base=0x{file_base:X}", file_base))

    best = None
    done = set()
    for kind, base in cands:
        if base in done:                    # v2 且 file_base=0 时两种解释等价
            continue
        done.add(base)
        bad = 0
        lo = None
        hi = 0
        prev_end = HEADER_MIN               # 数据不可能落在固定头部里
        for start, size in sorted((base + e.offset, e.size) for e in entries):
            if start < prev_end or start + size > filesize:
                bad += 1
            lo = start if lo is None else min(lo, start)
            hi = max(hi, start + size)
            prev_end = max(prev_end, start + size)
        # 数据区要么整段在目录之前，要么整段在目录之后，不能和目录你中有我
        if hi <= dir_pos:
            slack = dir_pos - hi
        elif dir_end <= lo:
            slack = filesize - hi
        else:
            slack = None
        ev = _Eval(base=base, kind=kind, bad=bad, disjoint=slack is not None,
                   tail_ok=slack is not None and 0 <= slack <= TAIL_SLACK)
        if ev.bad == 0 and ev.disjoint and ev.tail_ok:
            return ev
        score = (ev.bad, 0 if ev.disjoint else 1, 0 if ev.tail_ok else 1)
        if best is None or score < best[0]:
            best = (score, ev)
    return best[1]


# ---------------------------------------------------------------- 解包
def extract(path, writer, origin: str = "", progress: Optional[Callable] = None) -> Report:
    """把 pck 里的文件写进 writer。

    writer 是 me.collect.writer.OutputWriter。异常分两档：整个包读不通（魔数、
    版本、加密目录、目录完全乱）抛 PckError；单条记录有问题（越界、加密、被标记
    删除）只记账跳过，不影响其它文件。
    """
    path = Path(path)
    idx = read_index(path)
    rep = Report(total=len(idx.entries), index=idx)
    if not idx.base_confident:
        rep.warnings.append("offset 基准没能完全对上，解出来的文件可能错位")
    if idx.flags:
        rep.warnings.append(f"头部 flags=0x{idx.flags:X}（含非默认标志位）")

    with open(path, "rb") as f:
        for i, e in enumerate(idx.entries):
            if progress:
                progress(i + 1, rep.total, os.path.basename(e.path)[:32])
            if e.flags & FILE_REMOVAL:
                rep.skipped_removed += 1
                continue
            if e.flags & FILE_ENCRYPTED:
                rep.skipped_encrypted += 1
                continue

            pos = idx.base + e.offset
            if pos < HEADER_MIN or pos + e.size > idx.filesize:
                rep.skipped_broken += 1
                continue
            rel, sneaky = _clean_path(e.path)
            if sneaky:
                rep.escaped.append(e.path)
            cat = kinds.classify(rel)
            if not writer.wants(cat):
                rep.skipped_filtered += 1
                continue
            if _write_entry(writer, cat, rel, f, pos, e.size, origin):
                rep.written += 1
            else:
                rep.skipped_broken += 1

    if progress:
        progress(rep.total, rep.total, "完成")
    return rep


def _clean_path(path: str):
    """把 pck 里的路径变成安全的相对路径，顺带报出目录穿越企图。"""
    p = path.replace("\\", "/").strip()
    for prefix in ("res://", "user://"):
        if p.startswith(prefix):
            p = p[len(prefix):]
    p = p.lstrip("/")
    sneaky = any(seg == ".." for seg in p.split("/"))
    return sanitize_rel(p), sneaky


def _write_entry(writer, category: str, rel: str, handle, offset: int,
                 size: int, origin: str) -> bool:
    """分块把一个条目从 pck 搬到输出目录，返回是否真的写出来了。

    小文件走 writer.write_bytes；大文件不能整块读进内存，而 writer 的公开接口只有
    「整块 bytes」和「从路径 copy_from」两种，pck 里却是偏移+长度，所以这里借
    writer 内部 _prepare/_record 这两个动作自己分块写；取不到就退回整块写。
    """
    handle.seek(offset)
    prepare = getattr(writer, "_prepare", None)
    record = getattr(writer, "_record", None)
    if size <= SMALL or prepare is None or record is None:
        data = handle.read(size)
        return (len(data) == size
                and writer.write_bytes(category, rel, data, origin) is not None)

    dest = prepare(category, rel)
    if dest is None:
        return False
    done = 0
    with open(dest, "wb") as fo:
        while done < size:
            block = handle.read(min(CHUNK, size - done))
            if not block:
                break
            fo.write(block)
            done += len(block)
    if done != size:
        return False
    record(category, dest, done, origin)
    return True
