# -*- coding: utf-8 -*-
"""data.win 结构重建 —— 读原文件，重新搭一份自洽结构，再填回去（V0.15）。

和「就地改几个 u32」的区别
--------------------------
就地改写是拿原字节当模板打补丁，补丁的合法性来自「这次改对了」。
重建是**先把原 chunk 解析成模型，再按模型重新生成字节，最后把新结构
填回原来的位置**。生成出来的东西只依赖模型，不依赖原字节里剩下的垃圾。

为什么是「填回原位置」而不是「重排整个文件」
--------------------------------------------
data.win 里所有指针都是**文件绝对偏移**。一旦把某个 chunk 挪位置：
  * 别的 chunk 里指向它的指针要跟着改；
  * 它里面指向别处的指针要按新的基准改。
要改干净就得把 23 个 chunk 全部建成完整模型（UTMT 那种规模的活）。
填回原 span 则别人一个字节都不用动，只需要三条约束：

  1. 新结构长度 ≤ 原 span，多出来的补零（chunk 长度字段不变）；
  2. 指向**本 chunk 之外**的指针原样保留 —— 它指向的绝对偏移没变；
  3. 指向**本 chunk 内部**的指针按新布局重算。

FONT 为什么能重建
-----------------
FONT 的条目是定长头 + 变长字形表，变长部分自己的头里就写着字形数：

    size(条目) = H + G * (4 + 16)      G = u32(条目 + 40)

H 不硬编码，从样本里**量**出来（相邻两个可信指针之差减掉 20G，取众数）。
实测 Undertale / 这两个样本都量到 H = 44、字形记录 16 字节。

另外：条目数据紧跟指针表起排，所以从「表尾」按 size 一路走下来就能得到
每一项的起点。**走出来的起点必须与所有未损坏的指针逐一吻合**，否则
说明模型不成立，直接放弃 —— 宁可不动，也不能改错。
"""

import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from me.formats.gamemaker_win import Chunk, WinFile

# FONT 条目：定长头 + G 个 u32 字形指针 + G 个 16 字节字形记录
GLYPH_REC = 16
GLYPH_PTR = 4
GLYPH_COUNT_OFF = 40          # 定长头里「字形数」的偏移
MIN_HEADER = GLYPH_COUNT_OFF + 4
# 量不出 H 时的兜底（实测值，仅当样本里没有可信相邻对才会用到）
FALLBACK_HEADER = 44
# 走出来的条目允许在 chunk 末尾留多少补零
TAIL_SLACK = 64

# 全文件反查「有没有别的地方指进 FONT」很慢（64 MB 文件要几秒），
# 而且 GameMaker 的资源引用按下标走、跨 chunk 直接指进 FONT 的设计上不存在，
# 所以默认关掉，只有 --deep 自证时才跑。见 _scan_external_pointers。
DEEP_SCAN = False

# 只改坏掉的指针槽，条目字节一个都不动。用来分辨问题出在
# 「指针表」还是「条目内容」—— 见 --pointers-only。
POINTERS_ONLY = False


class RebuildError(Exception):
    """模型不成立，拒绝重建。"""


@dataclass
class Finding:
    level: str                 # err / warn / info
    chunk: str
    code: str
    message: str
    detail: str = ""

    def __str__(self) -> str:
        tag = {"err": "[err]", "warn": "[warn]", "info": "[i]"}.get(
            self.level, self.level)
        where = f" {self.chunk}" if self.chunk else ""
        extra = f"  ({self.detail})" if self.detail else ""
        return f"  {tag}{where} {self.message}{extra}"


@dataclass
class Font:
    """一个 FONT 条目。头 44 字节原样保留，只重排它和它的字形。"""
    header: bytes
    glyphs: List[bytes] = field(default_factory=list)
    origin: int = 0            # 原文件里的起点（只用于报告）

    @property
    def glyph_count(self) -> int:
        return struct.unpack_from("<I", self.header, GLYPH_COUNT_OFF)[0]

    def size(self, header_len: int) -> int:
        return header_len + self.glyph_count * (GLYPH_PTR + GLYPH_REC)


@dataclass
class RebuildReport:
    src: str = ""
    dst: str = ""
    findings: List[Finding] = field(default_factory=list)
    rebuilt: List[str] = field(default_factory=list)   # 重建过的 chunk
    old_bytes: int = 0
    new_bytes: int = 0
    notes: List[str] = field(default_factory=list)
    font_ok: bool = False      # 重建后 FONT 自检通过（不再有越界指针）

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.level == "err"]


# ══════════════════════════════════════════════════════════════════
#  量头部长度
# ══════════════════════════════════════════════════════════════════

def measure_header(w: WinFile, ch: Chunk, ptrs: Sequence[int]) -> Tuple[int, int]:
    """量出 FONT 条目的定长头长度 H。

    做法：对每一对「相邻且都在 chunk 内」的可信指针，
    ``H = 后继 - 前驱 - 20 * 前驱的字形数``。取众数。

    返回 (H, 投票数)。投票数 < 2 说明样本里没有可信相邻对。
    """
    from collections import Counter
    d = w.raw
    votes = Counter()
    for i in range(len(ptrs) - 1):
        a, b = ptrs[i], ptrs[i + 1]
        if not (ch.base <= a < b < ch.end):
            continue
        if a + MIN_HEADER > ch.end:
            continue
        g = struct.unpack_from("<I", d, a + GLYPH_COUNT_OFF)[0]
        if g > 1 << 20:
            continue
        h = (b - a) - g * (GLYPH_PTR + GLYPH_REC)
        if MIN_HEADER <= h <= 4096:
            votes[h] += 1
    if not votes:
        return 0, 0
    h, n = votes.most_common(1)[0]
    return h, n


# ══════════════════════════════════════════════════════════════════
#  FONT
# ══════════════════════════════════════════════════════════════════

def walk_font(w: WinFile, ch: Chunk, count: int, header_len: int) -> List[int]:
    """从指针表尾开始，按 size 走出每一项的起点。"""
    starts: List[int] = []
    off = ch.base + 4 + 4 * count
    for _ in range(count):
        if off + MIN_HEADER > ch.end:
            raise RebuildError(
                f"走到第 {len(starts) + 1} 项时越出 chunk（偏移 {off:,}）")
        starts.append(off)
        g = struct.unpack_from("<I", w.raw, off + GLYPH_COUNT_OFF)[0]
        if g > 1 << 20:
            raise RebuildError(
                f"第 {len(starts)} 项的字形数不合理（{g:,}，偏移 {off:,}）")
        off += header_len + g * (GLYPH_PTR + GLYPH_REC)
    if off > ch.end + TAIL_SLACK:
        raise RebuildError(
            f"走完 {count} 项到了 {off:,}，超出 chunk 末尾 {ch.end:,}")
    return starts


