# -*- coding: utf-8 -*-
"""GameMaker 容器：语义模型（AST）。

为什么要有这一层
----------------
V0.14/0.15 的做法是「在原字节上缝补」：找到坏指针就改写，找到补位就清零。
这条路在 data1.win/data2.win 上走不通 —— 每修一处就顶出另一处，因为
**文件长度和布局是被一串绝对偏移绑死的**，局部改动没有自由度。

V0.16 换一条路：把文件读成对象，再从对象写出一份全新的文件。
- 这个模块只存**语义**：字符串、字形、字体的字段值，以及它们之间的引用关系；
- 里面没有任何文件偏移。偏移是布局的产物，一律由 Serializer 计算。

引用怎么表达
------------
模型里要引用别的对象时，直接持有那个对象（``FontItem.name`` 就是那个
``GmString``）。Serializer 在写字节时把「持有关系」翻译成绝对偏移。

这样解析代码和写字节的代码就分开了：解析只管把字节读成语义，
布局改动（对齐、搬家、改长度）不会碰到解析逻辑。

自校验
------
每个 chunk 都有 ``verify(built)``：拿**写出来的字节**反过来核对模型。
不满足就抛 ``ModelError``，绝不带着错误写出文件。
"""

import struct
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ══════════════════════════════════════════════════════════════════
#  错误
# ══════════════════════════════════════════════════════════════════


class ModelError(Exception):
    """模型不成立（读不出来，或写出去自检不过）。"""


# ══════════════════════════════════════════════════════════════════
#  chunk 基类
# ══════════════════════════════════════════════════════════════════


class Chunk:
    """一个 chunk。``name`` 是文件里那 4 个字节。

    ``guard`` 是 chunk 收尾的零字节数，**默认 0**。

    为什么以前不是 0
    ----------------
    早先为了让 UTMT 读完最后一个结构时那次「多读 1 个字节」不越界，给所有块
    都补了 4 个零。补位确实挡住了越界，但 UTMT 的长度校验是

        if (读到的长度 != 头部写的长度) 警告 "…(N padding?)" 然后跳过

    补位写进了长度、却没被任何结构读走，于是**每个块都弹一次警告**。实测只有
    FONT 真的多读（见 ``FontTable.guard``），其余块读完的位置正好等于结构长度。

    现在规矩是：头部的长度 == 结构实际长度 == UTMT 读到的长度，三者必须相等，
    块与块之间不留任何无主字节。``guard`` 只在**有实测依据**的块上非 0。
    """

    name: str = ""
    guard: int = 0

    def emit(self, w) -> None:
        """把数据区写进 writer（不含名字和长度字段）。"""
        raise NotImplementedError

    def verify(self, built) -> None:
        """拿写出来的字节核对。子类按需重写。"""
        return None

    # 便于调试
    def __repr__(self) -> str:                       # pragma: no cover
        return f"<{type(self).__name__} {self.name}>"


class RawChunk(Chunk):
    """原样搬运的 chunk：还没建模的部分先这么挂着。

    ``guard = 0``：原字节一个不改（长度由原文件自己的对齐保证）。

    这类 chunk 内部的绝对偏移**不会**被重算 —— 所以只要布局变了，
    它里面指向别处的指针就会失效。这是分阶段重构的临时状态，
    最终目标是所有 chunk 都换成模型。
    """

    guard = 0

    def __init__(self, name: str, data: bytes):
        self.name = name
        self.data = bytes(data)

    def emit(self, w) -> None:
        w.raw(self.data)

    def __repr__(self) -> str:                       # pragma: no cover
        return f"<RawChunk {self.name} {len(self.data):,}B>"


# ══════════════════════════════════════════════════════════════════
#  STRG：字符串表
# ══════════════════════════════════════════════════════════════════


class GmString:
    """一条字符串，对应 STRG 里的一条记录。

    记录形状（UTMT ``UndertaleString.Unserialize``）::

        u32 字节长度 | 正文（不含 NUL） | 1 个 NUL

    **两种地址**，别混用：

    * 记录起点（长度字段所在处）—— STRG 自己的指针表存的是这个，
      因为它是 ``ReadUndertaleObjectPointer``；
    * 正文起点（记录起点 + 4）—— 别处的 name 字段存的是这个，
      因为 ``ReadUndertaleString`` 会做 ``addr - 4``。

    所以同一串字符串在文件里有两个有效地址，Serializer 分别提供
    ``ref()`` 和 ``string_ref()``。
    """

    __slots__ = ("text", "addr")

    def __init__(self, text: bytes):
        if not isinstance(text, (bytes, bytearray)):
            raise ModelError(f"StringTable 的正文必须是 bytes，收到 {type(text)}")
        self.text = bytes(text)
        self.addr = -1                      # 由 Serializer 在布局阶段填

    def emit(self, w) -> None:
        w.begin(self)                       # 记录起点
        w.u32(len(self.text))
        w.raw(self.text)
        w.u8(0)

    def __repr__(self) -> str:                       # pragma: no cover
        try:
            shown = self.text[:24].decode("utf-8")
        except UnicodeDecodeError:
            shown = repr(self.text[:24])
        return f"<GmString {shown!r}>"

    def __eq__(self, other) -> bool:
        return isinstance(other, GmString) and other.text == self.text

    def __hash__(self) -> int:
        return hash(self.text)


class StringTable(Chunk):
    """STRG。

    规范布局（本模块写出的一定是这个形状）::

        u32 count
        u32 记录起点[count]
        记录[count]            # 按表顺序紧挨着排
        0x00 补位                # 补到**文件绝对** 128 边界

    最后那条补位是关键：UTMT 读完最后一条后是

        while (reader.AbsPosition % 0x80 != 0)
            if (reader.ReadByte() != 0) throw new IOException("Padding error in STRG");

    起点是「按表顺序最后读的那条」的末尾，终点是下一个 128 的倍数。
    所以补位必须走到 128 边界，而且那一步不能越过 chunk 末尾 ——
    本模块直接让 chunk 长到那个边界，于是这件事自动成立。
    """

    name = "STRG"

    ALIGN = 0x80

    def __init__(self, strings: Sequence[GmString]):
        self.strings: List[GmString] = list(strings)

    # ---- 读 ----
    @classmethod
    def from_win(cls, w) -> "StringTable":
        """从 WinFile 读成模型。

        字符串的正文可以在文件任何位置（指针是绝对偏移），读的时候按指针取，
        不能假设它跟在表后面 —— data2.win 就有 29 条正文压在 AUDO 段末尾。
        """
        ch = w.chunks.get("STRG")
        if ch is None or ch.size < 8:
            raise ModelError("没有 STRG chunk")
        n = w.u32(ch.base)
        if n == 0 or 4 + 4 * n > ch.size:
            raise ModelError(f"STRG 指针表越界: count={n:,}, span={ch.size:,}")
        raw = w.raw
        out: List[GmString] = []
        for i in range(n):
            rec = w.u32(ch.base + 4 + 4 * i)
            if not (0 < rec < len(raw) - 5):
                raise ModelError(f"STRG[{i}] 指针 {rec:,} 不在文件内")
            ln = w.u32(rec)
            if ln > 0xFFFF or rec + 4 + ln + 1 > len(raw):
                raise ModelError(f"STRG[{i}] 长度 {ln:,} 越界（记录在 {rec:,}）")
            out.append(GmString(raw[rec + 4:rec + 4 + ln]))
        return cls(out)

    # ---- 写 ----
    def emit(self, w) -> None:
        w.u32(len(self.strings))
        for s in self.strings:
            w.ref(s)                        # 表里存记录起点
        for s in self.strings:
            s.emit(w)
        w.align_abs(self.ALIGN)             # 补到绝对 128 边界

    # ---- 自检 ----
    def verify(self, built) -> None:
        span = built.chunk_span(self.name)
        if span is None:
            raise ModelError("STRG 没有出现在输出里")
        base, end = span

        seen = set()
        for i, s in enumerate(self.strings):
            a = built.addr_of(s)
            if not (base <= a < end):
                raise ModelError(f"STRG[{i}] 记录起点 {a:,} 不在 chunk "
                                 f"{base:,}..{end:,} 内")
            if a in seen:
                raise ModelError(f"STRG[{i}] 记录起点与前面重复: {a:,}")
            seen.add(a)
            got = built.u32(a)
            if got != len(s.text):
                raise ModelError(f"STRG[{i}] 写出后长度字段 {got} != {len(s.text)}")
            tail = built.raw[a + 4 + len(s.text)]
            if tail != 0:
                raise ModelError(f"STRG[{i}] 结尾不是 NUL（{tail:#x}）")

        if not self.strings:
            return
        last = max(built.addr_of(s) + 4 + len(s.text) + 1 for s in self.strings)
        bound = (last + self.ALIGN - 1) // self.ALIGN * self.ALIGN
        if bound > end:
            raise ModelError(f"STRG 补位要走到 {bound:,}，越过 chunk 末尾 {end:,}")
        guard = built.guard_of(self.name)
        if end != bound + guard:
            raise ModelError(f"STRG 末尾 {end:,} 不是补位边界 {bound:,} + 守卫 {guard}")
        if any(built.raw[x] for x in range(last, bound)):
            raise ModelError("STRG 末尾补位里有非零字节")


# ══════════════════════════════════════════════════════════════════
#  FONT：字体表
# ══════════════════════════════════════════════════════════════════


FONT_TABLE = 4                  # u32 count
FONT_HEADER_DEFAULT = 44        # 定长头（含 name/display 两个指针）
FONT_COUNT_OFF = 40             # 定长头里「字形数」的偏移
TEXTURE_SLOT = 28              # 定长头里 Texture（TPAG 指针）的偏移
FONT_MIN_HEADER = FONT_COUNT_OFF + 4
GLYPH_REC = 16                  # 字形记录
GLYPH_PTR = 4
GLYPH_SIZE = GLYPH_REC
# 走出来的条目允许在 chunk 末尾留多少补零
FONT_TAIL_SLACK = 64


def measure_font_header(w, ch, ptrs: Sequence[int]) -> Tuple[int, int]:
    """量出 FONT 定长头的长度。

    对每一对「相邻且都在 chunk 内」的可信指针：``H = 后继 - 前驱 - 20*字形数``，
    取众数。返回 ``(H, 票数)``；票数不足说明样本里没有可信相邻对。
    """
    from collections import Counter
    votes: Counter = Counter()
    for i in range(len(ptrs) - 1):
        a, b = ptrs[i], ptrs[i + 1]
        if not (ch.base <= a < b < ch.end):
            continue
        if a + FONT_COUNT_OFF + 4 > ch.end:
            continue
        g = struct.unpack_from("<I", w.raw, a + FONT_COUNT_OFF)[0]
        if g > 1 << 20:
            continue
        h = b - a - g * (GLYPH_PTR + GLYPH_REC)
        if FONT_MIN_HEADER <= h <= 4096:
            votes[h] += 1
    if not votes:
        return -1, 0
    h, n = votes.most_common(1)[0]
    return h, n


def walk_font_items(w, ch, count: int, header_len: int) -> List[int]:
    """从指针表尾开始，按 ``header_len + 20*G`` 走出每一项的起点。"""
    starts: List[int] = []
    off = ch.base + FONT_TABLE + GLYPH_PTR * count
    for _ in range(count):
        if off + FONT_MIN_HEADER > ch.end:
            raise ModelError(
                f"走到第 {len(starts) + 1} 项时越出 chunk（偏移 {off:,}）")
        starts.append(off)
        g = struct.unpack_from("<I", w.raw, off + FONT_COUNT_OFF)[0]
        if g > 1 << 20:
            raise ModelError(f"第 {len(starts)} 项字形数不合理: {g:,}")
        off += header_len + g * (GLYPH_PTR + GLYPH_REC)
    if off > ch.end + FONT_TAIL_SLACK:
        raise ModelError(f"走完 {count} 项到了 {off:,}，超出 chunk 末尾 {ch.end:,}")
    return starts


class Glyph:
    """一条字形记录。16 字节整体保留：只认得开头那个码位，其余原样。

    为什么不当成有名字段来存：字形记录的字段名随版本变过，而它的字节
    **不需要重算** —— 里面全是坐标和码位，没有指向别处的偏移。整体保存
    能让没动过的字形逐字节一致，也就保住了可核对性。
    """

    __slots__ = ("data", "addr")

    def __init__(self, data: bytes):
        if len(data) != GLYPH_SIZE:
            raise ModelError(f"字形记录必须是 {GLYPH_SIZE} 字节，收到 {len(data)}")
        self.data = bytes(data)
        self.addr = -1

    @property
    def code(self) -> int:
        return struct.unpack_from("<H", self.data, 0)[0]

    def emit(self, w) -> None:
        w.begin(self)
        w.raw(self.data)

    def __repr__(self) -> str:                       # pragma: no cover
        return f"<Glyph {self.code:#x}>"


