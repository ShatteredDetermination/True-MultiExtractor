# -*- coding: utf-8 -*-
"""自研 GameMaker data.win 解析器（取代 UndertaleModTool 依赖）。

为什么不用版本表
----------------
GameMaker 的 chunk 内部布局随版本连续变动，UTMT 靠一张巨大的版本表硬编码，
遇到表外的版本就直接抛异常。本解析器改走另一条路：**让文件自己描述自己**。

两个事实撑起这套做法（均在真实样本上实测过）：

1. 每个「列表型」chunk 的头部是 ``u32 count`` + ``u32 指针[count]``，
   指针是**文件绝对偏移**。相邻指针之差就是条目尺寸 —— 条目尺寸不用猜，
   量出来即可（实测 TXTR 在 bytecode 16 是 8 字节、bytecode 17 是 28 字节）。

2. 条目内部的字段位置同样能量出来：在条目里扫 u32，看哪一个指向
   「已知格式的纹理数据」或「合法的 TPAG 条目」，取多数条目都成立的偏移。

所以本模块对版本差异是**免疫**的，只依赖两个不变式：chunk 表的形状、
以及指针表指向条目本体的起点。

读 / 写
-------
``WinFile`` 现在只做读。但它保留 ``raw``（原始字节）与每个结构的文件偏移，
``as_writable()`` 给出可改的 bytearray —— V0.15 的实验性重建就挂在这上面：
重建只改结构、不拆包，目标是改完的文件**原版 UTMT 也能解析**。
"""

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from me.formats import gamemaker_image as gimg

MAGIC_FORM = b"FORM"
MAGIC_GEN8 = b"GEN8"

# 认得出名字的 chunk（全大写字母+数字）
def _is_chunk_name(b: bytes) -> bool:
    return len(b) == 4 and all(
        (65 <= c <= 90) or (48 <= c <= 57) for c in b)


class NotGamemaker(Exception):
    """开头不是 FORM/GEN8。"""


class ParseError(Exception):
    """结构读不下去。"""


@dataclass
class Chunk:
    name: str
    off: int          # 名字字段的文件偏移
    size: int         # 长度字段的值
    base: int         # 数据起点 = off + 8
    end: int          # base + size


@dataclass
class Tpag:
    """TPAG 记录：纹理页上的一块矩形。"""
    off: int
    x: int
    y: int
    w: int
    h: int
    sheet: int


@dataclass
class Sprite:
    off: int
    name: str
    frames: List[int] = field(default_factory=list)   # TPAG 下标


@dataclass
class Background:
    off: int
    name: str
    frames: List[int] = field(default_factory=list)


@dataclass
class Texture:
    off: int          # 条目偏移
    blob: int         # blob 的文件偏移
    kind: str = ""    # png / 2zoq / qoi / bz2


@dataclass
class Sound:
    off: int
    name: str = ""
    file: str = ""            # 外置音频的相对路径
    audo: int = -1            # AUDO 下标，-1 表示外置或认不出