def _scan_external_pointers(w: WinFile, ch: Chunk, table_end: int) -> List[str]:
    """查有没有「别的 chunk 里的 u32」指向 FONT 内部（--deep 才跑）。

    指向 FONT 内部的东西一旦被我重排就会失效，所以要先确认没有。
    代价是整文件扫一遍 u32（64 MB 的文件约几秒），默认不跑 ——
    GameMaker 的资源引用按**下标**走，跨 chunk 直接指进 FONT 的设计上不存在，
    这一扫是防意外，不是常规路径。
    """
    from collections import Counter
    d = w.raw
    n = len(d) // 4
    vals = memoryview(d)[:n * 4].cast("I")      # 零拷贝，只看 4 字节对齐处
    hits = Counter()
    base, end = ch.base, ch.end
    for idx, v in enumerate(vals):
        if v <= base or v >= end:
            continue
        off = idx * 4
        if ch.base + 4 <= off < table_end:
            continue                            # FONT 自己的指针表
        for name in w.order:
            c = w.chunks[name]
            if c.base <= off < c.end:
                if name != "FONT":
                    hits[name] += 1
                break
    return [f"{k}×{v}" for k, v in hits.most_common(6)]


def parse_fonts(w: WinFile) -> Tuple[List[Font], List[Finding]]:
    """把 FONT 解析成模型。损坏的指针靠「走」补回来。"""
    out: List[Finding] = []
    ch = w.chunks.get("FONT")
    if ch is None or ch.size < 8:
        return [], out

    d = w.raw
    count = w.u32(ch.base)
    if count == 0:
        return [], out
    table_end = ch.base + 4 + 4 * count
    if table_end > ch.end:
        raise RebuildError(f"FONT 指针表越界, count={count:,}")

    ptrs = [w.u32(ch.base + 4 + 4 * i) for i in range(count)]
    good = [i for i, p in enumerate(ptrs) if ch.base <= p < ch.end]
    bad = [i for i in range(count) if i not in good]

    out.append(Finding(
        "info", "FONT", "table",
        f"{count} 项, 可信 {len(good)}"
        + (f", 损坏 {len(bad)} 下标 {bad[:8]}" if bad else "")))

    header_len, votes = measure_header(w, ch, ptrs)
    if header_len <= 0:
        header_len, votes = FALLBACK_HEADER, 0
        out.append(Finding("warn", "FONT", "header",
                           f"定长头未能量出, 按 {FALLBACK_HEADER} 代入"))
    else:
        out.append(Finding("info", "FONT", "header",
                           f"定长头 {header_len} 字节, {votes} 票"))

    starts = walk_font(w, ch, count, header_len)

    # 走出来的起点必须与所有可信指针吻合 —— 这是模型成立与否的判据
    mismatch = [(i, ptrs[i], starts[i]) for i in good if ptrs[i] != starts[i]]
    if mismatch:
        raise RebuildError(
            "起点与可信指针不符, 模型不成立: "
            + "; ".join(f"[{i}] {a:,} != {b:,}" for i, a, b in mismatch[:4]))
    if bad:
        out.append(Finding("err", "FONT", "pointers",
                           f"{len(bad)}/{count} 个指针落在 chunk 之外",
                           "按走出的起点重写"))
    if DEEP_SCAN:
        ext = _scan_external_pointers(w, ch, table_end)
        if ext:
            out.append(Finding("warn", "FONT", "external",
                               "chunk 之外还有 u32 落进 FONT 内部", " ".join(ext)))

    fonts: List[Font] = []
    bad_glyph = 0
    for i, off in enumerate(starts):
        header = bytes(d[off:off + header_len])
        if len(header) < header_len:
            raise RebuildError(f"第 {i} 项头部不完整")
        g = struct.unpack_from("<I", header, GLYPH_COUNT_OFF)[0]
        # 字形记录按**指针数组**取，不能顺着往下读（数组可以乱序）。
        # 但数组本身也可能坏 —— 判据是它必须是紧凑布局：
        #   第 k 个指针 = 记录区首址 + k*16
        # 实测两个样本的每一项都满足（第 0 项：首指针 = 项起点 + 428 = 44+4*96）。
        # 不满足就说明数组不可信，改按紧凑布局读，并按布局重写指针。
        rec_base = off + header_len + GLYPH_PTR * g
        if rec_base + GLYPH_REC * g > ch.end:
            raise RebuildError(f"第 {i} 项字形区越出 chunk")
        gps = [struct.unpack_from("<I", d, off + header_len + GLYPH_PTR * k)[0]
               for k in range(g)]
        canonical = [rec_base + k * GLYPH_REC for k in range(g)]
        if gps != canonical:
            bad_glyph += 1
            out.append(Finding(
                "err", "FONT", "glyphs",
                f"第 {i} 项字形指针 {g} 项不符紧凑布局",
                "按布局重写指针"))
            gps = canonical
        fonts.append(Font(header=header,
                          glyphs=[bytes(d[p:p + GLYPH_REC]) for p in gps],
                          origin=off))

    # 字形记录里如果藏着**真的**指向 FONT 内部的指针，重排就会打断它。
    # 注意不能只看「值落在 FONT 地址区间」：字形记录里是 u16 对，
    # 一个 CJK 码位（如 0xB044=45124）配上一个小 SourceX 就能拼出
    # 一个正好落在 FONT 区间里的 u32。实测 data1.win 这样误判了 21 个。
    # 真指针必须落在结构边界上：要么是某一项的起点，要么在字形记录网格上。
    start_set = set(starts)
    real_inner = 0
    for f, fstart in zip(fonts, starts):
        g = f.glyph_count
        rec_base = fstart + header_len + GLYPH_PTR * g
        for rec in f.glyphs:
            for k in range(0, GLYPH_REC, 4):
                v = struct.unpack_from("<I", rec, k)[0]
                if v in start_set:
                    real_inner += 1
                elif rec_base <= v < rec_base + GLYPH_REC * g and (v - rec_base) % GLYPH_REC == 0:
                    real_inner += 1
    if real_inner:
        raise RebuildError(
            f"字形记录含 {real_inner} 个 u32 指向 FONT 结构边界, 不可重排")
    return fonts, out