@dataclass
class FontItem:
    """一个 FONT 条目。

    规范布局::

        +0   u32  Name        -> 正文起点（string_ref）
        +4   u32  DisplayName -> 正文起点
        +8   ...  定长头剩余字段（原样保留，长度 = header_len - 8 - 4）
        +40  u32  字形数 G
        +44  u32  字形记录起点[G]      # 紧密数组：rec_base + k*16
             16B 字形记录[G]

    字形指针数组写成**紧密布局**是刻意的：UTMT 读它按指针逐个 seek，
    数组本身错乱（data1.win/data2.win 都是这样）就只能靠这份规范把它拉直。
    ``origin`` 记原文件里的起点，只用于报告，不参与写出。
    """

    name: Optional[GmString] = None
    display: Optional[GmString] = None
    texture: Optional[Record] = None  # 字体用的 TPAG 条目
    tail: bytes = b""                 # 定长头 +8 .. +count_off
    glyphs: List[Glyph] = field(default_factory=list)
    origin: int = 0
    addr: int = -1

    @property
    def header_len(self) -> int:
        return 8 + len(self.tail) + 4

    def emit(self, w) -> None:
        w.begin(self)
        if self.name is not None:
            w.string_ref(self.name)
        else:
            w.u32(0)
        if self.display is not None:
            w.string_ref(self.display)
        else:
            w.u32(0)
        w.raw(self.tail[:TEXTURE_SLOT - 8])
        if self.texture is not None:
            w.ref(self.texture)
        else:
            w.u32(0)
        w.raw(self.tail[TEXTURE_SLOT - 8 + 4:])
        w.u32(len(self.glyphs))
        for g in self.glyphs:
            w.ref(g)
        for g in self.glyphs:
            g.emit(w)

    def expected_size(self) -> int:
        return self.header_len + GLYPH_PTR * len(self.glyphs) \
            + GLYPH_SIZE * len(self.glyphs)


class FontTable(Chunk):
    """FONT：``u32 count`` + ``u32 条目起点[count]`` + 条目本体。"""

    name = "FONT"

    # UTMT 的 FONT 读完之后比结构本身多走 4 个字节（实测：guard=0 时报
    # ``Reading out of chunk bounds ... while reading UndertaleFont``，
    # guard=4 时读满、且 ``EnsureLengthFromHere`` 不再报补位告警）。
    # 这 4 个字节必须算进 chunk 长度，所以留在这里而不是全局。
    guard = 4


    def __init__(self, items: Sequence[FontItem]):
        self.items: List[FontItem] = list(items)

    # ---- 读 ----
    @classmethod
    def from_win(cls, w, header_len: Optional[int] = None,
                 strings: Optional["StringTable"] = None,
                 tpag: Optional["OpaqueListChunk"] = None) -> "FontTable":
        """读成模型。

        指针坏了靠**走**补回来：条目是 ``header_len + 20*G`` 定长，
        从表尾开始按这个步长就能走出真实起点。走出来的起点必须和所有
        未损坏的指针逐一吻合，否则模型不成立 —— 那时候宁可报错，
        也不要把一条错位的结构写成新文件。

        ``strings`` 传进来时按对象复用（名字要指向同一批 GmString，
        否则写出去会变成两套互不相干的字符串）。
        """
        ch = w.chunks.get("FONT")
        if ch is None or ch.size < 8:
            raise ModelError("没有 FONT chunk")
        raw = w.raw
        count = w.u32(ch.base)
        if count == 0:
            return cls([])
        if FONT_TABLE + GLYPH_PTR * count > ch.size:
            raise ModelError(f"FONT 指针表越界: count={count:,}")

        ptrs = [w.u32(ch.base + FONT_TABLE + GLYPH_PTR * i) for i in range(count)]
        good = [i for i, p in enumerate(ptrs) if ch.base <= p < ch.end]

        if header_len is None:
            header_len, _votes = measure_font_header(w, ch, ptrs)
            if header_len <= 0:
                header_len = FONT_HEADER_DEFAULT
        starts = walk_font_items(w, ch, count, header_len)

        mismatch = [(i, ptrs[i], starts[i]) for i in good if ptrs[i] != starts[i]]
        if mismatch:
            raise ModelError(
                "走出来的起点与可信指针不符: "
                + "; ".join(f"[{i}] {a:,} != {b:,}" for i, a, b in mismatch[:4]))

        # 名字字段：这两个指针指向 STRG 的正文
        table = strings if strings is not None else (
            StringTable.from_win(w) if "STRG" in w.chunks else None)
        by_text = {s.text: s for s in table.strings} if table else {}
        tpag_by_addr = {r.origin: r for r in tpag.entries} if tpag else {}

        def as_string(value: int) -> Optional[GmString]:
            if not (0 < value < len(raw) - 4):
                return None
            rec = value - 4                     # 正文指针 -> 记录起点
            if not (0 < rec < len(raw) - 5):
                return None
            ln = w.u32(rec)
            if ln > 0xFFFF or rec + 4 + ln + 1 > len(raw):
                return None
            return by_text.get(raw[rec + 4:rec + 4 + ln])

        items: List[FontItem] = []
        for i, off in enumerate(starts):
            body = raw[off:off + header_len]
            if len(body) < header_len:
                raise ModelError(f"FONT[{i}] 头部不完整（起点 {off:,}）")
            g = struct.unpack_from("<I", body, FONT_COUNT_OFF)[0]
            rec_base = off + header_len + GLYPH_PTR * g
            if rec_base + GLYPH_REC * g > ch.end:
                raise ModelError(f"FONT[{i}] 字形区越出 chunk")
            recs = [Glyph(raw[rec_base + k * GLYPH_REC:
                              rec_base + (k + 1) * GLYPH_REC])
                    for k in range(g)]
            tex = tpag_by_addr.get(struct.unpack_from("<I", body, TEXTURE_SLOT)[0]) \
                if tpag_by_addr else None
            items.append(FontItem(
                name=as_string(struct.unpack_from("<I", body, 0)[0]),
                display=as_string(struct.unpack_from("<I", body, 4)[0]),
                texture=tex,
                tail=bytes(body[8:FONT_COUNT_OFF]),
                glyphs=recs, origin=off))
        return cls(items)

    # ---- 写 ----
    def emit(self, w) -> None:
        w.u32(len(self.items))
        for f in self.items:
            w.ref(f)
        for f in self.items:
            f.emit(w)

    # ---- 自检 ----
    def verify(self, built) -> None:
        span = built.chunk_span(self.name)
        if span is None:
            raise ModelError("FONT 没有出现在输出里")
        base, end = span
        for i, f in enumerate(self.items):
            a = built.addr_of(f)
            if not (base <= a < end):
                raise ModelError(f"FONT[{i}] 条目起点 {a:,} 不在 chunk 内")
            # 名字字段必须指向 STRG 里那条字符串的**正文**（记录起点 + 4）
            for slot, s in ((0, f.name), (4, f.display)):
                if s is None:
                    continue
                want = built.addr_of(s) + 4
                got = built.u32(a + slot)
                if got != want:
                    raise ModelError(
                        f"FONT[{i}] 槽位 +{slot} 写出 {got:,}，"
                        f"应为字符串正文 {want:,}")
            if built.u32(a + FONT_COUNT_OFF) != len(f.glyphs):
                raise ModelError(f"FONT[{i}] 写出后字形数不符")
            rec_base = a + f.header_len + GLYPH_PTR * len(f.glyphs)
            for k, g in enumerate(f.glyphs):
                want = rec_base + k * GLYPH_SIZE
                got = built.addr_of(g)
                if got != want:
                    raise ModelError(
                        f"FONT[{i}] 字形[{k}] 起点 {got:,} != 紧密布局 {want:,}")
                if built.u32(a + f.header_len + GLYPH_PTR * k) != got:
                    raise ModelError(f"FONT[{i}] 字形[{k}] 指针回填不一致")
                if built.raw[want:want + GLYPH_SIZE] != g.data:
                    raise ModelError(f"FONT[{i}] 字形[{k}] 字节不一致")
            if rec_base + GLYPH_SIZE * len(f.glyphs) > end:
                raise ModelError(f"FONT[{i}] 字形区越出 chunk 末尾")


class Record:
    """列表型 chunk 里的一条定长记录。

    记录本身**原样保留**，只把声明过的槽位换成符号引用。这样「还没完全
    吃透」的 chunk 也能安全重建：不动看不懂的字节，但仍然重算指针。

    ``refs``：槽位偏移 -> (目标对象, 种类)
    种类 ``obj`` 写记录起点，``str`` 写字符串正文（记录起点 + 4）。
    """

    __slots__ = ("data", "refs", "addr", "origin")

    def __init__(self, data: bytes, refs: Optional[Dict[int, tuple]] = None,
                 origin: int = 0):
        self.data = bytes(data)
        self.refs = dict(refs or {})
        self.addr = -1
        self.origin = origin            # 原文件里的地址（只用于建映射）

    def emit(self, w) -> None:
        w.begin(self)
        pos = 0
        for off in sorted(self.refs):
            if not (pos <= off <= len(self.data) - 4):
                raise ModelError(f"记录槽位 {off} 越界（长度 {len(self.data)}）")
            w.raw(self.data[pos:off])
            ref = self.refs[off]
            target, kind = ref[0], ref[1]
            delta = ref[2] if len(ref) > 2 else 0
            if kind == "str":
                w.string_ref(target)
            elif delta:
                w.ref_at(target, delta)
            else:
                w.ref(target)
            pos = off + 4
        w.raw(self.data[pos:])

    def __repr__(self) -> str:                       # pragma: no cover
        return f"<Record {len(self.data)}B>"


class OpaqueListChunk(Chunk):
    """``u32 count + u32 指针表 + 条目`` 的通用形态。

    TPAG 就是这一类：条目是 22 字节定长记录，里面**只有 u16 索引**
    （指向 TXTR 的第几页），没有任何绝对偏移；但它自己的指针表是绝对
    偏移，所以原字节照搬是不行的 —— 指针表必须按新布局重算。
    """


    def __init__(self, name: str, entries: Sequence[Record]):
        self.name = name
        self.entries: List[Record] = list(entries)

    @classmethod
    def from_win(cls, w, name: str) -> "OpaqueListChunk":
        ch = w.chunks.get(name)
        if ch is None or ch.size < 8:
            raise ModelError(f"没有 {name} chunk")
        raw = w.raw
        n = w.u32(ch.base)
        if n == 0:
            return cls(name, [])
        if 4 + 4 * n > ch.size:
            raise ModelError(f"{name} 指针表越界: count={n:,}")
        ptrs = [w.u32(ch.base + 4 + 4 * i) for i in range(n)]
        steps = [b - a for a, b in zip(ptrs, ptrs[1:]) if b > a]
        if not steps:
            raise ModelError(f"{name} 条目尺寸量不出来")
        step = max(set(steps), key=steps.count)
        if any(s != step for s in steps):
            raise ModelError(f"{name} 条目尺寸不一致: {sorted(set(steps))}")
        out = []
        for i, p in enumerate(ptrs):
            if not (ch.base <= p and p + step <= ch.end):
                raise ModelError(f"{name}[{i}] 条目 {p:,} 越出 chunk")
            out.append(Record(raw[p:p + step], origin=p))
        return cls(name, out)

    def emit(self, w) -> None:
        w.u32(len(self.entries))
        for r in self.entries:
            w.ref(r)
        for r in self.entries:
            r.emit(w)

    def verify(self, built) -> None:
        span = built.chunk_span(self.name)
        if span is None:
            raise ModelError(f"{self.name} 没有出现在输出里")
        base, end = span
        for i, r in enumerate(self.entries):
            a = built.addr_of(r)
            if not (base <= a < end):
                raise ModelError(f"{self.name}[{i}] 条目 {a:,} 不在 chunk 内")
            if built.raw[a:a + len(r.data)] != r.data:
                raise ModelError(f"{self.name}[{i}] 条目字节不一致")


