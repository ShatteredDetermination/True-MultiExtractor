# -*- coding: utf-8 -*-
"""GameMaker 容器：布局与序列化。

职责边界
--------
AST 只描述语义（谁引用谁），本模块负责把语义变成字节：

1. **量测（dry pass）**：完整走一遍写出流程，但不落字节，只推进游标。
   这一遍把所有对象的**绝对地址**定下来，同时记下每个 chunk 的数据区间。
2. **写出（real pass）**：再走同一遍代码，这次真的写字节。每个对象落到
   哪个地址必须与第 1 遍完全一致 —— 不一致说明两次布局算错了，
   直接抛 ``ModelError``（这是这类「先量后写」实现最常见的隐性 bug，
   所以拿断言挡住，不给出半对半错的文件）。

为什么不用「边算边写」
----------------------
指针可以往前指（FONT 的名字指到后面 STRG 里），也可能指回自己所在的结构。
单遍写出时这些地址还不知道，只能事后回填，回填表一乱就是错位。
两遍走同一段代码，就没有「两套逻辑不同步」的问题。

对齐
----
``align_abs(n)`` 按**文件绝对偏移**补零。UTMT 的判据全部是绝对偏移
（STRG 补位到 0x80、TXTR 每条数据 0x80、AUDO 每条 4），所以对齐必须在
绝对坐标里做，不能只按 chunk 内偏移算。
"""

import struct
from typing import Dict, List, Optional, Tuple

from me.formats.gamemaker_ast import Chunk, DataFile, ModelError

U32 = struct.Struct("<I")

# 每个 chunk 数据末尾的守卫字节数。
#
# 实测（Ver 0.15E，UTMT 0.9.2）：chunk 解析在读完最后一个结构之后，
# 还会再读 1 个字节 —— 内容紧贴 chunk 末尾时这一步就越界，报
# ``Reading out of chunk bounds at <chunk 末尾>``。GM 自己写出的文件里，
# 每个 chunk 末尾都带着补位（data.win 的 FONT 后面就有 512 字节），
# 所以这个读取从来不会踩到边界；我们的排布是紧的，必须显式留出这一字节。
CHUNK_GUARD = 1


# ══════════════════════════════════════════════════════════════════
#  写出的结果（供自检读回）
# ══════════════════════════════════════════════════════════════════


class Built:
    """一份写出来的文件 + 布局表。自检用它反查。"""

    def __init__(self, data: bytes, addrs: Dict[int, int],
                 spans: Dict[str, Tuple[int, int]],
                 guards: Optional[Dict[str, int]] = None,
                 refs: Optional[List[Tuple[int, int]]] = None):
        self.data = data
        self._addrs = addrs
        self._spans = spans
        self._guards = guards or {}
        # 真写阶段记下的每个指针位置：(写入位置, 目标对象 id)。
        # 「原文件 vs 重建结果」逐字节比对靠它才知道哪些字是指针。
        self.refs: List[Tuple[int, int]] = list(refs or [])

    def guard_of(self, name: str) -> int:
        return self._guards.get(name, 0)

    # ---- 字节 ----
    def u32(self, off: int) -> int:
        if not (0 <= off <= len(self.data) - 4):
            raise ModelError(f"自检读到文件外: {off:,}")
        return U32.unpack_from(self.data, off)[0]

    @property
    def raw(self) -> bytes:
        return self.data

    # ---- 布局 ----
    def addr_of(self, obj) -> int:
        a = self._addrs.get(id(obj), -1)
        if a < 0:
            raise ModelError(f"对象没有地址: {obj!r}")
        return a

    def chunk_span(self, name: str) -> Optional[Tuple[int, int]]:
        return self._spans.get(name)

    @property
    def spans(self) -> Dict[str, Tuple[int, int]]:
        return dict(self._spans)


# ══════════════════════════════════════════════════════════════════
#  Writer
# ══════════════════════════════════════════════════════════════════