def build_font_chunk(fonts: Sequence[Font], header_len: int,
                     span: int, base: int) -> bytes:
    """按模型重新生成整个 FONT chunk 的字节（含长度字段）。

    ``base`` 是 chunk 数据在**文件里的绝对偏移**。data.win 的指针全是文件
    绝对偏移，所以写指针时都要加 base —— 第一版直接写了 chunk 内的相对
    位置（84、2048 这种），UTMT 立刻报「把 ResourceById<EmbeddedAudio>
    转成 UndertaleFont 失败」：它顺着这些小数去文件开头读到别的东西了。
    """
    table_end = 4 + 4 * len(fonts)
    body = bytearray()
    body += struct.pack("<I", len(fonts))
    checks: List[Tuple[str, int]] = []

    # 先算好每一项的起点，再填指针表
    off = table_end
    starts = []
    for f in fonts:
        starts.append(off)
        off += f.size(header_len)
    for i, s in enumerate(starts):
        body += struct.pack("<I", base + s)
        checks.append((f"指针表[{i}]", base + s))

    for i, (f, s) in enumerate(zip(fonts, starts)):
        assert len(body) == s, f"布局算错：{len(body)} != {s}"
        body += f.header
        g = f.glyph_count
        rec_base = base + s + header_len + GLYPH_PTR * g
        for k in range(g):
            v = rec_base + k * GLYPH_REC
            body += struct.pack("<I", v)
            checks.append((f"项{i}.字形[{k}]", v))
        for rec in f.glyphs:
            body += rec

    if len(body) > span:
        raise RebuildError(
            f"重建后的 FONT 需要 {len(body):,} 字节，原 span 只有 {span:,}")

    # 自检：写进去的每一个指针都必须是本 chunk 内的文件绝对偏移。
    # 少了这一步，「写成了 chunk 内相对偏移」这种错会一路走到 UTMT 才炸。
    lo, hi = base, base + len(body)
    for label, v in checks:
        if not (lo <= v < hi):
            raise RebuildError(f"重建结果里 {label} 越界：{v:,} 不在 {lo:,}..{hi:,}")

    body += bytes(span - len(body))          # 补零，长度不变
    return b"FONT" + struct.pack("<I", span) + bytes(body)


# ══════════════════════════════════════════════════════════════════
#  UTMT 的对齐判据（实测推出来的）
# ══════════════════════════════════════════════════════════════════
#
# 两个样本 UTMT 都卡在「记录边界 + 1」上，拿一份它读得通的 Undertale 当对照
# 才看清规矩：
#
#   * STRG 的字符串按**指针表顺序**逐条读，每条 = u32 长度 + 正文 + 1 个 NUL；
#     读完最后一条之后，reader 要**补齐到 4 字节边界**，且补的字节必须是 0。
#     实测：data.win 走完 63,977 条停在 16,028,093，UTMT 报 16,028,094 ——
#     它从 093 起读补位，第一个字节是 0x08 不是 0，于是抛在 094。
#     Undertale 走完停在 1,794,714，后面 102 字节是 0，所以没事。
#
#   * TXTR 的纹理数据是同一条规矩：读完一条 blob 之后补齐到 4 字节边界。
#     实测：data1.win 的条目 2 blob 长 2,769,618，尾 67,188,718（模 4 = 2），
#     紧跟的就是条目 15 的 blob 的 PNG 签名（89 50 …）→ 抛在 67,188,719。
#
# 所以修复就是两件事：把该是 0 的补位字节写成 0；写不了的就把 blob 挪到
# 能对齐的地方（blob 只被 TXTR 条目引用，挪它们是安全的）。

PNG_SIG = b"\x89PNG\r\n\x1a\n"

# 纹理数据要按 128 字节对齐 —— 这是拿读得通的 Undertale 那份量出来的：
#   条目读完后到第一条 blob 之间 92 字节、两条 blob 之间 8 字节、
#   data1.win 里 52 字节，全部正好把上一条的末尾补到 128 的倍数：
#     1,794,852 -> 1,794,944（128×14,023）    92 字节
#     2,216,184 -> 2,216,192（128×17,314）     8 字节
#     12,374,092 -> 12,374,144（128×96,673）  52 字节
# 而两个坏样本的 blob 起点全都不对齐（16,306,955 / 57,387,430 …）。
# 只补到 4 字节时，UTMT 会在第一条数据处报 padding error 并把位置往后推
# 1 字节 —— 因为它扫的是 128 字节跨度里的非零字节。
TEX_ALIGN = 128


def _align4(x: int) -> int:
    return (x + 3) & ~3


def _align_tex(x: int) -> int:
    return (x + TEX_ALIGN - 1) // TEX_ALIGN * TEX_ALIGN


def png_len(d: bytes, off: int) -> int:
    """从 PNG 签名起按块结构走到 IEND，返回整条的长度；不是 PNG 返回 -1。

    blob 是**裸 PNG，没有长度前缀**（条目里的 +0 是标志位，不是长度），
    长度只能这样算出来。实测三个文件的每一条都算得出来。
    """
    if off < 0 or off + 8 > len(d) or d[off:off + 8] != PNG_SIG:
        return -1
    i, n = off + 8, len(d)
    while i + 8 <= n:
        ln = struct.unpack_from(">I", d, i)[0]
        typ = d[i + 4:i + 8]
        if ln > (1 << 24):
            return -1
        i += 12 + ln
        if typ == b"IEND":
            return i - off
    return -1


@dataclass
class TexBlob:
    index: int          # 指针表下标
    entry: int          # TXTR 条目偏移
    old: int            # 原来的数据偏移
    length: int
    new: int = -1


def _targets(d: bytes, values) -> int:
    """整文件扫 4 字节对齐的 u32，数一数有几个等于 values 里的地址。

    只用来回答「这几位我要覆盖，会不会有别的指针指着它」。
    values 通常只有几个地址，偶然撞上的概率可以忽略。
    """
    want = set(values)
    if not want:
        return 0
    n = len(d) // 4
    hits = 0
    for v in memoryview(d)[:n * 4].cast("I"):
        if v in want:
            hits += 1
    return hits


def _collect_blobs(w: WinFile) -> Tuple[Optional[Chunk], List[TexBlob]]:
    """把 TXTR 的纹理数据收集成模型。任何一条对不上就整体放弃。"""
    ch = w.chunks.get("TXTR")
    if ch is None or ch.size < 8:
        return None, []
    d = w.raw
    try:
        tex = w.textures()
    except Exception:                                  # noqa: BLE001
        return ch, []
    out: List[TexBlob] = []
    for i, t in enumerate(tex):
        if not (0 < t.blob < len(d) - 8):
            return ch, []
        L = png_len(d, t.blob)
        if L <= 0:
            return ch, []                              # 有不是裸 PNG 的，不动
        out.append(TexBlob(index=i, entry=t.off, old=t.blob, length=L))
    return ch, out