# ══════════════════════════════════════════════════════════════════
#  数据块：TXTR / AUDO
# ══════════════════════════════════════════════════════════════════

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def png_length(d: bytes, off: int) -> int:
    """从 PNG 签名起按块结构走到 IEND，返回整条长度；不是 PNG 返回 -1。

    TXTR 的纹理数据是**裸 PNG，没有长度前缀**，长度只能这样算出来。
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


class Blob:
    """一段可被指针指向的数据。

    * TXTR 的纹理数据：裸字节，没有长度前缀（``with_length=False``）；
    * AUDO 的音频数据：``u32 长度 + 字节``（``with_length=True``）。

    ``addr`` 指向**长度字段**（AUDO）或**数据首字节**（TXTR），也就是
    UTMT 里 ``ReadUndertaleObjectPointer`` 登记的那个地址。
    """

    __slots__ = ("data", "with_length", "addr")

    def __init__(self, data: bytes, with_length: bool = False):
        self.data = bytes(data)
        self.with_length = with_length
        self.addr = -1

    def emit(self, w) -> None:
        w.begin(self)
        if self.with_length:
            w.u32(len(self.data))
        w.raw(self.data)

    def __repr__(self) -> str:                       # pragma: no cover
        return f"<Blob {len(self.data):,}B>"


class TextureTable(Chunk):
    """TXTR：纹理页。

    条目本身是定长记录（Scaled / GeneratedMips / 纹理数据指针，随版本多寡），
    模型只把**纹理数据指针**提出来做成符号引用，其余字节原样保留 ——
    不去猜那些字段的含义，也就不会猜错。

    规范布局::

        u32 count
        u32 条目起点[count]
        条目[count]
        0x00 补位到 128 边界
        纹理数据[count]        # 每条都从 128 的倍数开始

    UTMT 读完上一条数据后是
    ``while (AbsPosition % 0x80 != 0) ReadByte()``（必须全零），然后按条目里
    登记的地址读下一条。所以每条数据的起点都必须落在 128 边界上，这样
    补位长度才为 0..127 且不会压到下一条。
    """

    name = "TXTR"

    ALIGN = 0x80

    def __init__(self, records: Sequence[Record], blobs: Sequence[Blob],
                 field: int):
        self.records: List[Record] = list(records)
        self.blobs: List[Blob] = list(blobs)
        self.field = field
        if len(self.records) != len(self.blobs):
            raise ModelError("TXTR 条目数与纹理数据条数不一致")

    # ---- 读 ----
    @classmethod
    def from_win(cls, w) -> "TextureTable":
        ch = w.chunks.get("TXTR")
        if ch is None or ch.size < 8:
            raise ModelError("没有 TXTR chunk")
        raw = w.raw
        n = w.u32(ch.base)
        if n == 0:
            return cls([], [], -1)
        if 4 + 4 * n > ch.size:
            raise ModelError(f"TXTR 指针表越界: count={n:,}")
        ptrs = [w.u32(ch.base + 4 + 4 * i) for i in range(n)]

        # 条目尺寸：相邻指针差值的众数（这些文件里全部相等）
        steps = [b - a for a, b in zip(ptrs, ptrs[1:]) if b > a]
        if not steps:
            raise ModelError("TXTR 条目尺寸量不出来（指针不递增）")
        step = max(set(steps), key=steps.count)
        if any(s != step for s in steps):
            raise ModelError(f"TXTR 条目尺寸不一致: {sorted(set(steps))}")

        entries = []
        for i, p in enumerate(ptrs):
            if not (ch.base <= p and p + step <= ch.end):
                raise ModelError(f"TXTR[{i}] 条目 {p:,} 越出 chunk")
            entries.append(raw[p:p + step])

        # 纹理数据指针：找一个偏移，使每条的 u32 都落在一条合法 PNG 的起点上
        for off in range(0, step - 3, 4):
            cand = [struct.unpack_from("<I", e, off)[0] for e in entries]
            if not all(0 < v < len(raw) - 8 for v in cand):
                continue
            lens = [png_length(raw, v) for v in cand]
            if any(x <= 0 for x in lens):
                continue                      # 有一条不是 PNG，这个偏移不对
            blobs = [Blob(raw[v:v + x]) for v, x in zip(cand, lens)]
            records = [Record(e, {off: (b, "obj")})
                       for e, b in zip(entries, blobs)]
            return cls(records, blobs, off)
        raise ModelError("TXTR 里找不到纹理数据指针（没有一致的 PNG 起点）")

    # ---- 写 ----
    def emit(self, w) -> None:
        w.u32(len(self.records))
        for r in self.records:
            w.ref(r)
        for r in self.records:
            r.emit(w)
        for b in self.blobs:
            w.align_abs(self.ALIGN)
            b.emit(w)
        # 收尾补到 4 字节：UTMT 读完最后一条数据后还要
        #   while (Position % 4 != 0) ReadByte()   —— 必须全零且在 chunk 内
        w.align_abs(4)

    # ---- 自检 ----
    def verify(self, built) -> None:
        span = built.chunk_span(self.name)
        if span is None:
            raise ModelError("TXTR 没有出现在输出里")
        base, end = span
        last = base
        for i, b in enumerate(self.blobs):
            a = built.addr_of(b)
            if not (base <= a < end):
                raise ModelError(f"TXTR[{i}] 数据 {a:,} 不在 chunk 内")
            if a % self.ALIGN:
                raise ModelError(f"TXTR[{i}] 数据起点 {a:,} 不在 {self.ALIGN} 边界")
            if a < last:
                raise ModelError(f"TXTR[{i}] 数据起点回退（{a:,} < {last:,}）")
            if built.raw[a:a + len(b.data)] != b.data:
                raise ModelError(f"TXTR[{i}] 数据字节不一致")
            if built.u32(built.addr_of(self.records[i]) + self.field) != a:
                raise ModelError(f"TXTR[{i}] 条目里的数据指针没有回填成 {a:,}")
            last = a + len(b.data)
        if last > end:
            raise ModelError(f"TXTR 数据越过 chunk 末尾 {end:,}")


class AudioTable(Chunk):
    """AUDO：内嵌音频。

    规范布局::

        u32 count
        u32 条目起点[count]
        条目[count]            # u32 长度 + 数据

    条目之间补位到 4 字节（UTMT 的 ``PaddedObject``），**最后一条不补**。
    """

    name = "AUDO"

    ALIGN = 4

    def __init__(self, blobs: Sequence[Blob]):
        self.blobs = list(blobs)
        for b in self.blobs:
            b.with_length = True

    @classmethod
    def from_win(cls, w) -> "AudioTable":
        ch = w.chunks.get("AUDO")
        if ch is None or ch.size < 8:
            raise ModelError("没有 AUDO chunk")
        raw = w.raw
        n = w.u32(ch.base)
        if n == 0:
            return cls([])
        if 4 + 4 * n > ch.size:
            raise ModelError(f"AUDO 指针表越界: count={n:,}")
        out = []
        for i in range(n):
            p = w.u32(ch.base + 4 + 4 * i)
            if not (0 < p < len(raw) - 4):
                raise ModelError(f"AUDO[{i}] 指针 {p:,} 不在文件内")
            ln = w.u32(p)
            if p + 4 + ln > len(raw):
                raise ModelError(f"AUDO[{i}] 长度 {ln:,} 越界")
            out.append(Blob(raw[p + 4:p + 4 + ln], with_length=True))
        return cls(out)

    def emit(self, w) -> None:
        w.u32(len(self.blobs))
        for b in self.blobs:
            w.ref(b)
        for i, b in enumerate(self.blobs):
            if i:
                w.align_abs(self.ALIGN)
            b.emit(w)
        # 末尾**不补位**：UTMT 读完最后一条就结束了（最后一条属于 PaddedObject
        # 的例外，不补）。实测补 2 字节后它读到的长度比头部少 2，报补位警告；
        # 原文件 data.win 的 AUDO 长度 35,060,359 本身就是奇数，同样没有补。

    def verify(self, built) -> None:
        span = built.chunk_span(self.name)
        if span is None:
            raise ModelError("AUDO 没有出现在输出里")
        base, end = span
        for i, b in enumerate(self.blobs):
            a = built.addr_of(b)
            if not (base <= a < end):
                raise ModelError(f"AUDO[{i}] 条目 {a:,} 不在 chunk 内")
            if i and a % self.ALIGN:
                raise ModelError(f"AUDO[{i}] 条目起点 {a:,} 不在 {self.ALIGN} 边界")
            if built.u32(a) != len(b.data):
                raise ModelError(f"AUDO[{i}] 写出后长度字段不符")
            if built.raw[a + 4:a + 4 + len(b.data)] != b.data:
                raise ModelError(f"AUDO[{i}] 数据字节不一致")
            if a + 4 + len(b.data) > end:
                raise ModelError(f"AUDO[{i}] 数据越过 chunk 末尾")


class VariableListChunk(Chunk):
    """变长条目的列表型 chunk（SPRT / OBJT / ROOM / CODE / SOND …）。

    条目长度不等，所以不能按「定长步长」切；但指针表给了每条的真实起点，
    相邻指针之差就是这一条的物理长度。条目内容整体保留，只把**发现出来
    的字符串槽位**换成符号引用。

    槽位怎么发现（不是猜）：对每个候选偏移，统计所有条目在这个位置上的
    u32 —— 非零的必须**全部**落在 STRG 的字符串正文地址上（允许 0 表示没有）。
    满足这条的偏移才算槽位。真实布局里 name 这类字段在所有条目上都成立，
    偶然撞上同一个地址的可能性随条目数指数下降。
    """


    def __init__(self, name: str, entries: Sequence[Record],
                 slots: Sequence[int] = (), tail: int = 0):
        self.name = name
        self.entries: List[Record] = list(entries)
        self.slots = list(slots)
        # 块尾没有归属的补位字节（定长记录时才可能非 0）
        self.tail = tail

    @classmethod
    def from_win(cls, w, name: str,
                 strings: Optional["StringTable"] = None,
                 slots: Optional[Sequence[int]] = None,
                 objects: Optional[Dict[str, Dict[int, object]]] = None
                 ) -> "VariableListChunk":
        """读成模型。

        ``objects``：``{chunk 名: {原地址: 模型对象}}``。条目里指向别的 chunk
        的指针（SPRT 的帧 -> TPAG、SCPT 的代码 -> CODE、OBJT 的精灵 -> SPRT）
        就靠它识别并重定位；不传就只认字符串槽位。

        条目长度怎么定
        --------------
        相邻指针之差就是这一条的物理长度 —— 但**最后一条**没有下一个指针，
        只能走到 chunk 末尾，于是块尾那几字节补位会被吞进最后一条（实测
        data2.win 的 BGND 多 4 字节、SCPT 多 12 字节，UTMT 读到的是少 4/12）。
        所以这里先看间距：若所有间距都相等（这类 chunk 的条目本来就是定长的），
        就用这个长度切每一条，块尾多出来的字节记为 ``tail`` 并丢掉；
        间距不等才退回「一条到下一个指针」的变长切法。
        """
        ch = w.chunks.get(name)
        if ch is None or ch.size < 8:
            raise ModelError(f"没有 {name} chunk")
        raw = w.raw
        n = w.u32(ch.base)
        if n == 0:
            return cls(name, [], [])
        if 4 + 4 * n > ch.size:
            raise ModelError(f"{name} 指针表越界: count={n:,}")
        ptrs = [w.u32(ch.base + 4 + 4 * i) for i in range(n)]
        if any(b <= a for a, b in zip(ptrs, ptrs[1:])):
            raise ModelError(f"{name} 的指针不是递增的，切不出条目边界")
        if not (ch.base <= ptrs[0] < ch.end):
            raise ModelError(f"{name}[0] 起点 {ptrs[0]:,} 越出 chunk")

        gaps = [b - a for a, b in zip(ptrs, ptrs[1:])]
        stride = gaps[0] if gaps and len(set(gaps)) == 1 else None
        bodies: List[bytes] = []
        tail = 0
        if stride:
            for i, p in enumerate(ptrs):
                if p + stride > ch.end:
                    raise ModelError(f"{name}[{i}] 区间 {p:,}+{stride} 越出 chunk")
                bodies.append(raw[p:p + stride])
            tail = ch.end - (ptrs[-1] + stride)
            if tail < 0 or tail > 0x100:
                raise ModelError(f"{name} 末尾没有归属的字节 {tail:+,}")
            if any(raw[ch.end - tail:ch.end]):
                raise ModelError(f"{name} 末尾的补位不全是 0")
        else:
            for i, p in enumerate(ptrs):
                end = ptrs[i + 1] if i + 1 < n else ch.end
                if end > ch.end or end < p:
                    raise ModelError(f"{name}[{i}] 区间 {p:,}..{end:,} 不合法")
                bodies.append(raw[p:end])

        table = strings
        if table is None and "STRG" in w.chunks:
            table = StringTable.from_win(w)

        rec_to_str = {}
        if table is not None and "STRG" in w.chunks:
            c = w.chunks["STRG"]
            for i, s in enumerate(table.strings):
                rec = w.u32(c.base + 4 + 4 * i)
                rec_to_str[rec + 4] = s

        found = cls._discover_slots(bodies, rec_to_str, objects or {}) \
            if slots is None else [(off, "str") for off in slots]

        entries = []
        for i, body in enumerate(bodies):
            refs = {}
            for off, kind in found:
                if off + 4 > len(body):
                    continue
                v = struct.unpack_from("<I", body, off)[0]
                if kind == "str":
                    if v in rec_to_str:
                        refs[off] = (rec_to_str[v], "str")
                else:
                    obj = objects[kind].get(v) if objects else None
                    if obj is not None:
                        refs[off] = (obj, "obj")
            entries.append(Record(body, refs, origin=ptrs[i]))
        return cls(name, entries, [off for off, _k in found], tail=tail)

    @staticmethod
    def _discover_slots(bodies: Sequence[bytes],
                        rec_to_str: Dict[int, "GmString"],
                        objects: Dict[str, Dict[int, object]]) -> List[tuple]:
        """在所有条目上一致成立的「指针槽位」：((偏移, 种类), ...)。

        种类 ``str`` 表示指向 STRG 的字符串正文；其余是 chunk 名，表示指向
        那个 chunk 的条目。判据是**全部条目都成立**（0 视为没有该字段）。
        """
        if not bodies:
            return []
        limit = min(64, min(len(b) for b in bodies))
        out: List[tuple] = []
        for off in range(0, limit - 3, 4):
            kinds = (["str"] if rec_to_str else []) + list(objects.keys())
            kinds += list(objects.keys())
            for kind in kinds:
                table = rec_to_str if kind == "str" else objects[kind]
                ok = True
                for b in bodies:
                    v = struct.unpack_from("<I", b, off)[0]
                    if v == 0:
                        continue
                    if v not in table:
                        ok = False
                        break
                if ok:
                    out.append((off, kind))
                    break
        return out

    def emit(self, w) -> None:
        w.u32(len(self.entries))
        for r in self.entries:
            w.ref(r)
        for r in self.entries:
            r.emit(w)

    def verify(self, built) -> None:
        span = built.chunk_span(self.name)
        if span is None:
            raise ModelError(f"{self.name} 没有出现在输出里")
        base, end = span
        want_len = 4 + 4 * len(self.entries) + sum(len(r.data)
                                                   for r in self.entries)
        if end - base != want_len:
            raise ModelError(
                f"{self.name} 写出 {end - base:,} 字节，模型算出 {want_len:,} 字节")
        for i, r in enumerate(self.entries):
            a = built.addr_of(r)
            if not (base <= a < end):
                raise ModelError(f"{self.name}[{i}] 条目 {a:,} 不在 chunk 内")
            for off, (s, kind) in r.refs.items():
                target = built.addr_of(s) + (4 if kind == "str" else 0)
                if built.u32(a + off) != target:
                    raise ModelError(
                        f"{self.name}[{i}] 槽位 {off} 未回填成 {kind} 地址 {target:,}")


class StringListChunk(Chunk):
    """``u32 count + u32 字符串正文[count]``（GLOB）。

    这不是指针表 + 条目，而是「长度 + 一串字符串引用」。
    """


    def __init__(self, name: str, strings: Sequence[GmString]):
        self.name = name
        self.strings: List[GmString] = list(strings)

    @classmethod
    def from_win(cls, w, name: str,
                 table: Optional["StringTable"] = None) -> "StringListChunk":
        ch = w.chunks.get(name)
        if ch is None or ch.size < 4:
            raise ModelError(f"没有 {name} chunk")
        raw = w.raw
        n = w.u32(ch.base)
        if n == 0:
            return cls(name, [])
        if 4 + 4 * n > ch.size:
            raise ModelError(f"{name} 越界: count={n:,}")
        if table is None:
            table = StringTable.from_win(w)
        c = w.chunks["STRG"]
        rec_to_str = {}
        for i, s in enumerate(table.strings):
            rec = w.u32(c.base + 4 + 4 * i)
            rec_to_str[rec + 4] = s
        out = []
        for i in range(n):
            v = w.u32(ch.base + 4 + 4 * i)
            if v not in rec_to_str:
                raise ModelError(f"{name}[{i}] 的 {v:,} 不是字符串正文地址")
            out.append(rec_to_str[v])
        return cls(name, out)

    def emit(self, w) -> None:
        w.u32(len(self.strings))
        for s in self.strings:
            w.string_ref(s)

    def verify(self, built) -> None:
        span = built.chunk_span(self.name)
        if span is None:
            raise ModelError(f"{self.name} 没有出现在输出里")
        base, end = span
        for i, s in enumerate(self.strings):
            want = built.addr_of(s) + 4
            got = built.u32(base + 4 + 4 * i)
            if got != want:
                raise ModelError(f"{self.name}[{i}] 写出 {got:,} != 正文 {want:,}")


# ══════════════════════════════════════════════════════════════════
#  FUNC / VARI：内联定长列表
# ══════════════════════════════════════════════════════════════════
#
# 这两个不是「指针表 + 条目」，而是 u32 数量之后直接排记录：
#
#   FUNC  FUNC(12B)：Name, Occurrences, FirstAddress      （= -1 表示没有）
#         CodeLocals：u32 局部变量数, Name, 每个 (Index, Name)
#   VARI  VARI(20B)：Name, InstanceType(i32), VarID(i32),
#                    Occurrences, FirstAddress
#
# `FirstAddress` 指进 CODE 条目的 bytecode 中间 —— 所以它必须记成
# 「CODE 记录 + 组内偏移」，CODE 搬家后按新地址回填。


def _code_span_lookup(chunk_obj):
    """给 CODE 整块建「地址 -> (CODE 块对象, 块内偏移)」查询。

    用整块而不是逐条目，是因为 CODE 里存在**没有条目指向的字节**
    （data.win 的 6,272 条代码都挤在末尾 125 KB，前面 5 MB 没有任何指针
    表项指进去，而 FUNC/VARI 的 FirstAddress 正落在那一带）。按整块算
    偏移，搬家后这些地址才能跟着走。
    """
    base = chunk_obj.base
    end = base + len(chunk_obj.data)

    def find(addr: int):
        if base <= addr < end:
            return chunk_obj, addr - base
        return None

    return find


class RebasedListChunk(Chunk):
    """整块原样搬运，只把开头的指针表按新起点重算。

    为什么需要它：CODE 这类 chunk 内部有**没有条目指向的字节**（data.win 的
    CODE 里，6,272 条代码条目都挤在末尾 125 KB，前面 5 MB 没有任何指针表项
    指进去，而 FUNC/VARI 的 FirstAddress 恰恰指在那片区域里）。把条目重排会
    改变这些内部偏移，所以这一块必须**逐字节保持相对布局**，只重定位它的起点。

    做法：数出 `u32 count` 与指针表，指针表里每一项写成
    「新数据起点 + (原值 - 原数据起点)」。原地什么都不动。
    """


    def __init__(self, name: str, data: bytes, base: int, count: int,
                 names: Optional[Dict[int, "GmString"]] = None):
        self.name = name
        self.data = bytes(data)
        self.base = base                # 原数据起点
        self.count = count
        self.names = dict(names or {})   # 表下标 -> 条目 +0 的名字字符串

    @classmethod
    def from_win(cls, w, name: str,
                 table: Optional["StringTable"] = None) -> "RebasedListChunk":
        ch = w.chunks.get(name)
        if ch is None or ch.size < 8:
            raise ModelError(f"没有 {name} chunk")
        n = w.u32(ch.base)
        if n == 0 or 4 + 4 * n > ch.size:
            raise ModelError(f"{name} 指针表越界: count={n:,}")
        names: Dict[int, GmString] = {}
        if table is not None:
            text_at = _string_content_map(w, table)
            for i in range(n):
                p = w.u32(ch.base + 4 + 4 * i)
                if ch.base <= p < ch.end:
                    s = text_at(w.u32(p))
                    if s is not None:
                        names[i] = s
        return cls(name, w.raw[ch.base:ch.end], ch.base, n, names)

    def emit(self, w) -> None:
        if w.dry:
            # 量测阶段只需要长度：里面引用的字符串地址这还没算出来
            w.raw(self.data)
            return
        # 先算出这一块写出去以后的数据起点（begin 已经登记过）
        here = w.tell()
        data = bytearray(self.data)
        for i in range(self.count):
            old = struct.unpack_from("<I", data, 4 + 4 * i)[0]
            if not (self.base <= old < self.base + len(self.data)):
                raise ModelError(f"{self.name} 指针[{i}] {old:,} 不在块内")
            off = old - self.base
            struct.pack_into("<I", data, 4 + 4 * i, here + off)
            s = self.names.get(i)
            if s is not None:
                struct.pack_into("<I", data, off, w._addr(s) + 4)
        w.raw(bytes(data))

    def verify(self, built) -> None:
        span = built.chunk_span(self.name)
        if span is None:
            raise ModelError(f"{self.name} 没有出现在输出里")
        base, end = span
        for i in range(self.count):
            v = built.u32(base + 4 + 4 * i)
            if not (base <= v < end):
                raise ModelError(f"{self.name} 指针[{i}] {v:,} 落在 chunk 之外")


class FunctionEntry:
    """FUNC 里的一条函数。"""

    __slots__ = ("name", "occurrences", "first")

    def __init__(self, name: Optional[GmString], occurrences: int,
                 first: Optional[Tuple[Record, int]]):
        self.name = name
        self.occurrences = occurrences
        self.first = first                 # (CODE 记录, 组内偏移)


class CodeLocalsEntry:
    """CodeLocals 里的一条：名字 + 若干局部变量。"""

    __slots__ = ("name", "locals")

    def __init__(self, name: Optional[GmString], locals_: Sequence[tuple]):
        self.name = name
        self.locals = list(locals_)        # [(index, GmString|None), ...]


class FunctionTable(Chunk):
    """FUNC：函数列表 + 代码局部变量列表。"""

    name = "FUNC"

    def __init__(self, functions: Sequence[FunctionEntry],
                 locals_: Sequence[CodeLocalsEntry]):
        self.functions = list(functions)
        self.locals = list(locals_)

    @classmethod
    def from_win(cls, w, table: Optional["StringTable"] = None,
                 code: Optional[Chunk] = None) -> "FunctionTable":
        ch = w.chunks.get("FUNC")
        if ch is None or ch.size < 8:
            raise ModelError("没有 FUNC chunk")
        raw = w.raw
        if table is None:
            table = StringTable.from_win(w)
        text_at = _string_content_map(w, table)
        find_code = _code_span_lookup(code) if code is not None else (lambda a: None)

        pos = ch.base
        n = w.u32(pos)
        pos += 4
        if pos + 12 * n > ch.end:
            raise ModelError(f"FUNC 函数列表越界: count={n:,}")
        funcs = []
        for i in range(n):
            name_ptr = w.u32(pos)
            occ = w.u32(pos + 4)
            first = w.u32(pos + 8)
            hit = find_code(first) if occ and first not in (0, 0xFFFFFFFF) else None
            if occ and first not in (0, 0xFFFFFFFF) and hit is None:
                raise ModelError(f"FUNC[{i}] 的 FirstAddress {first:,} "
                                 f"不在 CODE chunk 内")
            funcs.append(FunctionEntry(text_at(name_ptr), occ, hit))
            pos += 12

        m = w.u32(pos)
        pos += 4
        locals_ = []
        for i in range(m):
            cnt = w.u32(pos)
            name_ptr = w.u32(pos + 4)
            pos += 8
            if pos + 8 * cnt > ch.end:
                raise ModelError(f"FUNC 第 {i} 条 CodeLocals 越界")
            vars_ = []
            for _ in range(cnt):
                idx = w.u32(pos)
                vars_.append((idx, text_at(w.u32(pos + 4))))
                pos += 8
            locals_.append(CodeLocalsEntry(text_at(name_ptr), vars_))
        return cls(funcs, locals_)

    def emit(self, w) -> None:
        w.u32(len(self.functions))
        for f in self.functions:
            if f.name is not None:
                w.string_ref(f.name)
            else:
                w.u32(0)
            w.u32(f.occurrences)
            if f.occurrences and f.first is not None:
                rec, delta = f.first
                w.ref_at(rec, delta)
            else:
                w.u32(0xFFFFFFFF)
        w.u32(len(self.locals))
        for ent in self.locals:
            w.u32(len(ent.locals))
            if ent.name is not None:
                w.string_ref(ent.name)
            else:
                w.u32(0)
            for idx, s in ent.locals:
                w.u32(idx)
                if s is not None:
                    w.string_ref(s)
                else:
                    w.u32(0)

    def verify(self, built) -> None:
        span = built.chunk_span(self.name)
        if span is None:
            raise ModelError("FUNC 没有出现在输出里")
        base, end = span
        pos = base
        n = built.u32(pos)
        pos += 4
        for i, f in enumerate(self.functions):
            if f.name is not None and built.u32(pos) != built.addr_of(f.name) + 4:
                raise ModelError(f"FUNC[{i}] 名字未回填")
            if f.occurrences and f.first is not None:
                rec, delta = f.first
                if built.u32(pos + 8) != built.addr_of(rec) + delta:
                    raise ModelError(f"FUNC[{i}] FirstAddress 未回填")
            pos += 12
        if not (base <= pos <= end):
            raise ModelError("FUNC 写出越界")


class VariableTable(Chunk):
    """VARI：变量列表。"""

    name = "VARI"

    def __init__(self, variables: Sequence[dict], header: Sequence[int] = (0, 0, 0)):
        self.variables = list(variables)
        self.header = list(header)
        # 每条: name, instance_type, var_id, occurrences, first

    @classmethod
    def from_win(cls, w, table: Optional["StringTable"] = None,
                 code: Optional[Chunk] = None) -> "VariableTable":
        ch = w.chunks.get("VARI")
        if ch is None or ch.size < 8:
            raise ModelError("没有 VARI chunk")
        if table is None:
            table = StringTable.from_win(w)
        text_at = _string_content_map(w, table)
        find_code = _code_span_lookup(code) if code is not None else (lambda a: None)

        # 头部三个 u32：VarCount1, VarCount2, MaxLocalVarCount；条目从 +12 起，
        # 条数由长度反推（UTMT 就是这么算的，不用 VarCount1）
        self_header = struct.unpack_from("<3I", w.raw, ch.base)
        n = (ch.size - 12) // 20
        pos = ch.base + 12
        if n and pos + 20 * n > ch.end:
            raise ModelError(f"VARI 列表越界: count={n:,}")
        out = []
        for i in range(n):
            name = text_at(w.u32(pos))
            itype = struct.unpack_from("<i", w.raw, pos + 4)[0]
            vid = struct.unpack_from("<i", w.raw, pos + 8)[0]
            occ = w.u32(pos + 12)
            first = w.u32(pos + 16)
            hit = find_code(first) if occ and first not in (0, 0xFFFFFFFF) else None
            if occ and first not in (0, 0xFFFFFFFF) and hit is None:
                raise ModelError(f"VARI[{i}] 的 FirstAddress {first:,} "
                                 f"不在 CODE chunk 内")
            out.append({"name": name, "instance_type": itype, "var_id": vid,
                        "occurrences": occ, "first": hit})
            pos += 20
        return cls(out, header=self_header)

    def emit(self, w) -> None:
        # 头部沿用解析到的三个计数（VarCount1 / VarCount2 / MaxLocalVarCount）
        for v in self.header:
            w.u32(v)
        for v in self.variables:
            if v["name"] is not None:
                w.string_ref(v["name"])
            else:
                w.u32(0)
            w.u32(v["instance_type"] & 0xFFFFFFFF)
            w.u32(v["var_id"] & 0xFFFFFFFF)
            w.u32(v["occurrences"])
            if v["occurrences"] and v["first"] is not None:
                rec, delta = v["first"]
                w.ref_at(rec, delta)
            else:
                w.u32(0xFFFFFFFF)

    def verify(self, built) -> None:
        span = built.chunk_span(self.name)
        if span is None:
            raise ModelError("VARI 没有出现在输出里")
        base, _end = span
        pos = base + 12
        for i, v in enumerate(self.variables):
            if v["name"] is not None and built.u32(pos) != built.addr_of(v["name"]) + 4:
                raise ModelError(f"VARI[{i}] 名字未回填")
            if v["occurrences"] and v["first"] is not None:
                rec, delta = v["first"]
                if built.u32(pos + 16) != built.addr_of(rec) + delta:
                    raise ModelError(f"VARI[{i}] FirstAddress 未回填")
            pos += 20


def _string_at_content(w, value: int) -> Optional[GmString]:
    """把一个字符串正文地址读成 GmString（不查表）。不像就返回 None。"""
    rec = value - 4
    raw = w.raw
    if not (0 < rec < len(raw) - 5):
        return None
    ln = struct.unpack_from("<I", raw, rec)[0]
    if ln == 0 or ln > 0xFFFF or rec + 4 + ln + 1 > len(raw):
        return None
    body = raw[rec + 4:rec + 4 + ln]
    if not body or b"\x00" in body or raw[rec + 4 + ln] != 0:
        return None
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text.isprintable():        # 控制字符说明这不是字符串，是撞上的数字
        return None
    return GmString(body)


def _string_content_map(w, table: Optional["StringTable"]):
    """正文地址 -> GmString 的查询函数。"""
    m: Dict[int, GmString] = {}
    if table is not None and "STRG" in w.chunks:
        c = w.chunks["STRG"]
        for i, s in enumerate(table.strings):
            rec = w.u32(c.base + 4 + 4 * i)
            m[rec + 4] = s

    def look(v: int) -> Optional[GmString]:
        return m.get(v)

    return look


class SpriteItem:
    """SPRT 的一个条目。

    字节布局（UTMT ``UndertaleSprite``，``+0..+56`` 各版本一致）::

        +0   Name (str ptr)
        +4   Width, Height                (u32 ×2)
        +12  MarginLeft/Right/Bottom/Top  (i32 ×4)
        +28  Transparent, Smooth, Preload (bool，各占 4 字节)
        +40  BBoxMode, SepMasks           (u32 ×2)
        +48  OriginX, OriginY             (i32 ×2)
        +56  ↓ 两种走法

    **普通走法**（GMS 1.x）::

        +56  帧数 N
        +60  N × TPAG 指针
              掩码数 M，M 段位图（每段 ((宽+7)//8)*高 字节），再补到 4

    **特殊走法**（``+56 == 0xFFFFFFFF``，GMS2 全是这种）::

        +56  0xFFFFFFFF  哨兵
        +60  SVersion    +64  SSpriteType
        +68  GMS2 播放速度 (f32) + 类型 (u32)     # 仅 GMS2
        +76  SVersion>=2 时跟序列指针、>=3 再跟九宫格指针
        +76  之后同普通走法的「帧数 + 帧指针 + 掩码」

    这里把「哨兵到帧数之间」的那一段原样存进 ``special``（data2.win 全是
    SVersion=1，也就是 20 字节，不含指针）；SVersion>=2 带的是指针，
    搬字节会留下旧地址，所以直接拒绝，等真碰到再建模。

    掩码位图的长度是**算出来的**（``((宽+7)//8)*高``），文件里没有长度前缀，
    所以这里原样保存，不解释内容。
    """

    __slots__ = ("name", "header", "special", "frames", "mask_count", "masks")

    def __init__(self, name: Optional[GmString], header: bytes,
                 special: Optional[bytes], frames: Sequence[Record],
                 mask_count: int, masks: bytes):
        self.name = name
        self.header = bytes(header)      # +4..+56
        self.special = None if special is None else bytes(special)
        self.frames = list(frames)
        self.mask_count = mask_count
        self.masks = bytes(masks)

    @property
    def frames_at(self) -> int:
        """帧数在条目内的偏移（普通 56，特殊则要跳过 special 段）。"""
        return 56 + (len(self.special) if self.special is not None else 0)

    def emit(self, w) -> None:
        w.begin(self)
        if self.name is not None:
            w.string_ref(self.name)
        else:
            w.u32(0)
        w.raw(self.header)
        if self.special is not None:
            w.raw(self.special)
        w.u32(len(self.frames))
        for f in self.frames:
            w.ref(f)
        w.u32(self.mask_count)
        if self.masks:
            w.raw(self.masks)
        w.align_abs(4)


class SpriteTable(Chunk):
    """SPRT：精灵。帧指向 TPAG 条目，掩码按宽高算长度。"""

    name = "SPRT"

    def __init__(self, items: Sequence[SpriteItem], gms2: bool = False,
                 tail: int = 0):
        self.items = list(items)
        self.gms2 = gms2        # 特殊类型里有没有那 8 字节播放速度
        self.tail = tail        # 原 chunk 末尾没有归属的补位字节数

    # ---- 读 ----
    @classmethod
    def from_win(cls, w, table: Optional["StringTable"] = None,
                 tpag: Optional["OpaqueListChunk"] = None) -> "SpriteTable":
        ch = w.chunks.get("SPRT")
        if ch is None or ch.size < 8:
            raise ModelError("没有 SPRT chunk")
        n = w.u32(ch.base)
        if n == 0:
            return cls([])
        if 4 + 4 * n > ch.size:
            raise ModelError(f"SPRT 指针表越界: count={n:,}")
        ptrs = [w.u32(ch.base + 4 + 4 * i) for i in range(n)]
        for i, p in enumerate(ptrs):
            if not (ch.base <= p and p + 60 <= ch.end):
                raise ModelError(f"SPRT[{i}] 条目 {p:,} 越出 chunk")
        if table is None:
            table = StringTable.from_win(w)
        text_at = _string_content_map(w, table)
        tpag_by_addr = {r.origin: r for r in tpag.entries} if tpag else {}

        special = sum(1 for p in ptrs if w.u32(p + 56) == SPRITE_SPECIAL)
        # 有没有那 8 字节播放速度，靠「铺满整块」来定：两种走法各走一遍，
        # 必须恰好一种能把 chunk 铺满，否则不做猜测
        tries = (False, True) if special else (False,)
        good = []
        for gms2 in tries:
            got = _sprite_walk(w, ch, ptrs, text_at, tpag_by_addr, gms2)
            if got is not None:
                good.append((gms2, got))
        if not good:
            raise ModelError(
                f"SPRT 两种走法都铺不满（{special:,} 个特殊条目）："
                "帧指针、掩码长度或播放速度字段对不上")
        if len(good) > 1:
            raise ModelError("SPRT 两种走法都能铺满，判不出布局，不做猜测")
        gms2, (items, tail) = good[0]
        return cls(items, gms2=gms2, tail=tail)

    def emit(self, w) -> None:
        w.u32(len(self.items))
        for it in self.items:
            w.ref(it)
        for it in self.items:
            it.emit(w)

    # ---- 自检 ----
    def verify(self, built) -> None:
        span = built.chunk_span(self.name)
        if span is None:
            raise ModelError("SPRT 没有出现在输出里")
        base, end = span
        for i, it in enumerate(self.items):
            a = built.addr_of(it)
            if not (base <= a < end):
                raise ModelError(f"SPRT[{i}] 条目 {a:,} 不在 chunk 内")
            if it.name is not None and built.u32(a) != built.addr_of(it.name) + 4:
                raise ModelError(f"SPRT[{i}] 名字未回填")
            if it.special is not None and \
                    built.raw[a + 56:a + 56 + len(it.special)] != it.special:
                raise ModelError(f"SPRT[{i}] 特殊类型头部字节不一致")
            fp = a + it.frames_at
            if built.u32(fp) != len(it.frames):
                raise ModelError(f"SPRT[{i}] 帧数未写出")
            for k, f in enumerate(it.frames):
                if built.u32(fp + 4 + 4 * k) != built.addr_of(f):
                    raise ModelError(f"SPRT[{i}] 帧[{k}] 未回填")
            mp = fp + 4 + 4 * len(it.frames)
            if built.u32(mp) != it.mask_count:
                raise ModelError(f"SPRT[{i}] 掩码数未写出")
            if built.raw[mp + 4:mp + 4 + len(it.masks)] != it.masks:
                raise ModelError(f"SPRT[{i}] 掩码字节不一致")


SPRITE_SPECIAL = 0xFFFFFFFF     # +56 是这个值就是特殊类型（GMS2）
SPRITE_MAX_FRAMES = 4096
SPRITE_MAX_MASKS = 64
SPRITE_TAIL_MAX = 0x100         # 末尾允许的无主补位字节上限


def _sprite_walk(w, ch, ptrs, text_at, tpag_by_addr,
                 gms2: bool):
    """按一种假设把整块 SPRT 走一遍；走不通（越界/指针不对/铺不满）返回 None。

    返回 ``(items, tail)``。判据是「首尾相接铺满」：条目一条接一条，
    最后允许有 ``SPRITE_TAIL_MAX`` 以内的全零补位。
    """
    raw = w.raw
    items: List[SpriteItem] = []
    pieces: List[Tuple[int, int, str]] = [(0, 4 + 4 * len(ptrs), "指针表")]
    for i, p in enumerate(ptrs):
        pos = p + 56
        special = None
        if w.u32(pos) == SPRITE_SPECIAL:
            sver, stype = w.u32(pos + 4), w.u32(pos + 8)
            # SWF / Spine / Vector 各有自己的形状，而且 SVersion>=2 带的是指针，
            # 原样搬会留下旧地址 —— 没建模就明确拒绝，不搬字节
            if stype != 0:
                raise ModelError(f"SPRT[{i}] 是 SWF/Spine/Vector 特殊类型"
                                 f"（SSpriteType={stype}），暂不支持")
            if sver >= 2:
                raise ModelError(f"SPRT[{i}] SVersion={sver}，带序列/九宫格指针，"
                                 "暂不支持")
            end = pos + 12
            if gms2:
                end += 8
            if end > ch.end:
                return None
            special = raw[pos:end]
            pos = end
        frames_n = w.u32(pos) if pos + 4 <= ch.end else -1
        if not (0 <= frames_n <= SPRITE_MAX_FRAMES):
            return None
        pos += 4
        if pos + 4 * frames_n > ch.end:
            return None
        frames = []
        for k in range(frames_n):
            rec = tpag_by_addr.get(w.u32(pos + 4 * k))
            if rec is None:
                return None
            frames.append(rec)
        pos += 4 * frames_n
        if pos + 4 > ch.end:
            return None
        mask_n = w.u32(pos)
        pos += 4
        if mask_n > SPRITE_MAX_MASKS:
            return None
        one = ((w.u32(p + 4) + 7) // 8) * w.u32(p + 8)
        size = one * mask_n
        if pos + size > ch.end:
            return None
        masks = raw[pos:pos + size]
        pos += size
        pos += (-pos) % 4
        items.append(SpriteItem(name=text_at(w.u32(p)),
                                header=raw[p + 4:p + 56], special=special,
                                frames=frames, mask_count=mask_n, masks=masks))
        pieces.append((p - ch.base, pos - ch.base, f"SPRT[{i}]"))

    last = max(e for _a, e, _l in pieces)
    tail = ch.size - last
    if tail < 0 or tail > SPRITE_TAIL_MAX:
        return None
    if any(raw[ch.base + last:ch.end]):
        return None
    if tail:
        pieces.append((last, ch.size, "末尾补位"))
    _check_tiling(pieces, ch.size, "SPRT")
    return items, tail


# ══════════════════════════════════════════════════════════════════
#  OBJT：物件（条目里套着三层指针表）
# ══════════════════════════════════════════════════════════════════
#
# 条目布局（GMS 1.x / bytecode 16，UTMT ``UndertaleGameObject``）：
#
#   +0   Name (str ptr)      +4   Sprite 索引
#   +8   Visible/Solid ...   +64  物理顶点数 V     +80  V × 顶点(8B)
#   之后是 Events：指针表套指针表
#        Events       = u32 12 + 12 个指针
#        每种事件      = u32 n + n 个指针 -> Event
#        Event        = u32 Subtype + Actions 指针表
#        Actions      = u32 m + m 个指针 -> EventAction
#        EventAction  = 56 字节，+28 是 ActionName（字符串正文），+32 是代码索引
#
# 索引（Sprite/Parent/Mask/CodeId）是**下标**，不是地址，所以不用重定位；
# 真正要重算的只有：名字、以及每一层指针表本身。


class PtrList:
    """``u32 数量 + u32 指针[数量] + 子项``。子项可以是 PtrList 或 EventAction。"""

    __slots__ = ("items", "addr")

    def __init__(self, items: Sequence[object]):
        self.items = list(items)
        self.addr = -1

    def emit(self, w) -> None:
        w.begin(self)
        w.u32(len(self.items))
        for it in self.items:
            w.ref(it)
        for it in self.items:
            it.emit(w)

    def walk(self):
        """深度优先遍历所有子项（自检用）。"""
        for it in self.items:
            yield it
            if isinstance(it, PtrList):
                yield from it.walk()
            elif isinstance(it, Event):
                yield it.actions
                yield from it.actions.walk()


class EventAction:
    """事件动作：56 字节定长，只有 ActionName 是地址。"""

    __slots__ = ("data", "name", "addr")

    SIZE = 56
    NAME_OFF = 28

    def __init__(self, data: bytes, name: Optional[GmString]):
        self.data = bytes(data)
        self.name = name
        self.addr = -1

    def emit(self, w) -> None:
        w.begin(self)
        w.raw(self.data[:self.NAME_OFF])
        if self.name is not None:
            w.string_ref(self.name)
        else:
            w.u32(0)
        w.raw(self.data[self.NAME_OFF + 4:])


class Event:
    """一条事件：子类型 + 动作表。"""

    __slots__ = ("subtype", "actions", "addr")

    def __init__(self, subtype: int, actions: PtrList):
        self.subtype = subtype
        self.actions = actions
        self.addr = -1

    def emit(self, w) -> None:
        w.begin(self)
        w.u32(self.subtype)
        self.actions.emit(w)


class ObjectItem:
    """OBJT 的一个条目。"""

    __slots__ = ("name", "header", "vertices", "events", "addr")

    HEADER = 80
    VERTEX = 8

    def __init__(self, name: Optional[GmString], header: bytes,
                 vertices: bytes, events: PtrList):
        self.name = name
        self.header = bytes(header)      # +4..+80
        self.vertices = bytes(vertices)
        self.events = events
        self.addr = -1

    def emit(self, w) -> None:
        w.begin(self)
        if self.name is not None:
            w.string_ref(self.name)
        else:
            w.u32(0)
        w.raw(self.header)
        w.raw(self.vertices)
        self.events.emit(w)


class ObjectTable(Chunk):
    """OBJT：物件表。"""

    name = "OBJT"

    def __init__(self, items: Sequence[ObjectItem]):
        self.items = list(items)

    # ---- 读 ----
    @classmethod
    def from_win(cls, w, table: Optional["StringTable"] = None) -> "ObjectTable":
        ch = w.chunks.get("OBJT")
        if ch is None or ch.size < 8:
            raise ModelError("没有 OBJT chunk")
        raw = w.raw
        n = w.u32(ch.base)
        if n == 0:
            return cls([])
        if 4 + 4 * n > ch.size:
            raise ModelError(f"OBJT 指针表越界: count={n:,}")
        if table is None:
            table = StringTable.from_win(w)
        text_at = _string_content_map(w, table)

        ptrs = [w.u32(ch.base + 4 + 4 * i) for i in range(n)]

        def read_list(pos: int, depth: int) -> PtrList:
            """depth: 2 = 12 个事件类型表, 1 = Event, 0 = EventAction。"""
            if not (ch.base <= pos < ch.end) or pos + 4 > ch.end:
                raise ModelError(f"OBJT 指针表 {pos:,} 越出 chunk")
            cnt = w.u32(pos)
            if cnt > 4096:
                raise ModelError(f"OBJT 指针表数量异常: {cnt:,} @ {pos:,}")
            items: List[object] = []
            q = pos + 4
            addrs = []
            for _ in range(cnt):
                if q + 4 > ch.end:
                    raise ModelError("OBJT 指针表越界")
                addrs.append(w.u32(q))
                q += 4
            for a in addrs:
                if not (ch.base <= a < ch.end):
                    raise ModelError(f"OBJT 子项 {a:,} 越出 chunk")
                if depth == 0:
                    if a + EventAction.SIZE > ch.end:
                        raise ModelError(f"OBJT 动作 {a:,} 越界")
                    items.append(EventAction(
                        raw[a:a + EventAction.SIZE],
                        text_at(w.u32(a + EventAction.NAME_OFF))))
                elif depth == 1:
                    if a + 4 > ch.end:
                        raise ModelError(f"OBJT 事件 {a:,} 越界")
                    items.append(Event(w.u32(a), read_list(a + 4, 0)))
                else:
                    items.append(read_list(a, 1))
            return PtrList(items)

        items = []
        for i, p in enumerate(ptrs):
            if not (ch.base <= p and p + ObjectItem.HEADER <= ch.end):
                raise ModelError(f"OBJT[{i}] 条目 {p:,} 越出 chunk")
            name = text_at(w.u32(p))
            header = raw[p + 4:p + ObjectItem.HEADER]
            vcount = w.u32(p + 64)
            pos = p + ObjectItem.HEADER
            vsize = vcount * ObjectItem.VERTEX
            if vcount > 4096 or pos + vsize > ch.end:
                raise ModelError(f"OBJT[{i}] 物理顶点数异常: {vcount:,}")
            items.append(ObjectItem(name, header,
                                    raw[pos:pos + vsize],
                                    read_list(pos + vsize, 2)))
        return cls(items)

    # ---- 写 ----
    def emit(self, w) -> None:
        w.u32(len(self.items))
        for it in self.items:
            w.ref(it)
        for it in self.items:
            it.emit(w)

    # ---- 自检 ----
    def verify(self, built) -> None:
        span = built.chunk_span(self.name)
        if span is None:
            raise ModelError("OBJT 没有出现在输出里")
        base, end = span
        for i, it in enumerate(self.items):
            a = built.addr_of(it)
            if not (base <= a < end):
                raise ModelError(f"OBJT[{i}] 条目 {a:,} 不在 chunk 内")
            if it.name is not None and built.u32(a) != built.addr_of(it.name) + 4:
                raise ModelError(f"OBJT[{i}] 名字未回填")
            for node in it.events.walk():
                na = built.addr_of(node)
                if not (base <= na < end):
                    raise ModelError(f"OBJT[{i}] 子结构 {na:,} 不在 chunk 内")
                if isinstance(node, EventAction) and node.name is not None:
                    if built.u32(na + EventAction.NAME_OFF) != \
                            built.addr_of(node.name) + 4:
                        raise ModelError(f"OBJT[{i}] 动作名未回填")


# ══════════════════════════════════════════════════════════════════
#  ROOM：房间
# ══════════════════════════════════════════════════════════════════
#
# 条目布局（GMS 1.x / bytecode 16，UTMT ``UndertaleRoom``）：
#
#   +0  Name (str)   +4  Caption (str)   +8  Width   +12 Height   +16 Speed
#   +20 Persistent   +24 BackgroundColor +28 DrawBackgroundColor
#   +32 CreationCodeId（索引）  +36 Flags
#   +40 Backgrounds 指针  +44 Views 指针  +48 GameObjects 指针  +52 Tiles 指针
#   +56 World  +60 Top/Left/Right/Bottom  +76 GravityX/Y/MetersPerPixel
#
# 四张表都是「u32 数量 + u32 指针」的形态，表项本身只有数字和**索引**
# （背景/精灵/物件/代码），没有绝对地址 —— 所以表项原样保留即可，要重算的
# 只有：条目的名字、以及四张指针表本身。
#
# 表项长度不是猜的
# ----------------
# 表项里既然没有偏移，唯一的自由度就是「一条表项占多少字节」。这里不用常量
# 表去假设，而是拿**两路证据**互证，任何一路说不通就抛：
#
# 1. 指针数组里相邻表项的地址差（实测）：同一张表的表项在文件里是紧挨着的，
#    间距的众数就是表项长度；
# 2. UTMT 的版本判据：``UndertaleRoom.GameObject`` 只在
#    ``BytecodeVersion >= 16`` 时多读一个 PreCreateCode（索引），所以物件表
#    有两种长度，其余三张表各版本一致。
#
# 实测（三个样本）：data.win bytecode 16 → 物件 40；data1.win bytecode 15 →
# 物件 36；data2.win 16 → 40。两路证据全部一致，且下面这条恒等式成立：
# ROOM chunk 长度 == 指针表 + 条目头 + 四张表 + 表项（差 0 字节）。
# 差一个字节就说明表项长度判错了，``_check_layout`` 会直接抛，不会写出错文件。

ROOM_HEADER = 88                # GMS1：名字/caption(8) + 头(80)
ROOM_HEADER_GMS2 = 92           # GMS2：头里多一个 Layers 指针（+88）
ROOM_LAYERS_SLOT = 80           # Layers 指针在「+8 之后那一段」里的偏移
ROOM_HEADER_SLOT = 8            # 条目指针里「名字+caption」占的 8 字节
ROOM_LISTS = (("Backgrounds", 40), ("Views", 44),
              ("GameObjects", 48), ("Tiles", 52))

# 一张表里允许出现的表项长度（UTMT 已定义的布局，别无其它）
ROOM_ITEM_ALLOWED = {"Backgrounds": (40,), "Views": (56,),
                     "GameObjects": (36, 40), "Tiles": (48,)}
ROOM_ITEM_OBJECT_OLD = 36        # bytecode < 16：没有 PreCreateCode
ROOM_ITEM_OBJECT_NEW = 40        # bytecode >= 16：X,Y,Object,ID,Code,SX,SY,Color,Rot,PreCode
ROOM_FLAG_GMS2 = 0x20000         # UndertaleRoom.RoomEntryFlags.IsGMS2
ROOM_MAX_ITEMS = 100000

# GMS2 图层（UTMT ``UndertaleRoom.Layer``）
ROOM_LAYER_HEADER = 36           # 名字(4) + id(4) + 类型(4) + 深度(4) + 偏移/速度(16) + 可见(4)
ROOM_LAYER_BACKGROUND = 40       # 背景图层：一条定长记录（内部只有索引）
ROOM_TILE = 48                   # Assets 里的图块表项
ROOM_SPRITE_INSTANCE = 44        # Assets 里的精灵实例表项（+0 是名字指针）
ROOM_MAX_LAYERS = 4096
ROOM_TAIL_MAX = 0x100            # 末尾允许的无主补位上限（实测 data2 是 12 字节）


def _bytecode_version(w) -> Optional[int]:
    """GEN8 头里的 bytecode 版本。

    GEN8 开头是 ``IsDebuggerDisabled(bool, 1 字节) + BytecodeVersion(u8)``，
    所以版本在 ``gen8_off + 1``（实测 data.win=16、data1.win=15、data2.win=16，
    与「物件表 40 / 36 / 40」完全吻合）。读不到或不合理时返回 None，
    表示「版本未知」，此时只靠实测间距判断。
    """
    try:
        off = w.gen8_off
    except AttributeError:
        return None
    if not (0 <= off < len(w.raw) - 2):
        return None
    ver = w.raw[off + 1]
    return ver if 8 <= ver <= 30 else None


def _room_expected_size(key: str, bytecode: Optional[int]) -> Optional[int]:
    if key != "GameObjects":
        return ROOM_ITEM_ALLOWED[key][0]
    if bytecode is None:
        return None
    return ROOM_ITEM_OBJECT_NEW if bytecode >= 16 else ROOM_ITEM_OBJECT_OLD


def _room_item_size(w, ch, ptrs, key: str, off: int,
                    bytecode: Optional[int]) -> int:
    """一张表的表项长度：实测间距 与 bytecode 版本 两路互证。"""
    want = _room_expected_size(key, bytecode)
    addrs: List[int] = []
    for p in ptrs:
        lp = w.u32(p + off)
        if lp == 0:
            continue
        if not (ch.base <= lp and lp + 4 <= ch.end):
            raise ModelError(f"ROOM.{key} 表 {lp:,} 越出 chunk")
        cnt = w.u32(lp)
        if cnt > ROOM_MAX_ITEMS or lp + 4 + 4 * cnt > ch.end:
            raise ModelError(f"ROOM.{key} 表数量异常: {cnt:,} @ {lp:,}")
        addrs.extend(w.u32(lp + 4 + 4 * j) for j in range(cnt))
    if len(addrs) < 2:
        if want is None:
            raise ModelError(f"ROOM.{key} 表项不足两条，且版本未知，判不出长度")
        return want
    seen = sorted(set(addrs))
    counts: Dict[int, int] = {}
    for a, b in zip(seen, seen[1:]):
        if b > a:
            counts[b - a] = counts.get(b - a, 0) + 1
    got = max(counts.items(), key=lambda kv: kv[1])[0]
    if got not in ROOM_ITEM_ALLOWED[key]:
        raise ModelError(
            f"ROOM.{key} 表项 {got} 字节，不在已知布局 "
            f"{ROOM_ITEM_ALLOWED[key]} 里")
    if want is not None and got != want:
        raise ModelError(
            f"ROOM.{key} 表项实测 {got} 字节，bytecode {bytecode} 对应 "
            f"{want} 字节（两路证据不一致，不做猜测）")
    return got


def _read_layer(w, ch, L: int, text_at, sites, pieces,
                room_i: int, layer_j: int) -> "LayerItem":
    """读一个 GMS2 图层，同时登记它的指针槽位与字节区间。"""
    raw = w.raw
    tag = f"Room[{room_i}].layer[{layer_j}]"
    if not (ch.base <= L and L + ROOM_LAYER_HEADER <= ch.end):
        raise ModelError(f"{tag} {L:,} 越出 chunk")
    kind = w.u32(L + 8)
    name = text_at(w.u32(L))
    if name is not None:
        sites.append((L - ch.base, name))     # 图层名槽位（在 +0）
    header = raw[L + 4:L + ROOM_LAYER_HEADER]
    pos = L + ROOM_LAYER_HEADER
    payload = b""
    fills = None
    if kind == 0:                               # Path：没有数据
        pass
    elif kind == 1:                             # Background：40 字节定长
        if pos + ROOM_LAYER_BACKGROUND > ch.end:
            raise ModelError(f"{tag} 背景记录越出 chunk")
        payload = raw[pos:pos + ROOM_LAYER_BACKGROUND]
    elif kind == 2:                             # Instances：数量 + 实例 ID
        cnt = w.u32(pos) if pos + 4 <= ch.end else -1
        if not (0 <= cnt <= ROOM_MAX_ITEMS) or pos + 4 + 4 * cnt > ch.end:
            raise ModelError(f"{tag} 实例数量异常: {cnt}")
        payload = raw[pos:pos + 4 + 4 * cnt]
    elif kind == 3:                             # Assets：两张指针表
        fills = []
        for k, item_size in ((0, ROOM_TILE), (1, ROOM_SPRITE_INSTANCE)):
            slot = pos + 4 * k
            if slot + 4 > ch.end:
                raise ModelError(f"{tag}.assets[{k}] 指针越出 chunk")
            lp = w.u32(slot)
            if not (ch.base <= lp and lp + 4 <= ch.end):
                raise ModelError(f"{tag}.assets[{k}] 表 {lp:,} 越出 chunk")
            cnt = w.u32(lp)
            if cnt > ROOM_MAX_ITEMS or lp + 4 + 4 * cnt > ch.end:
                raise ModelError(f"{tag}.assets[{k}] 数量异常: {cnt:,}")
            recs = []
            for m in range(cnt):
                a = w.u32(lp + 4 + 4 * m)
                if not (ch.base <= a and a + item_size <= ch.end):
                    raise ModelError(
                        f"{tag}.assets[{k}][{m}] {a:,} 越出 chunk")
                refs = {}
                if item_size == ROOM_SPRITE_INSTANCE:
                    s = text_at(w.u32(a))
                    if s is not None:
                        refs[0] = (s, "str")
                        sites.append((a - ch.base, s))
                rec = Record(raw[a:a + item_size], refs=refs, origin=a)
                recs.append(rec)
                sites.append((lp + 4 + 4 * m - ch.base, rec))
                pieces.append((a - ch.base, a - ch.base + item_size,
                               f"{tag}.assets[{k}][{m}]"))
            pl = PtrList(recs)
            fills.append(pl)
            sites.append((slot - ch.base, pl))
            pieces.append((lp - ch.base, lp - ch.base + 4 + 4 * cnt,
                           f"{tag}.assets[{k}] 表"))
        pos += 4 * len(fills)
    elif kind == 4:                             # Tiles：背景索引 + 网格
        if pos + 12 > ch.end:
            raise ModelError(f"{tag} 图块头越出 chunk")
        tx, ty = w.u32(pos + 4), w.u32(pos + 8)
        size = 12 + 4 * tx * ty
        if pos + size > ch.end:
            raise ModelError(f"{tag} 图块网格 {tx}x{ty} 越出 chunk")
        payload = raw[pos:pos + size]
    else:
        raise ModelError(f"{tag} 类型 {kind} 未建模（Path/Effect 之类）")

    pieces.append((L - ch.base, pos + len(payload) - ch.base, tag))
    return LayerItem(name=name, header=header, kind=kind,
                     payload=payload, fills=fills)


class LayerItem:
    """GMS2 的一个图层：36 字节头 + 按类型的数据。

    头（+0..+36）::

        +0  名字指针   +4 LayerId   +8 LayerType   +12 LayerDepth
        +16 XOffset / YOffset / HSpeed / VSpeed（f32 ×4）   +32 IsVisible

    数据按类型（UTMT ``UndertaleRoom.Layer``）::

        0 Path       —— 没有数据
        1 Background —— 40 字节定长（内部只有索引，没有地址）
        2 Instances  —— u32 数量 + 数量 × 实例 ID（全是数字）
        3 Assets     —— 两个指针表：Tile(48B) / SpriteInstance(44B，+0 是名字)
        4 Tiles      —— 背景索引 + X + Y + X*Y 个 u32（全是数字）
        6 Effect     —— 2.2 以前是「字符串 + 属性表」，没建模，直接拒绝
    """

    __slots__ = ("name", "header", "kind", "payload", "fills", "addr")

    def __init__(self, name: Optional[GmString], header: bytes, kind: int,
                 payload: bytes = b"", fills: Optional[Sequence[PtrList]] = None):
        self.name = name
        self.header = bytes(header)          # +4..+36
        self.kind = kind
        self.payload = bytes(payload)        # 类型自己的那一段（不含指针）
        self.fills = None if fills is None else list(fills)
        self.addr = -1

    def size_of(self) -> int:
        total = self.head_size
        if self.fills:
            for pl in self.fills:
                total += 4 + 4 * len(pl.items)
                total += sum(len(r.data) for r in pl.items)
        return total

    @property
    def head_size(self) -> int:
        """图层自己占的字节（含 Assets 的那几个指针，不含子表本体）。"""
        return ROOM_LAYER_HEADER + len(self.payload) + (
            4 * len(self.fills) if self.fills else 0)

    def emit(self, w) -> None:
        w.begin(self)
        if self.name is not None:
            w.string_ref(self.name)
        else:
            w.u32(0)
        w.raw(self.header)
        if self.fills:
            for pl in self.fills:            # 先写指针
                w.ref(pl)
            for pl in self.fills:            # 再写两张表本体
                pl.emit(w)
        else:
            w.raw(self.payload)


class RoomItem:
    """ROOM 的一个条目：名字 + caption + 头部 + 四张表（+ GMS2 的图层表）。"""

    __slots__ = ("name", "caption", "header", "head_refs", "lists",
                 "layers", "addr")

    def __init__(self, name: Optional[GmString], caption: Optional[GmString],
                 header: bytes, head_refs: Dict[int, PtrList],
                 lists: Dict[str, Optional[PtrList]],
                 layers: Optional[PtrList] = None):
        self.name = name
        self.caption = caption
        self.header = bytes(header)          # +8..（88 或 92）
        self.head_refs = dict(head_refs)     # 头里的指针槽位（相对 +8）
        self.lists = dict(lists)
        self.layers = layers

    def emit(self, w) -> None:
        w.begin(self)
        if self.name is not None:
            w.string_ref(self.name)
        else:
            w.u32(0)
        if self.caption is not None:
            w.string_ref(self.caption)
        else:
            w.u32(0)
        # 头里混着四个（GMS2 是五个）绝对指针，按槽位切开写
        pos = 0
        for off in sorted(self.head_refs):
            w.raw(self.header[pos:off])
            w.ref(self.head_refs[off])
            pos = off + 4
        w.raw(self.header[pos:])
        # 指针写完之后才是各张表本体
        for key, _off in ROOM_LISTS:
            lp = self.lists.get(key)
            if lp is not None:
                lp.emit(w)
        if self.layers is not None:
            self.layers.emit(w)


def _check_tiling(pieces: List[Tuple[int, int, str]], size: int,
                  what: str) -> None:
    """校验一组结构首尾相接、正好铺满 ``[0, size)``。

    ROOM 里没有任何补位字节：条目头、四张指针表、每条表项都紧挨着写。
    所以「每段起点 == 上一段终点」且「最后一段终点 == chunk 长度」这两个条件
    同时成立时，等于证明**每个字节都有归属** —— 少算或多算一个字节都会
    在这里断开，并指名是哪一段。
    """
    pos = 0
    for start, end, label in sorted(pieces):
        if end <= start:
            raise ModelError(f"{what} 出现零长或倒序的段: {label}")
        if start != pos:
            raise ModelError(
                f"{what} 布局在 {pos:,} 处断开: {label} 从 {start:,} 开始"
                f"（差 {start - pos:+,} 字节）")
        pos = end
    if pos != size:
        raise ModelError(
            f"{what} 末尾差 {size - pos:+,} 字节（铺到 {pos:,}，chunk {size:,}）")


class RoomTable(Chunk):
    """ROOM：房间表。两种布局（GMS1 定长头 / GMS2 多一层图层）。"""

    name = "ROOM"

    def __init__(self, items: Sequence[RoomItem],
                 sizes: Optional[Dict[str, int]] = None,
                 gms2: bool = False, tail: int = 0):
        self.items = list(items)
        self.sizes = dict(sizes or {k: ROOM_ITEM_ALLOWED[k][0]
                                    for k, _ in ROOM_LISTS})
        self.gms2 = gms2
        self.tail = tail
        # 解析时登记的指针位置：chunk 内偏移 -> 被指向的对象。
        # 重建之后按它逐字节核对：同一偏移在新文件里必须指向同一个对象。
        self.sites: List[Tuple[int, object]] = []

    @property
    def header_len(self) -> int:
        return ROOM_HEADER_GMS2 if self.gms2 else ROOM_HEADER

    @staticmethod
    def _list_size(pl: PtrList) -> int:
        total = 4 + 4 * len(pl.items)
        for obj in pl.items:
            total += obj.size_of() if isinstance(obj, LayerItem) \
                else len(obj.data)
        return total

    # ---- 布局 ----
    def layout_size(self) -> int:
        """按模型算出的 chunk 数据长度（不含 chunk 末尾的补位）。"""
        total = 4 + 4 * len(self.items)
        for it in self.items:
            total += ROOM_HEADER_SLOT + len(it.header)
            for _key, _off in ROOM_LISTS:
                lp = it.lists.get(_key)
                if lp is not None:
                    total += self._list_size(lp)
            if it.layers is not None:
                total += self._list_size(it.layers)
        return total

    def _pieces(self, addr_of, base: int) -> List[Tuple[int, int, str]]:
        """把每个结构在新（或原）文件里的字节区间列出来，供铺满校验。"""
        out: List[Tuple[int, int, str]] = [(0, 4 + 4 * len(self.items), "指针表")]
        for i, it in enumerate(self.items):
            a = addr_of(it) - base
            out.append((a, a + ROOM_HEADER_SLOT + len(it.header),
                        f"Room[{i}] 头"))
            for key, _off in ROOM_LISTS:
                lp = it.lists.get(key)
                if lp is None:
                    continue
                la = addr_of(lp) - base
                out.append((la, la + 4 + 4 * len(lp.items),
                            f"Room[{i}].{key} 表"))
                for j, r in enumerate(lp.items):
                    ra = addr_of(r) - base
                    out.append((ra, ra + len(r.data), f"Room[{i}].{key}[{j}]"))
            if it.layers is None:
                continue
            la = addr_of(it.layers) - base
            out.append((la, la + 4 + 4 * len(it.layers.items),
                        f"Room[{i}].Layers 表"))
            for j, L in enumerate(it.layers.items):
                lx = addr_of(L) - base
                out.append((lx, lx + L.head_size, f"Room[{i}].layer[{j}]"))
                if not L.fills:
                    continue
                for k, pl in enumerate(L.fills):
                    na = addr_of(pl) - base
                    out.append((na, na + 4 + 4 * len(pl.items),
                                f"Room[{i}].layer[{j}].assets[{k}] 表"))
                    for m, r in enumerate(pl.items):
                        ra = addr_of(r) - base
                        out.append((ra, ra + len(r.data),
                                    f"Room[{i}].layer[{j}].assets[{k}][{m}]"))
        return out

    # ---- 读 ----
    @classmethod
    def from_win(cls, w, table: Optional["StringTable"] = None) -> "RoomTable":
        ch = w.chunks.get("ROOM")
        if ch is None or ch.size < 8:
            raise ModelError("没有 ROOM chunk")
        raw = w.raw
        n = w.u32(ch.base)
        if n == 0:
            return cls([])
        if 4 + 4 * n > ch.size:
            raise ModelError(f"ROOM 指针表越界: count={n:,}")
        if table is None:
            table = StringTable.from_win(w)
        text_at = _string_content_map(w, table)
        ptrs = [w.u32(ch.base + 4 + 4 * i) for i in range(n)]
        gms2 = bool(w.u32(ptrs[0] + 36) & ROOM_FLAG_GMS2)
        header_len = ROOM_HEADER_GMS2 if gms2 else ROOM_HEADER
        for i, p in enumerate(ptrs):
            if not (ch.base <= p and p + header_len <= ch.end):
                raise ModelError(f"ROOM[{i}] 条目 {p:,} 越出 chunk")

        bytecode = _bytecode_version(w)
        sizes = {key: _room_item_size(w, ch, ptrs, key, off, bytecode)
                 for key, off in ROOM_LISTS}

        pieces: List[Tuple[int, int, str]] = [(0, 4 + 4 * n, "指针表")]
        # 登记的是**槽位**（写指针的那 4 个字节），不是目标地址：
        # 每一处都要说清「这个字的 4 字节指向哪个对象」，重建后逐位复核
        sites: List[Tuple[int, object]] = []
        items: List[RoomItem] = []
        for i, p in enumerate(ptrs):
            base = p - ch.base
            name = text_at(w.u32(p))
            caption = text_at(w.u32(p + 4))
            lists: Dict[str, Optional[PtrList]] = {}
            head_refs: Dict[int, PtrList] = {}
            for key, off in ROOM_LISTS:
                lp = w.u32(p + off)
                if lp == 0:
                    lists[key] = None      # 原文件就是空指针，照原样保留
                    continue
                size = sizes[key]
                cnt = w.u32(lp)
                if cnt > ROOM_MAX_ITEMS or lp + 4 + 4 * cnt > ch.end:
                    raise ModelError(f"ROOM.{key} 表数量异常: {cnt:,} @ {lp:,}")
                recs = []
                for j in range(cnt):
                    a = w.u32(lp + 4 + 4 * j)
                    if not (ch.base <= a and a + size <= ch.end):
                        raise ModelError(
                            f"ROOM[{i}].{key}[{j}] 表项 {a:,} 越出 chunk")
                    rec = Record(raw[a:a + size], origin=a)
                    recs.append(rec)
                    sites.append((lp + 4 + 4 * j - ch.base, rec))
                    pieces.append((a - ch.base, a - ch.base + size,
                                   f"Room[{i}].{key}[{j}]"))
                pl = PtrList(recs)
                lists[key] = pl
                head_refs[off - ROOM_HEADER_SLOT] = pl
                sites.append((p + off - ch.base, pl))
                pieces.append((lp - ch.base, lp - ch.base + 4 + 4 * cnt,
                               f"Room[{i}].{key} 表"))

            layers = None
            if gms2:
                lp = w.u32(p + 88)
                if lp == 0:
                    raise ModelError(f"ROOM[{i}] 是 GMS2 布局但没有 Layers 表")
                cnt = w.u32(lp)
                if cnt > ROOM_MAX_LAYERS or lp + 4 + 4 * cnt > ch.end:
                    raise ModelError(f"ROOM[{i}].Layers 数量异常: {cnt:,}")
                objs = []
                for j in range(cnt):
                    L = w.u32(lp + 4 + 4 * j)
                    objs.append(_read_layer(w, ch, L, text_at, sites, pieces,
                                            i, j))
                    sites.append((lp + 4 + 4 * j - ch.base, objs[-1]))
                layers = PtrList(objs)
                head_refs[ROOM_LAYERS_SLOT] = layers
                sites.append((p + 88 - ch.base, layers))
                pieces.append((lp - ch.base, lp - ch.base + 4 + 4 * cnt,
                               f"Room[{i}].Layers 表"))

            items.append(RoomItem(name=name, caption=caption,
                                  header=raw[p + 8:p + header_len],
                                  head_refs=head_refs, lists=lists,
                                  layers=layers))
            pieces.append((base, base + header_len, f"Room[{i}] 头"))
            sites.append((4 + 4 * i, items[-1]))
            if name is not None:
                sites.append((base, name))
            if caption is not None:
                sites.append((base + 4, caption))

        last = max(e for _a, e, _l in pieces)
        tail = ch.size - last
        if tail < 0 or tail > ROOM_TAIL_MAX:
            raise ModelError(f"ROOM 末尾没有归属的字节 {tail:+,}（超出 {ROOM_TAIL_MAX}）")
        if any(raw[ch.base + last:ch.end]):
            raise ModelError("ROOM 末尾的补位不全是 0")
        if tail:
            pieces.append((last, ch.size, "末尾补位"))

        out = cls(items, sizes, gms2=gms2, tail=tail)
        out.sites = sites
        _check_tiling(pieces, ch.size, "ROOM")
        return out

    def emit(self, w) -> None:
        w.u32(len(self.items))
        for it in self.items:
            w.ref(it)
        for it in self.items:
            it.emit(w)

    # ---- 自检 ----
    def verify(self, built) -> None:
        span = built.chunk_span(self.name)
        if span is None:
            raise ModelError("ROOM 没有出现在输出里")
        base, end = span
        guard = built.guard_of(self.name)
        # 写出的字节必须正好是模型算出来的长度（多一个少一个都说明布局错了）
        if (end - base) - guard != self.layout_size():
            raise ModelError(
                f"ROOM 写出 {end - base - guard:,} 字节，模型算出 "
                f"{self.layout_size():,} 字节")
        # 写出的每个结构必须首尾相接地铺满整个 chunk
        _check_tiling(self._pieces(built.addr_of, base), end - base - guard,
                      "ROOM")
        for i, it in enumerate(self.items):
            a = built.addr_of(it)
            if not (base <= a < end):
                raise ModelError(f"ROOM[{i}] 条目 {a:,} 不在 chunk 内")
            if it.name is not None and built.u32(a) != built.addr_of(it.name) + 4:
                raise ModelError(f"ROOM[{i}] 名字未回填")
            if it.caption is not None and built.u32(a + 4) != \
                    built.addr_of(it.caption) + 4:
                raise ModelError(f"ROOM[{i}] caption 未回填")
            slot = ROOM_HEADER_SLOT
            for key, off in ROOM_LISTS:
                lp = it.lists.get(key)
                got = built.u32(a + off)
                if lp is None:
                    if got != 0:
                        raise ModelError(f"ROOM[{i}] {key} 应为空指针")
                    continue
                if got != built.addr_of(lp):
                    raise ModelError(
                        f"ROOM[{i}] {key} 表指针 {got:,} != "
                        f"{built.addr_of(lp):,}")
                for j, r in enumerate(lp.items):
                    if len(r.data) != self.sizes[key]:
                        raise ModelError(
                            f"ROOM[{i}].{key}[{j}] 表项 {len(r.data)} 字节，"
                            f"应为 {self.sizes[key]} 字节")
            if it.layers is None:
                continue
            if built.u32(a + 88) != built.addr_of(it.layers):
                raise ModelError(f"ROOM[{i}] Layers 指针未回填")
            for j, L in enumerate(it.layers.items):
                la = built.addr_of(L)
                if L.name is not None and \
                        built.u32(la) != built.addr_of(L.name) + 4:
                    raise ModelError(f"ROOM[{i}].layer[{j}] 名字未回填")
                if not L.fills:
                    continue
                for k, pl in enumerate(L.fills):
                    if built.u32(la + ROOM_LAYER_HEADER + 4 * k) != \
                            built.addr_of(pl):
                        raise ModelError(
                            f"ROOM[{i}].layer[{j}].assets[{k}] 表指针未回填")


class FlatStructChunk(Chunk):
    """定长结构块（GEN8 / OPTN / LANG / EXTN …）：数字 + 少量字符串。

    布局由 UTMT 的 ``UndertaleGeneralInfo`` 定义，这里不重写它 —— 只把
    **能证实的字符串槽位**（值都落在原文件 STRG 的正文地址上）换成符号引用，
    其余字节原样保留。这样哪怕版本字段的结构变了，也不会写错。
    """

    name = ""
    guard = 0

    def __init__(self, name: str, data: bytes, refs: Dict[int, tuple],
                 extra: Optional[Sequence["GmString"]] = None):
        self.name = name
        self.data = bytes(data)
        self.refs = dict(refs)
        # 指向「STRG 表里没有的字符串记录」时，把那条字符串重新造出来，
        # 由上层追加到表尾（追加不影响已有下标）
        self.extra: List[GmString] = list(extra or [])

    @classmethod
    def from_win(cls, w, name: str,
                 table: Optional["StringTable"] = None) -> "FlatStructChunk":
        if table is None:
            table = StringTable.from_win(w)
        ch = w.chunks
        strg = ch.get("STRG")
        if strg is None:
            raise ModelError(f"{self.name} 没有 STRG 可以对照")
        content = set()
        n = w.u32(strg.base)
        for i in range(n):
            rec = w.u32(strg.base + 4 + 4 * i)
            ln = w.u32(rec)
            if 0 < ln <= 0xFFFF:
                content.add(rec + 4)
        by_addr = {}
        for i, s in enumerate(table.strings):
            rec = w.u32(strg.base + 4 + 4 * i)
            by_addr[rec + 4] = s

        if name == "GEN8":
            data = w.raw[w.gen8_off:w.gen8_off + w.gen8_len]
        else:
            ch2 = w.chunks.get(name)
            if ch2 is None:
                raise ModelError(f"没有 {name} chunk")
            data = w.raw[ch2.base:ch2.end]
        refs = {}
        extra: List[GmString] = []
        for off in range(0, len(data) - 3, 4):
            v = struct.unpack_from("<I", data, off)[0]
            if v in by_addr:
                refs[off] = (by_addr[v], "str")
                continue
            s = _string_at_content(w, v)
            if s is not None:
                refs[off] = (s, "str")
                extra.append(s)
        return cls(name, data, refs, extra)

    def emit(self, w) -> None:
        # chunk 起点由容器登记（见 serializer._emit_file），这里只写数据
        pos = 0
        for off in sorted(self.refs):
            w.raw(self.data[pos:off])
            w.string_ref(self.refs[off][0])
            pos = off + 4
        w.raw(self.data[pos:])

    def verify(self, built) -> None:
        span = built.chunk_span(self.name)
        if span is None:
            raise ModelError(f"{self.name} 没有出现在输出里")
        base, _end = span
        for off, (s, _kind) in self.refs.items():
            if built.u32(base + off) != built.addr_of(s) + 4:
                raise ModelError(f"{self.name} 槽位 {off} 未回填")


# ══════════════════════════════════════════════════════════════════
#  容器
# ══════════════════════════════════════════════════════════════════


class DataFile:
    """一份 data.win 的模型：有序的 chunk 列表。

    第一个 chunk 必须是 GEN8（GameMaker 的约定），它同样按
    ``名字 + 长度 + 数据`` 存放，所以不需要特殊分支。
    """

    MAGIC = b"FORM"

    def __init__(self, chunks: Sequence[Chunk], path: str = ""):
        self.chunks: List[Chunk] = list(chunks)
        self.path = path

    def chunk(self, name: str) -> Optional[Chunk]:
        return next((c for c in self.chunks if c.name == name), None)

    def order(self) -> List[str]:
        return [c.name for c in self.chunks]

    @classmethod
    def gen8_chunk(cls, w) -> Optional[RawChunk]:
        """GEN8 不在 ``order`` 里（解析器把它当容器头），单独取一次。"""
        try:
            start = w.gen8_off
            length = w.gen8_len
        except AttributeError:
            return None
        if length <= 0 or start + length > len(w.raw):
            return None
        return RawChunk("GEN8", w.raw[start:start + length])

    # ---- 读 ----
    @classmethod
    def from_win(cls, w, modeled: Iterable[str] = (), path: str = "") -> "DataFile":
        """把 WinFile 读成模型。

        ``modeled`` 里的 chunk 用模型，其余的先用原字节占位。
        """
        want = {n.upper() for n in modeled}
        table: Optional[StringTable] = None
        if "STRG" in want:
            table = StringTable.from_win(w)
        chunks: List[Chunk] = []
        gen8 = cls.gen8_chunk(w)
        if gen8 is not None:
            chunks.append(gen8)
        for name in w.order:
            ch = w.chunks[name]
            if name in want:
                if name == "STRG":
                    chunks.append(table)
                elif name == "FONT":
                    chunks.append(FontTable.from_win(w, strings=table))
                elif name == "TXTR":
                    chunks.append(TextureTable.from_win(w))
                elif name == "AUDO":
                    chunks.append(AudioTable.from_win(w))
                else:
                    raise ModelError(f"没有 {name} 的模型")
            else:
                chunks.append(RawChunk(name, w.raw[ch.base:ch.end]))
        return cls(chunks, path=path)

    def verify(self, built) -> None:
        for c in self.chunks:
            c.verify(built)

    def __repr__(self) -> str:                       # pragma: no cover
        return f"<DataFile {'+'.join(self.order())}>"