class WinFile:
    """一份 data.win。"""

    def __init__(self, raw: bytes, path: Optional[Path] = None):
        self.raw = raw
        self.path = path
        self.chunks: Dict[str, Chunk] = {}
        self.order: List[str] = []
        self._parse_container()
        self._cache: Dict[str, object] = {}

    # ------------------------------------------------------------ 载入
    @classmethod
    def load(cls, path) -> "WinFile":
        p = Path(path)
        return cls(p.read_bytes(), p)

    def as_writable(self) -> bytearray:
        """给 V0.15 重建用的可写副本。长度必须保持，绝对偏移才不失效。"""
        return bytearray(self.raw)

    def reader(self):
        """当前用的是 bytes；留成方法，以后换 mmap 不用改调用方。"""
        return self.raw

    # ------------------------------------------------------------ 容器
    def _parse_container(self) -> None:
        d = self.raw
        if len(d) < 16 or d[:4] != MAGIC_FORM or d[8:12] != MAGIC_GEN8:
            raise NotGamemaker("开头不是 FORM/GEN8")
        self.gen8_len = struct.unpack_from("<I", d, 12)[0]
        self.gen8_off = 16
        off = 16 + self.gen8_len
        n = len(d)
        while off + 8 <= n:
            nm = d[off:off + 4]
            if not _is_chunk_name(nm):
                break
            size = struct.unpack_from("<I", d, off + 4)[0]
            end = off + 8 + size
            if end > n:
                raise ParseError(f"chunk {nm!r} 长度越界")
            name = nm.decode("ascii")
            self.chunks[name] = Chunk(name, off, size, off + 8, end)
            self.order.append(name)
            off = end
        self.chain_end = off
        if not self.chunks:
            raise ParseError("没有解析出任何 chunk")

    @property
    def chain_ok(self) -> bool:
        return self.chain_end == len(self.raw)

    def bytecode(self) -> int:
        """GEN8 首字段的高字节。实测 0x1001 -> 16，0x1101 -> 17。"""
        if self.gen8_len < 4:
            return 0
        v = struct.unpack_from("<I", self.raw, self.gen8_off)[0]
        return (v >> 8) & 0xFF

    def u32(self, off: int) -> int:
        return struct.unpack_from("<I", self.raw, off)[0]

    def u16(self, off: int) -> int:
        return struct.unpack_from("<H", self.raw, off)[0]

    # ------------------------------------------------------------ 列表型
    def list_chunk(self, name: str) -> Tuple[int, List[int]]:
        """返回 (count, [条目偏移])。chunk 不存在返回 (0, [])。"""
        ch = self.chunks.get(name)
        if ch is None or ch.size < 4:
            return 0, []
        n = self.u32(ch.base)
        if n == 0:
            return 0, []
        if 4 + 4 * n > ch.size:
            raise ParseError(f"{name} 指针表越界（count={n}）")
        ptrs = [self.u32(ch.base + 4 + 4 * i) for i in range(n)]
        return n, ptrs

    def entry_size(self, name: str, ptrs: Sequence[int], i: int) -> int:
        """用相邻指针之差量出条目尺寸；最后一个用 chunk 末尾封口。"""
        if i + 1 < len(ptrs):
            return ptrs[i + 1] - ptrs[i]
        ch = self.chunks.get(name)
        return (ch.end if ch else len(self.raw)) - ptrs[i]

    def entry_step(self, name: str, ptrs: Sequence[int]) -> int:
        """条目尺寸的众数。变长条目（SPRT/FONT）返回最常见值。"""
        if len(ptrs) < 2:
            return 0
        from collections import Counter
        c = Counter(ptrs[i + 1] - ptrs[i] for i in range(len(ptrs) - 1))
        return c.most_common(1)[0][0]

    # ------------------------------------------------------------ STRG
    def strings(self) -> List[str]:
        if "strg" in self._cache:
            return self._cache["strg"]           # type: ignore
        n, ptrs = self.list_chunk("STRG")
        out: List[str] = []
        for p in ptrs:
            try:
                ln = self.u32(p)
            except struct.error:
                out.append("")
                continue
            if ln > 1 << 20:
                out.append("")
                continue
            out.append(self.raw[p + 4:p + 4 + ln].decode("utf-8", "replace"))
        self._cache["strg"] = out
        self._cache["strg_set"] = set(ptrs)
        return out

    def _strg_span(self) -> Tuple[int, int]:
        ch = self.chunks.get("STRG")
        return (ch.base, ch.end) if ch else (0, 0)

    def string_at(self, off: int) -> str:
        """读一个字符串指针。

        GameMaker 里两种形态都真实存在，实测都有：
          * STRG 的指针表指向「长度字段」本身
          * 资源的 Name 字段指向「字符数据」，跳过了前面那 4 字节长度
            （Undertale 的 sprFaceFlowey：指针 1675128，长度 13 在 1675124）
        所以先按前者试，不成立再按后者试，都不成立就顺着读到 NUL。
        """
        n = len(self.raw)
        if not (0 <= off < n - 4):
            return ""
        ln = self.u32(off)
        if 0 < ln <= 4096 and off + 5 + ln <= n and self.raw[off + 4 + ln] == 0:
            return self.raw[off + 4:off + 4 + ln].decode("utf-8", "replace")
        if off >= 4:
            back = self.u32(off - 4)
            if 0 < back <= 4096 and off + back < n and self.raw[off + back] == 0:
                return self.raw[off:off + back].decode("utf-8", "replace")
        end = self.raw.find(b"\x00", off, min(n, off + 512))
        if end > off:
            return self.raw[off:end].decode("utf-8", "replace")
        return ""

    # ------------------------------------------------------------ TXTR
    def _discover_texture_field(self, ptrs: Sequence[int]) -> int:
        """量出条目里「blob 文件偏移」在哪个 4 字节槽。"""
        if not ptrs:
            return -1
        n = len(self.raw)
        probe = min(len(ptrs), 8)
        common: Optional[set] = None
        for i in range(probe):
            p = ptrs[i]
            step = self.entry_size("TXTR", ptrs, i)
            cands = set()
            for k in range(0, max(0, step - 3), 4):
                v = self.u32(p + k)
                if 16 < v < n and gimg.sniff(self.raw, v):
                    cands.add(k)
            common = cands if common is None else (common & cands)
            if common is not None and not common:
                # 交集空了就退回逐个条目的并集里最常见的那个
                break
        if common:
            return sorted(common)[0]
        # 退化路径：单个条目里第一个像纹理的槽
        p = ptrs[0]
        step = self.entry_size("TXTR", ptrs, 0)
        for k in range(0, max(0, step - 3), 4):
            v = self.u32(p + k)
            if 16 < v < n and gimg.sniff(self.raw, v):
                return k
        return -1

    def textures(self) -> List[Texture]:
        if "txtr" in self._cache:
            return self._cache["txtr"]           # type: ignore
        n, ptrs = self.list_chunk("TXTR")
        field_off = self._discover_texture_field(ptrs)
        out: List[Texture] = []
        for p in ptrs:
            blob = self.u32(p + field_off) if field_off >= 0 else 0
            out.append(Texture(off=p, blob=blob,
                               kind=gimg.sniff(self.raw, blob) if blob else ""))
        self._cache["txtr"] = out
        self._cache["txtr_field"] = field_off
        return out

    def texture_field(self) -> int:
        self.textures()
        return int(self._cache.get("txtr_field", -1))    # type: ignore

    # ------------------------------------------------------------ TPAG
    def tpag(self) -> List[Tpag]:
        if "tpag" in self._cache:
            return self._cache["tpag"]           # type: ignore
        n, ptrs = self.list_chunk("TPAG")
        out: List[Tpag] = []
        for p in ptrs:
            out.append(Tpag(off=p,
                            x=self.u16(p), y=self.u16(p + 2),
                            w=self.u16(p + 4), h=self.u16(p + 6),
                            sheet=self.u16(p + 20)))
        self._cache["tpag"] = out
        self._cache["tpag_index"] = {t.off: i for i, t in enumerate(out)}
        return out

    def tpag_index(self) -> Dict[int, int]:
        self.tpag()
        return self._cache["tpag_index"]         # type: ignore

    def _tpag_offsets(self) -> set:
        self.tpag()
        return set(self._cache["tpag_index"])    # type: ignore

    # ------------------------------------------------------------ 精灵/背景
    def _discover_frame_field(self, name: str, ptrs: Sequence[int],
                              valid: set) -> int:
        """量出条目里「帧指针数组」的起点偏移。"""
        probe = min(len(ptrs), 12)
        from collections import Counter
        votes = Counter()
        for i in range(probe):
            p = ptrs[i]
            step = self.entry_size(name, ptrs, i)
            k = 4
            while k + 4 <= min(step, 256):
                if self.u32(p + k) in valid:
                    votes[k] += 1
                    break
                k += 4
        if not votes:
            return -1
        return votes.most_common(1)[0][0]

    def _read_frames(self, name: str, ptrs: Sequence[int], i: int,
                     field_off: int, valid: set) -> List[int]:
        """读一个条目的帧数组，返回 TPAG 下标。"""
        p = ptrs[i]
        step = self.entry_size(name, ptrs, i)
        idx = self.tpag_index()
        out: List[int] = []

        # 数组前面那个字段是帧数（实测：bytecode 16 在 +56、17 在 +84）
        count = -1
        if field_off >= 4:
            c = self.u32(p + field_off - 4)
            if 0 < c <= 8192 and field_off + 4 * c <= step:
                count = c

        if count < 0:
            # 量不出帧数就顺着连续的有效指针走到头
            k = field_off
            while k + 4 <= step and self.u32(p + k) in valid:
                out.append(idx[self.u32(p + k)])
                k += 4
            return out

        for j in range(count):
            v = self.u32(p + field_off + 4 * j)
            if v not in valid:
                break
            out.append(idx[v])
        return out

    def sprites(self) -> List[Sprite]:
        if "sprt" in self._cache:
            return self._cache["sprt"]           # type: ignore
        n, ptrs = self.list_chunk("SPRT")
        if not ptrs:
            self._cache["sprt"] = []
            return []
        valid = self._tpag_offsets()
        foff = self._discover_frame_field("SPRT", ptrs, valid)
        out: List[Sprite] = []
        for i, p in enumerate(ptrs):
            nm = self.string_at(self.u32(p))
            frames = self._read_frames("SPRT", ptrs, i, foff, valid) \
                if foff >= 0 else []
            out.append(Sprite(off=p, name=nm or f"sprite_{i}", frames=frames))
        self._cache["sprt"] = out
        self._cache["sprt_field"] = foff
        return out

    def sprite_field(self) -> int:
        self.sprites()
        return int(self._cache.get("sprt_field", -1))    # type: ignore

    def backgrounds(self) -> List[Background]:
        if "bgnd" in self._cache:
            return self._cache["bgnd"]           # type: ignore
        n, ptrs = self.list_chunk("BGND")
        if not ptrs:
            self._cache["bgnd"] = []
            return []
        tset = self._tpag_offsets()
        idx = self.tpag_index()
        out: List[Background] = []
        for i, p in enumerate(ptrs):
            nm = self.string_at(self.u32(p))
            frames: List[int] = []
            step = self.entry_size("BGND", ptrs, i)
            # 背景的帧数组：条目里第一段连续的有效 TPAG 指针
            k = 4
            while k + 4 <= min(step, 128):
                if self.u32(p + k) in tset:
                    while k + 4 <= step and self.u32(p + k) in tset:
                        frames.append(idx[self.u32(p + k)])
                        k += 4
                    break
                k += 4
            out.append(Background(off=p, name=nm or f"background_{i}",
                                  frames=frames))
        self._cache["bgnd"] = out
        return out

    # ------------------------------------------------------------ 声音
    def audo(self) -> List[Tuple[int, int]]:
        """返回 [(条目偏移, 数据长度)]。数据本体紧跟长度字段。"""
        if "audo" in self._cache:
            return self._cache["audo"]           # type: ignore
        n, ptrs = self.list_chunk("AUDO")
        out: List[Tuple[int, int]] = []
        for p in ptrs:
            out.append((p, self.u32(p)))
        self._cache["audo"] = out
        return out

    def audo_blob(self, entry_off: int, size: int) -> bytes:
        return self.raw[entry_off + 4:entry_off + 4 + size]

    def sounds(self) -> List[Sound]:
        """SOND 条目。字段位置按文件实测，认不出的字段留空。"""
        if "sond" in self._cache:
            return self._cache["sond"]           # type: ignore
        n, ptrs = self.list_chunk("SOND")
        lo, hi = self._strg_span()
        n_audo = len(self.audo())
        out: List[Sound] = []
        for i, p in enumerate(ptrs):
            step = self.entry_size("SOND", ptrs, i)
            s = Sound(off=p)
            str_ptrs = []
            for k in range(0, max(0, step - 3), 4):
                v = self.u32(p + k)
                if lo <= v < hi:
                    str_ptrs.append((k, v))
            if str_ptrs:
                s.name = self.string_at(str_ptrs[0][1])
            # 外置音频：条目里还有一个字符串指针，指向相对路径
            for k, v in str_ptrs[1:]:
                txt = self.string_at(v)
                if txt and ("." in txt or "/" in txt or "\\" in txt):
                    s.file = txt
                    break
            if not s.file and n_audo:
                # 找一个落在 AUDO 下标范围内的小整数
                for k in range(0, max(0, step - 3), 4):
                    v = self.u32(p + k)
                    if 0 <= v < n_audo and v != 0:
                        s.audo = v
                        break
            out.append(s)
        self._cache["sond"] = out
        return out

    # ------------------------------------------------------------ 诊断
    def dump(self) -> str:
        lines = [
            f"file      : {self.path}",
            f"size      : {len(self.raw):,}",
            f"bytecode  : {self.bytecode()}",
            f"chunks    : {len(self.order)}  chain_end={self.chain_end:,}"
            f" {'OK' if self.chain_ok else 'MISMATCH'}",
        ]
        for name in ("TXTR", "TPAG", "SPRT", "BGND", "SOND", "AUDO", "STRG",
                     "FONT"):
            ch = self.chunks.get(name)
            if ch is None:
                continue
            n, ptrs = self.list_chunk(name)
            step = self.entry_step(name, ptrs) if ptrs else 0
            lines.append(f"  {name:<5} n={n:<7} step={step:<6} size={ch.size:,}")
        tf = self.texture_field()
        lines.append(f"  TXTR blob 字段偏移 = {tf}")
        lines.append(f"  SPRT 帧数组偏移   = {self.sprite_field()}")
        return "\n".join(lines)