def _free_windows(ch: Chunk, used: List[Tuple[int, int]],
                  min_size: int) -> List[Tuple[int, int]]:
    """chunk span 里没被占用的连续区间，按大小降序。"""
    out: List[Tuple[int, int]] = []
    cur = ch.base
    for a, b in sorted(used):
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if ch.end > cur:
        out.append((cur, ch.end))
    out = [(a, b) for a, b in out if b - a >= min_size]
    out.sort(key=lambda x: -(x[1] - x[0]))
    return out


def _all_zero(d: bytes, a: int, b: int) -> bool:
    """一段区间是不是全零。只有全零的区间才敢当搬家的落脚点。"""
    return not any(d[a:b])


def repair_txtr(w: WinFile, out: bytearray,
                notes: List[str]) -> Tuple[int, int, int]:
    """让每条纹理数据的末尾落在 4 字节边界上（UTMT 的 padding 判据）。

    原则：**先全部算完、验完，再写**。中途发现不安全就整体放弃，不留半成品
    （第一版边算边写，放弃时前半截已经改掉了，文件就被改坏了）。

    落脚点只有两种：
      * 原地——按地址顺序重排，起点对齐、条间补零，靠原来的空隙吸收补位；
      * 搬家——挪到「同一 chunk span 内、且整段全零」的空白区，只改 TXTR
        条目里的 blob 指针（blob 不被别的东西引用）。

    返回 (处理条数, 迁移条数, 补位字节数)；放弃时返回 (0, 0, 0)。
    """
    ch, blobs = _collect_blobs(w)
    if not blobs:
        return 0, 0, 0
    d = w.raw
    field = _blob_field(w)
    if field < 0:
        notes.append("TXTR: blob 字段未定位, 跳过")
        return 0, 0, 0

    # 按「落在哪个 chunk 里」分组 —— 不同 chunk 之间的数据不能互相覆盖
    groups: Dict[str, List[TexBlob]] = {}
    for b in blobs:
        key = next((n for n in w.order
                    if w.chunks[n].base <= b.old < w.chunks[n].end), "?")
        groups.setdefault(key, []).append(b)

    # 空白区：默认「整个 chunk 都算占用」，但 TXTR 例外 —— 它的内容就是
    # 指针表 + 条目表 + 纹理数据，中间那些大空隙是**真空白**，正好放搬家的
    # 纹理数据。原来一律按整段算，结果只在 STRG 尾部找得到落脚点；
    # 一旦退到后备的成段空白区（落在 AUDO 覆盖的范围里），UTMT 就崩：
    # data1.win 报 `Read underflow at 04033C74 while reading chunk TXTR`。
    used = [(w.chunks[n].off, w.chunks[n].end) for n in w.order]
    cht = w.chunks.get("TXTR")
    if cht is not None:
        used = [u for u in used if u != (cht.off, cht.end)]
        used.append((cht.off, cht.base))                          # chunk 头
        used.append((cht.base, cht.base + 4 + 4 * len(blobs)))    # 指针表
        used += [(b.entry, b.entry + field + 4) for b in blobs]    # 条目
        used += [(b.old, b.old + b.length) for b in blobs]         # 纹理数据
    windows: List[Tuple[int, int]] = []
    for name in w.order:
        ch2 = w.chunks[name]
        for a, b2 in _free_windows(ch2, [u for u in used
                                         if u[0] >= ch2.off and u[1] <= ch2.end],
                                   1 << 16):
            # TXTR 自己的段内不用查全零：那片地址按 chunk 划分只属于 TXTR，
            # 里面要么是我正在搬走的纹理数据，要么是死字节。
            if name == "TXTR" or _all_zero(out, a, b2):
                windows.append((a, b2))
    # STRG 尾部那段补位清零之后是真正的空白区，可以给纹理数据当落脚点
    # （按 chunk 划分它属于 STRG，所以上面那轮按 span 算的抓不到它）。
    chs = w.chunks.get("STRG")
    if chs is not None:
        ns = w.u32(chs.base)
        if ns and 4 + 4 * ns <= chs.size:
            ps = [w.u32(chs.base + 4 + 4 * i) for i in range(ns)]
            lns = w.u32(ps[-1])
            if lns <= 65535:
                ends = ps[-1] + 4 + lns + 1
                if ends < chs.end and _all_zero(out, ends, chs.end):
                    windows.append((ends, chs.end))
    # 文件里成段的空白区当**后备**落脚点。优先用 chunk 内部的空白区（含 STRG
    # 尾部）：实测把纹理数据塞进 AUDO 覆盖的那片空白会让 UTMT 在
    # UndertaleEmbeddedTexture+TexData 处报 count 越界，而塞进 STRG 尾部没事。
    fallback_windows = [(a, b) for a, b in _zero_runs(bytes(out), 1 << 16)]

    def pack(seq):
        prev_end = 0
        cur = 0
        pl = []
        for b in seq:
            cur = _align_tex(max(b.old, prev_end))
            pl.append((b, cur))
            prev_end = cur + b.length
        return pl, prev_end

    # 先处理「原地就能排下」的组：它们排完会空出一大片，正好给装不下的组当落脚点
    order = sorted(groups.items(),
                   key=lambda kv: pack(sorted(kv[1], key=lambda b: b.old))[1]
                   - max(b.old + b.length for b in kv[1]))
    placements: List[Tuple[TexBlob, int]] = []
    zero_ranges: List[Tuple[int, int]] = []
    for name, grp in order:
        grp.sort(key=lambda b: b.old)
        end_fp = max(b.old + b.length for b in grp)
        kept = list(grp)
        moved_here = []
        pl, end = pack(kept)
        while end > end_fp:
            if len(kept) <= 1:
                notes.append(
                    f"TXTR: 需多 {end - end_fp:,} 字节, 无可用空白区 "
                    f"(chunk 末尾空余 {ch.end - end_fp:,}), 跳过")
                return 0, 0, 0
            b = kept.pop()
            # 挑一个装得下这条的窗口：先 primary（chunk 内部 / STRG 尾部），
            # 都装不下再用后备的成段空白区。
            fit = [w for w in sorted(windows, key=lambda x: -(x[1] - x[0]))
                   if w[1] - w[0] >= b.length]
            if not fit:
                fit = [w for w in sorted(fallback_windows,
                                         key=lambda x: -(x[1] - x[0]))
                       if w[1] - w[0] >= b.length]
            if not fit:
                notes.append(
                    f"TXTR: 需多 {end - end_fp:,} 字节, 无装得下的空白区 "
                    f"(chunk 末尾空余 {ch.end - end_fp:,}), 跳过")
                return 0, 0, 0
            wa, wb = fit[0]
            for lst in (windows, fallback_windows):
                if (wa, wb) in lst:
                    lst.remove((wa, wb))
                    break
            dst = _align_tex(wa)
            moved_here.append((b, dst))
            windows.append((_align4(dst + b.length), wb))
            pl, end = pack(kept)
        here = pl + moved_here
        # 只清「原地重排」范围内的空隙；搬到别处去的那些 blob 之间的区间
        # 不属于本组，清它会把 STRG 尾部、chunk 头这些真数据一起抹掉。
        prev = grp[0].old
        for b, dst in sorted(pl, key=lambda x: x[1]):
            # 只清「对齐补位」那么小的空隙（≤128 字节）。更宽的间隔是文件里
            # 原有的富余空间，可能还放着别的东西 —— data1.win 在 12,429,696
            # 就有数据，整段扫过去会把它抹掉。
            if prev < dst <= prev + TEX_ALIGN:
                zero_ranges.append((prev, dst))
            prev = dst + b.length
        placements += here
        # 排完空出来的这一段，给后面的组用
        if end_fp > end:
            windows.append((end, end_fp))

    # UTMT 读完最后一条纹理之后要算「本 chunk 还剩多少字节」。如果**最后一条**
    # 落在 chunk 末尾之后，这个数就变成负的 → 直接 Read underflow。
    # data.win 能过，正是因为它的搬家落脚点在 TXTR 末尾之前；
    # data1.win 的最后一条落在 67M（TXTR 末尾只有 23.78M），必须搬回段内。
    last_place = max(placements, key=lambda p: p[0].entry)
    lb, ldst = last_place
    if ldst + lb.length > ch.end:
        fit = [w for w in sorted(windows + fallback_windows,
                                 key=lambda x: -(x[1] - x[0]))
               if w[1] - w[0] >= lb.length and w[0] >= ch.base and w[1] <= ch.end]
        if fit:
            placements.remove(last_place)
            placements.append((lb, _align_tex(fit[0][0])))
            notes.append(f"TXTR: 末条 #{lb.index} {ldst:,} -> "
                         f"{_align_tex(fit[0][0]):,} (段内)")
        else:
            notes.append(f"TXTR: 末条 #{lb.index} 在 {ldst + lb.length:,}, "
                         f"超出 chunk 末尾 {ch.end:,}, 段内无空白区")

    # 全部算完，逐条验补位那几位没被人指着，然后才写
    copies: List[Tuple[TexBlob, int, bytes]] = []
    for b, dst in placements:
        pad = _align_tex(dst + b.length)
        bad = _targets(out, list(range(dst + b.length, pad)))
        if bad:
            notes.append(f"TXTR: 补位 {dst + b.length:,} 引用 {bad} 个, 保留")
        copies.append((b, dst, bytes(out[b.old:b.old + b.length])))

    # 空隙要清的字节，必须原本就是「被搬走的那条数据」或「补位」
    blobs_old = {(b.old, b.old + b.length) for b in blobs}
    for a, z in zero_ranges:
        for x in range(a, z):
            if out[x] == 0:
                continue
            if any(lo <= x < hi for lo, hi in blobs_old):
                continue
            notes.append(f"TXTR: 空隙 {x:,} 非补位字节, 跳过")
            return 0, 0, 0

    for a, z in zero_ranges:
        out[a:z] = bytes(z - a)
    for b, dst, data in copies:
        out[dst:dst + b.length] = data
        pad = _align_tex(dst + b.length)
        if pad > dst + b.length:
            out[dst + b.length:pad] = bytes(pad - dst - b.length)
        struct.pack_into("<I", out, b.entry + field, dst)

    moved = sum(1 for b, dst in placements if dst != b.old)
    need = sum(_align_tex(x.length) - x.length for x, _ in placements)
    return len(placements), moved, need