class Writer:
    """两遍共用的写出器。

    ``dry=True`` 只推进游标、登记地址；``dry=False`` 真写字节，并核对
    每个对象是否落在量测阶段给它算的地址上。
    """

    def __init__(self, dry: bool, addrs: Optional[Dict[int, int]] = None,
                 spans: Optional[Dict[str, Tuple[int, int]]] = None,
                 guards: Optional[Dict[str, int]] = None):
        self.dry = dry
        self.pos = 0
        self.buf = bytearray()
        self.addrs: Dict[int, int] = {} if addrs is None else addrs
        self.spans: Dict[str, Tuple[int, int]] = {} if spans is None else spans
        self.guards: Dict[str, int] = {} if guards is None else guards
        self.refs: List[Tuple[int, int]] = []

    # ---- 基本写入 ----
    def tell(self) -> int:
        return self.pos

    def raw(self, data: bytes) -> None:
        if not self.dry:
            self.buf += data
        self.pos += len(data)

    def u8(self, value: int) -> None:
        if not self.dry:
            self.buf.append(value & 0xFF)
        self.pos += 1

    def u32(self, value: int) -> None:
        if self.dry:
            self.pos += 4
            return
        self.buf += U32.pack(value & 0xFFFFFFFF)
        self.pos += 4

    def align_abs(self, alignment: int) -> int:
        """补零到**绝对偏移** alignment 的倍数，返回补了几个字节。"""
        pad = (-self.pos) % alignment
        if pad:
            self.raw(bytes(pad))
        return pad

    def patch_u32(self, where: int, value: int) -> None:
        if self.dry:
            return                              # 量测阶段不需要真值
        if not (0 <= where <= len(self.buf) - 4):
            raise ModelError(f"回填位置 {where:,} 越界（缓冲区 {len(self.buf):,}）")
        U32.pack_into(self.buf, where, value & 0xFFFFFFFF)

    # ---- 对象地址 ----
    def begin(self, obj) -> None:
        """登记对象起点。写出阶段必须与量测阶段一致。"""
        if self.dry:
            if id(obj) in self.addrs:
                raise ModelError(f"对象被登记了两次: {obj!r}")
            self.addrs[id(obj)] = self.pos
            return
        want = self.addrs.get(id(obj), -1)
        if want < 0:
            raise ModelError(f"写出阶段遇到量测阶段没登记的对象: {obj!r}")
        if want != self.pos:
            raise ModelError(
                f"布局漂移: {obj!r} 量测在 {want:,}，实际写到 {self.pos:,}")

    def ref(self, obj) -> None:
        """写一个指向对象起点的 u32（记录起点）。"""
        if self.dry:
            self.pos += 4
            return
        self.refs.append((self.pos, id(obj)))
        self.u32(self._addr(obj))

    def _addr(self, obj) -> int:
        a = self.addrs.get(id(obj), -1)
        if a < 0:
            raise ModelError(f"引用了没有被写出的对象: {obj!r}")
        return a

    def ref_at(self, obj, delta: int) -> None:
        """写一个指向「对象起点 + delta」的 u32。

        FUNC/VARI 的 FirstAddress 指进 CODE 条目的 bytecode 中间，就是这个用法。
        """
        if self.dry:
            self.pos += 4
            return
        self.refs.append((self.pos, id(obj)))
        self.u32(self._addr(obj) + delta)

    def string_ref(self, s) -> None:
        """写一个指向字符串**正文**的 u32（UTMT 会自己减 4 找记录起点）。"""
        if self.dry:
            self.pos += 4
            return
        self.refs.append((self.pos, id(s)))
        self.u32(self._addr(s) + 4)


# ══════════════════════════════════════════════════════════════════
#  组装
# ══════════════════════════════════════════════════════════════════


def _emit_file(data: DataFile, w: Writer) -> None:
    """FORM + u32 长度 + 各 chunk。两遍都走这里。

    chunk 头里的长度必须**严格等于** UTMT 读过的字节数：UTMT 的校验是
    ``if (读到的长度 != 头里的长度) 警告 "…(N padding?)" 然后跳过``。
    所以这里只按模型自己声明的 ``guard`` 补字节，绝不额外对齐 ——
    多补的字节没人读，就是一条警告。
    """
    w.raw(data.MAGIC)
    size_at = w.tell()
    w.u32(0)                                    # 先占位，最后回填

    for chunk in data.chunks:
        name = chunk.name.encode("ascii")
        if len(name) != 4:            raise ModelError(f"chunk 名字必须是 4 字节: {chunk.name!r}")
        w.raw(name)
        len_at = w.tell()
        w.u32(0)                                # 长度字段同样先占位
        data_start = w.tell()
        w.begin(chunk)                      # 登记 chunk 数据起点（供 ref_at 用）
        chunk.emit(w)
        guard = getattr(chunk, "guard", 0)
        if guard:
            w.raw(bytes(guard))
        data_end = w.tell()
        size = data_end - data_start
        # 不做任何对齐：GM 自己写出的文件里，chunk 数据长度本来就不一定是 4 的
        # 倍数（data2.win 的 TPAG 是 76,470 字节，下一块就落在 2 mod 4 上），
        # UTMT 也不按 4 对齐去找下一块。这里多补的字节没人读，只会变成警告。
        w.patch_u32(len_at, size)
        if w.dry:
            if chunk.name in w.spans:
                raise ModelError(f"chunk 名字重复: {chunk.name}")
            w.spans[chunk.name] = (data_start, data_end)
            w.guards[chunk.name] = guard

    w.patch_u32(size_at, w.pos - 8)


def build(data: DataFile) -> Built:
    """量测 → 写出 → 自检，返回结果。"""
    if not data.chunks:
        raise ModelError("空的容器")

    dry = Writer(dry=True)
    _emit_file(data, dry)

    real = Writer(dry=False, addrs=dry.addrs, spans=dry.spans,
                  guards=dry.guards)
    _emit_file(data, real)

    if real.pos != dry.pos:
        raise ModelError(f"两次走过长度不一致: {real.pos:,} != {dry.pos:,}")
    if len(real.buf) != real.pos:
        raise ModelError(f"缓冲区长度 {len(real.buf):,} != 游标 {real.pos:,}")
    if len(real.buf) < 8 or U32.unpack_from(real.buf, 4)[0] != len(real.buf) - 8:
        raise ModelError("FORM 长度字段与文件长度不符")

    built = Built(bytes(real.buf), dry.addrs, dry.spans, guards=dry.guards,
                  refs=real.refs)
    data.verify(built)
    return built


def build_bytes(data: DataFile) -> bytes:
    return build(data).data