def _zero_runs(d: bytes, min_size: int = 1 << 16) -> List[Tuple[int, int]]:
    """找出文件里成段的空白区（默认 ≥64 KB）。

    这些段落全是零，说明本来就没有东西落在那儿 —— 拿它们当搬家的落脚点
    是安全的（指针指进全零区的文件本来也读不出东西）。
    """
    out: List[Tuple[int, int]] = []
    i, n = 0, len(d)
    pat = b"\x00" * min_size
    while True:
        j = d.find(pat, i)
        if j < 0:
            break
        a = j
        while a > 0 and d[a - 1] == 0:
            a -= 1
        b = j + min_size
        while b < n and d[b] == 0:
            b += 1
        out.append((a, b))
        i = b
    return out


def repair_audo(w: WinFile, out: bytearray,
                notes: List[str]) -> Tuple[int, int]:
    """让每条音频数据落在 4 字节边界上（UTMT 的 padding 判据）。

    音频条目 = ``u32 长度 + 数据``，条目之间正常留 2 字节 —— 那正好是把上一条
    补到 4 字节对齐（对照组 43/48 处都是 2 字节间隔，data1.win 是 214/222）。
    两个坏样本里出现 1 和 3 字节的间隔：整段音频被那个改写工具挪偏了 1 字节
    （data.win 的 AUDO 首指针 29,071,375 是奇数），于是 UTMT 在
    「blob 末尾 + 3」处报 padding error。

    AUDO 的指针表直接就是条目地址，重排不用量字段槽位，只改表。

    返回 (处理条数, 迁移条数)；放弃或本来对齐时返回 (0, 0)。
    """
    ch = w.chunks.get("AUDO")
    if ch is None or ch.size < 8:
        return 0, 0
    d = w.raw
    n = w.u32(ch.base)
    if n == 0 or 4 + 4 * n > ch.size:
        return 0, 0
    ptrs = [w.u32(ch.base + 4 + 4 * i) for i in range(n)]
    blobs: List[Tuple[int, int, int]] = []          # (表下标, 原地址, 长度)
    for i, p in enumerate(ptrs):
        if not (0 < p < len(d) - 4):
            return 0, 0
        ln = w.u32(p)
        if ln > (1 << 28) or p + 4 + ln > len(d):
            return 0, 0
        blobs.append((i, p, ln))

    def pack():
        # 每条都补到 4 字节（含最后一条）：UTMT 读下一条之前要把位置对齐，
        # 而音频段从指针表右边界开始，没有前移的余地，补位只能往后长。
        cur = _align4(blobs[0][1])
        pl = []
        for i, p, ln in blobs:
            pl.append((i, cur, ln, p))
            cur = _align4(cur + 4 + ln)
        return pl, cur

    plan, cur = pack()
    if cur > ch.end:
        notes.append(f"AUDO: 需到 {cur:,}, 越出 chunk 末尾 {ch.end:,}, 跳过")
        return 0, 0
    # 已经全对齐就不用动
    if all(dst == old for _, dst, _, old in plan):
        notes.append(f"AUDO: {len(plan)} 条已在 4 字节边界")
        return 0, 0

    # ---- 先算完、验完 ----
    first_old = blobs[0][1]
    end_fp = max(p + 4 + ln for _, p, ln in blobs)
    # 末尾补位可能压到紧跟其后的字符串记录（data.win / data2.win 都是这样，
    # 那条名字的长度字段会变成 0）。音频段从指针表右边界起步、前面没地方可退，
    # 所以没法躲开，只能如实报出来。
    if cur > end_fp:
        delta = cur - end_fp
        low = ch.base + 4 + 4 * n
        new_first = first_old - delta
        if new_first >= low and all(d[x] == 0 for x in range(new_first, first_old)):
            plan = [(i, dst - delta, ln, p) for i, dst, ln, p in plan]
            first_old -= delta
            cur -= delta
            notes.append(f"AUDO: 整体前移 {delta:,} 字节, 末尾回到 {cur:,}")
        else:
            hit = _string_record_overlap(w, end_fp, cur)
            if hit:
                notes.append(f"AUDO: 末尾补位 {end_fp:,} -> {cur:,} 覆盖字符串记录 "
                             f"{hit:,}, 该名字变空")
    gaps: List[Tuple[int, int]] = []
    prev = first_old
    for _, dst, ln, _ in plan:
        if dst > prev:
            gaps.append((prev, min(dst, cur)))
        prev = dst + 4 + ln
    old_span = {(p, p + 4 + ln) for _, p, ln in blobs}
    for a, z in gaps:
        for x in range(a, z):
            if d[x] == 0:
                continue
            if any(lo <= x < hi for lo, hi in old_span):
                continue
            notes.append(f"AUDO: 空隙 {x:,} 非补位字节, 跳过")
            return 0, 0

    copies = [(dst, bytes(d[p + 4:p + 4 + ln])) for _, dst, ln, p in plan]
    # ---- 再写 ----
    for a, z in gaps:
        out[a:z] = bytes(z - a)
    last_i = len(plan) - 1
    for k, ((i, dst, ln, _), (_, data)) in enumerate(zip(plan, copies)):
        end = dst + 4 + len(data)
        out[dst:dst + 4] = struct.pack("<I", len(data))
        out[dst + 4:end] = data
        pad = _align4(end)                # 含最后一条：见 pack() 的说明
        if pad > end:
            out[end:pad] = bytes(pad - end)
        struct.pack_into("<I", out, ch.base + 4 + 4 * i, dst)

    moved = sum(1 for _, dst, _, old in plan if dst != old)
    notes.append(f"AUDO: 末尾 {end_fp:,} -> {cur:,}")
    return len(plan), moved


def _blob_field(w: WinFile) -> int:
    """量出 TXTR 条目里 blob 字段的槽位偏移（0.14 已实现的那套量法）。

    注意 ``Texture.blob`` 是**数据的文件偏移**，不是字段值 ——
    第一版写成 ``t.blob - t.off`` 得到 256，于是把新指针写进了第一条纹理
    数据里，直接把 PNG 头覆盖掉了。这里量完还要回头核对：
    字段里现在装的必须正是第一条的旧偏移。
    """
    try:
        n, ptrs = w.list_chunk("TXTR")
    except Exception:                                  # noqa: BLE001
        return -1
    if not ptrs:
        return -1
    try:
        field = w._discover_texture_field(ptrs)
    except Exception:                                  # noqa: BLE001
        return -1
    if field < 0:
        return -1
    # 核对：每个条目在这个槽位里装的都应当是它自己的 blob 偏移
    try:
        tex = w.textures()
    except Exception:                                  # noqa: BLE001
        return -1
    for t in tex:
        if w.u32(t.off + field) != t.blob:
            return -1
    return field


def _string_record_overlap(w: WinFile, lo: int, hi: int) -> int:
    """区间 [lo, hi) 压在 STRG 表里的某条字符串记录上就返回那条记录的起点。

    STRG 的字符串可以放在文件任何位置（指针是绝对偏移），所以「AUDO 末尾往后
    多写几字节」只有在压到字符串才算真损坏。
    """
    ch = w.chunks.get("STRG")
    if ch is None or ch.size < 8:
        return 0
    n = w.u32(ch.base)
    if n == 0 or 4 + 4 * n > ch.size:
        return 0
    d = w.raw
    for i in range(n):
        p = w.u32(ch.base + 4 + 4 * i)
        if not (0 < p < len(d) - 5):
            continue
        ln = w.u32(p)
        if ln > 65535:
            continue
        end = p + 4 + ln + 1
        if p < hi and end > lo:
            return p
    return 0


def repair_strg_tail(w: WinFile, out: bytearray, notes: List[str]) -> int:
    """把 STRG 尾部补位清零到 128 字节边界（UTMT 的 padding 判据）。

    UTMT 源码（UndertaleChunkSTRG.UnserializeChunk）：

        while (reader.AbsPosition % 0x80 != 0)
            if (reader.ReadByte() != 0) throw new IOException("Padding error in STRG");

    起点是「按指针表顺序最后读的那条」的末尾，终点是**下一个 128 的倍数**，
    不是 chunk 末尾。所以 data.win 那一大段尾部之所以要全零，是因为那段里
    恰好没有任何要读的东西；而 data2.win 的最后一条停在 5,961,135，
    下一个 128 边界 5,961,216 已经越过 chunk 末尾（5,961,176）→
    读越界。这种情况清零没用，只能挪数据，这里如实报出来。
    """
    ch = w.chunks.get("STRG")
    if ch is None or ch.size < 8:
        return 0
    d = w.raw
    n = w.u32(ch.base)
    if n == 0 or 4 + 4 * n > ch.size:
        return 0
    ptrs = [w.u32(ch.base + 4 + 4 * i) for i in range(n)]
    last = ptrs[-1]
    if not (0 <= last < len(d) - 5):
        return 0
    ln = w.u32(last)
    if ln > 65535:
        return 0
    end = last + 4 + ln + 1                 # 含 1 个 NUL

    bound = _align_tex(end)                 # 补位要走到 128 的倍数
    if bound > ch.end:
        notes.append(f"STRG: 补位需到 {bound:,}, 超出 chunk 末尾 {ch.end:,} "
                     f"{bound - ch.end:,} 字节, 未处理")
        return 0
    if end >= ch.end:
        return 0
    if all(d[x] == 0 for x in range(end, ch.end)):
        notes.append(f"STRG: 尾部补位 {ch.end - end:,} 字节已为零")
        return 0

    # 这一段里有几个「从别的 chunk 指进来」的 u32 —— 只报告，不拦截
    outside = 0
    lo, hi = end, ch.end
    n4 = len(d) // 4
    for idx, v in enumerate(memoryview(d)[:n4 * 4].cast("I")):
        if lo <= v < hi:
            off = idx * 4
            if not (ch.base <= off < ch.end):
                outside += 1

    out[end:ch.end] = bytes(ch.end - end)
    notes.append(f"STRG: 尾部补位 {end:,}..{ch.end:,} 清零 {ch.end - end:,} 字节"
                 + (f", 外部 u32 {outside} 个" if outside else ""))
    return ch.end - end


# ══════════════════════════════════════════════════════════════════
#  诊断 / 重建
# ══════════════════════════════════════════════════════════════════

def _string_ok(d: bytes, off: int, end: int) -> bool:
    """off 处像不像一条字符串记录（u32 长度 + 能解出来的正文）。

    不要求结尾是 NUL：GameMaker 的字符串记录有没有终结符要看版本，
    拿它当判据会把好文件判坏（实测 data.win 的 13979 条被误判）。
    所以只要求：长度合理、落在文件内、正文能按 UTF-8 解出来。
    """
    if off < 0 or off + 5 > end:
        return False
    ln = struct.unpack_from("<I", d, off)[0]
    if ln > 65535 or off + 4 + ln > end:
        return False
    try:
        d[off + 4:off + 4 + ln].decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


# 指针表指向**自己块内**条目的 chunk —— 这类可以要求指针落在块内。
SELF_ADDRESSING = (
    "SOND", "AGRP", "SPRT", "BGND", "PATH", "SCPT", "SHDR", "FONT",
    "TMLN", "OBJT", "ROOM", "DAFL", "TPAG", "CODE", "TXTR", "AUDO",
)
# 指针表指向**别的 chunk**。STRG 的指针指向字符串数据，
# 判据换成「指向的记录本身是否完整」+ 数据放在哪儿。
CROSS_ADDRESSING = ("STRG",)
# 指针形态还没吃透的 chunk：只报数，不下结论。
# VARI/FUNC 的指针在 Undertale 这种 UTMT 读得通的文件里同样大量「不像字符串」，
# 说明是判据不成立，不是文件坏了 —— 宁可不报，也不能误报。
INFERRED = ("VARI", "FUNC")


def diagnose(w: WinFile) -> List[Finding]:
    """扫一遍能证明的结构问题。不跑 UTMT，也不改任何东西。"""
    out: List[Finding] = []

    if not w.chain_ok:
        out.append(Finding("err", "", "chain",
                           f"chunk 链停在 {w.chain_end:,}，文件 {len(w.raw):,}"))
    first = 8 + 8 + w.gen8_len
    if w.order and w.chunks[w.order[0]].off != first:
        out.append(Finding("err", w.order[0], "align",
                           f"首个 chunk 在 {w.chunks[w.order[0]].off:,}，"
                           f"应为 {first:,}"))
    out.append(Finding("info", "", "shell",
                       f"bytecode {w.bytecode()}, {len(w.order)} 个 chunk, "
                       f"链{'完整' if w.chain_ok else '不完整'}"))

    d = w.raw
    for name in w.order:
        ch = w.chunks[name]
        if ch.size < 8 or name in ("OPTN", "LANG", "EXTN", "GEN8"):
            continue
        n = w.u32(ch.base)
        if n == 0:
            continue
        if 4 + 4 * n > ch.size:
            out.append(Finding("warn", name, "count",
                               f"count={n:,} 指针表越出 chunk"))
            continue
        ptrs = [w.u32(ch.base + 4 + 4 * i) for i in range(n)]

        if name in SELF_ADDRESSING:
            bad = [i for i, p in enumerate(ptrs) if not (ch.base <= p < ch.end)]
            if bad:
                out.append(Finding("err", name, "pointers",
                                   f"{len(bad)}/{n} 个指针落在 chunk 之外",
                                   f"前几个下标 {bad[:6]}"))
            elif n > 2 and any(ptrs[i] >= ptrs[i + 1] for i in range(n - 1)):
                out.append(Finding("warn", name, "order", "指针不是递增的"))
        elif name in INFERRED:
            inside = sum(1 for p in ptrs if ch.base <= p < ch.end)
            out.append(Finding(
                "info", name, "shape",
                f"{n} 项，指针在块内 {inside} 项（形态未吃透，不作判据）"))
        elif name in CROSS_ADDRESSING:
            # 只看「指到的东西本身完不完整」。STRG 的字符串数据实测有
            # 一大截落在 AUDO 的字节范围里（指针是绝对偏移，数据放哪儿都行），
            # 所以「出块」本身不算错。
            inside = sum(1 for p in ptrs if ch.base <= p < ch.end)
            broken = [i for i, p in enumerate(ptrs)
                      if not _string_ok(d, p, len(d))]
            if broken:
                # 这条只是**判据**没过，不等于名字坏了：实测 data.win 有
                # 6,492 条落到这里，但重建前后精灵/背景/音效的名字一个没丢
                # （2583/2583、249/249、443/443），UTMT 也照读不误。
                # 所以降成 warn，别拿它当「必须处理」。
                out.append(Finding("warn", name, "records",
                                   f"{len(broken)}/{n} 条未过长度/UTF-8 判据",
                                   f"下标 {broken[:6]}"))
            elif inside < n:
                home = {}
                for p in ptrs:
                    if not (ch.base <= p < ch.end):
                        for other in w.order:
                            oc = w.chunks[other]
                            if oc.base <= p < oc.end:
                                home[other] = home.get(other, 0) + 1
                                break
                out.append(Finding(
                    "warn", name, "outside",
                    f"{n - inside}/{n} 条数据不在本 chunk 内",
                    " ".join(f"{k}×{v}" for k, v in
                             sorted(home.items(), key=lambda kv: -kv[1])[:4])))

            # STRG 末尾的补位要走到 128 的倍数，那一步会越过 chunk 末尾就读炸
            if name == "STRG" and ptrs:
                last = ptrs[-1]
                if 0 <= last < len(d) - 5:
                    ln = w.u32(last)
                    if ln <= 65535:
                        end = last + 4 + ln + 1
                        bound = _align_tex(end)
                        if bound > ch.end:
                            out.append(Finding(
                                "err", name, "pad128",
                                f"补位需到 {bound:,}, 超出 chunk 末尾 "
                                f"{ch.end:,} {bound - ch.end:,} 字节",
                                "清零无法解决"))

    try:
        _, fs = parse_fonts(w)
        out += fs
    except RebuildError as exc:
        out.append(Finding("err", "FONT", "model", str(exc)))
    return out


def rebuild_bytes(raw: bytes, want: Optional[Sequence[str]] = None) -> Tuple[bytes, RebuildReport]:
    """重建一份新字节。解析原 data → 建新结构 → 填回原 span。"""
    rep = RebuildReport(old_bytes=len(raw))
    w = WinFile(raw)
    rep.findings = diagnose(w)
    # 默认修这三块：FONT 的指针表、STRG 的尾部补位、TXTR 的纹理数据对齐
    targets = ({n.upper() for n in want} if want
               else {"FONT", "STRG", "TXTR", "AUDO"})

    out = bytearray(raw)
    font_count = 0

    if "FONT" in targets:
        fonts, fs = parse_fonts(w)
        if fonts:
            ch = w.chunks["FONT"]
            table = [w.u32(ch.base + 4 + 4 * i) for i in range(len(fonts))]
            header_len, _ = measure_header(w, ch, table)
            if header_len <= 0:
                header_len = FALLBACK_HEADER
            if POINTERS_ONLY:
                # 只把坏掉的指针槽改成走出来的真实起点，条目一个字节不动。
                # 用来分辨「问题在指针表」还是「问题在条目内容」。
                fixed = 0
                for i, f in enumerate(fonts):
                    slot = ch.base + 4 + 4 * i
                    if struct.unpack_from("<I", out, slot)[0] != f.origin:
                        struct.pack_into("<I", out, slot, f.origin)
                        fixed += 1
                rep.rebuilt.append(f"FONT: 指针表 {fixed} 槽重写")
            else:
                new = build_font_chunk(fonts, header_len, ch.size, ch.base)
                out[ch.off:ch.end] = new
                rep.rebuilt.append(
                    f"FONT: {len(fonts)} 项, 正文 {len(new) - 8:,}"
                    f"/{ch.size:,} 字节")
            font_count = len(fonts)
            rep.findings += fs

    # ---- 记录末尾的 4 字节补位（UTMT 的 padding 判据）----
    if "STRG" in targets:
        n = repair_strg_tail(w, out, rep.notes)
        if n:
            rep.rebuilt.append(f"STRG: 尾部补位清零 {n:,} 字节")
    if "TXTR" in targets:
        cnt, moved, need = repair_txtr(w, out, rep.notes)
        if cnt:
            rep.rebuilt.append(f"TXTR: {cnt} 条, 迁移 {moved} 条, "
                               f"补位 {need:,} 字节")
    if "AUDO" in targets:
        cnt, moved = repair_audo(w, out, rep.notes)
        if cnt:
            rep.rebuilt.append(f"AUDO: {cnt} 条, 迁移 {moved} 条")

    new_raw = bytes(out)
    if len(new_raw) != len(raw):
        raise RebuildError(
            f"文件长度改变: {len(new_raw):,} != {len(raw):,}")
    rep.new_bytes = len(new_raw)

    # 自检：重建后的字节必须自己能读，且 FONT 不再有越界指针
    w2 = WinFile(new_raw)
    left = [f for f in diagnose(w2) if f.level == "err"]
    # 重建之后重新诊断一遍，结论单独标 after: —— 原来的 findings 里还带着
    # 重建**前**的问题，混在一起会把已经修好的又报成「仍有问题」。
    rep.findings += [Finding(f.level, f.chunk, "after:" + f.code,
                             f.message, f.detail) for f in left]
    rep.font_ok = bool(font_count) and not any(f.chunk == "FONT" for f in left)
    if rep.font_ok:
        rep.notes.append(f"FONT: 指针 {font_count} 项全在 chunk 内")
    rep.notes.append(f"诊断: err {len(left)} 项")
    return new_raw, rep


def rebuild_file(src, dst=None, want: Optional[Sequence[str]] = None) -> RebuildReport:
    src = Path(src)
    raw = src.read_bytes()
    new_raw, rep = rebuild_bytes(raw, want)
    rep.src = str(src)
    if dst is None:
        dst = src.with_name(src.stem + "_rebuilt" + src.suffix)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(new_raw)
    rep.dst = str(dst)
    return rep


# ══════════════════════════════════════════════════════════════════
#  命令行（自查用）
# ══════════════════════════════════════════════════════════════════

def main(argv: Optional[List[str]] = None) -> int:
    global DEEP_SCAN, POINTERS_ONLY
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--deep" in argv:
        DEEP_SCAN = True
        argv.remove("--deep")
    only = None
    for i, a in enumerate(argv):
        if a == "--only" and i + 1 < len(argv):
            only = [x.strip().upper() for x in argv[i + 1].split(",") if x.strip()]
            del argv[i:i + 2]
            break
    if "--pointers-only" in argv:
        POINTERS_ONLY = True
        argv.remove("--pointers-only")
    if not argv:
        print("用法: python -m me.formats.gamemaker_rebuild <data.win> [目标] "
              "[--deep] [--pointers-only] [--only FONT,STRG,...]")
        return 2
    src = Path(argv[0])
    dst = Path(argv[1]) if len(argv) > 1 else None

    w = WinFile.load(src)
    print("=" * 74)
    print(f"  {src.name}  {len(w.raw):,} 字节")
    print("=" * 74)
    for f in diagnose(w):
        print(f)

    if dst is None:
        print("\n诊断完成, 未写出（给目标路径才会重建）")
        return 0

    rep = rebuild_file(src, dst, want=only)
    print("\n" + "=" * 74)
    print("  重建")
    print("=" * 74)
    for line in rep.rebuilt:
        print(f"  [ok] {line}")
    for n in rep.notes:
        print(f"  [i] {n}")
    errs = [f for f in rep.findings if f.level == "err"]
    print(f"  写出 {rep.dst}  {rep.new_bytes:,} 字节")
    print(f"  诊断 err {len(errs)} 项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
