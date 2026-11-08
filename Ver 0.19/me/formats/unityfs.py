# -*- coding: utf-8 -*-
"""UnityFS 包读取（自研，V0.17）。

Unity 打包出来的一堆 `*.assets` / `globalgamemanagers` / `.resS` 通常装在一个
`.unity3d`（或 `.bundle`）里，就是 UnityFS 包。自研通道只做「把包拆开、
把里面每个文件原样落盘」，不碰资源对象 —— 那是 SerializedFile 的事。

包结构（实测两份 Steam 游戏样本，规格数字都对得上）::

    magic          "UnityFS\\0"（8 字节）
    version        u32 大端（实测 8）
    unity_version  以 NUL 结尾的字符串，实测是 "5.x.x"
    unity_revision 以 NUL 结尾的字符串，实测如 "2022.3.55f1c1"
    size           i64 大端（等于文件大小）
    cbinfo_size    u32 大端（块信息压缩后长度）
    ubinfo_size    u32 大端（块信息解压后长度）
    flags          u32 大端（低 6 位 = 压缩方式：0 不压 / 1 LZMA / 2 LZ4 / 3 LZ4HC）
    [对齐到 16]
    块信息        压缩过的，就地解压成长度 ubinfo_size 的字节

块信息解开之后（全部**大端**）::

    16 字节         全零（块信息本身的校验位，实测两份包都是 16 个 0）
    u32 block_count
    block_count × (u32 未压缩长度, u32 压缩长度, u16 flags)
    u32 node_count
    node_count × (i64 偏移, i64 大小, u32 flags, 名字 + NUL)   ← 名字后不补齐

所有块按顺序解压并首尾相接，就是「载荷」；node 的偏移/大小是载荷内的位置。

几条写错就全错的细节（都是踩出来的）：
  * 块信息和块表都是**大端**，而 LZ4 的偏移字段是流内的（与字节序无关）。
  * 块信息在**头部对齐之后**，不是文件尾 —— flags 的 0x40 位不能按字面理解。
  * 名字后面**没有** 4 字节对齐；按 len+1 直接接下一个 node 的 20 字节。
  * 块信息开头那 16 个零要跳过，块数从第 16 字节开始数。
"""

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from me.formats import lz4_block

MAGIC = b"UnityFS\x00"
ALIGN = 16
# 块信息开头那 16 字节（实测全零）
INFO_PROLOGUE = 16
# 压缩方式
COMP_NONE, COMP_LZMA, COMP_LZ4, COMP_LZ4HC = 0, 1, 2, 3
COMP_NAMES = {0: "none", 1: "lzma", 2: "lz4", 3: "lz4hc"}
# flags 里取压缩方式的位
COMP_MASK = 0x3F

MAX_BLOCKS = 200_000
MAX_NODES = 200_000


class UnityFsError(Exception):
    """不是 UnityFS 包，或结构与本模块已知的版本不符。"""


@dataclass
class Block:
    uncompressed: int
    compressed: int
    flags: int
    offset: int = 0          # 压缩数据在文件里的位置


@dataclass
class Node:
    offset: int
    size: int
    flags: int
    name: str

    @property
    def is_serialized(self) -> bool:
        return self.flags & 4 == 0


@dataclass
class Bundle:
    path: Path
    version: int
    unity_version: str
    unity_revision: str
    flags: int
    header_size: int          # 块信息开始的位置
    data_offset: int          # 第一个块的数据位置
    blocks: List[Block] = field(default_factory=list)
    nodes: List[Node] = field(default_factory=list)

    @property
    def compression(self) -> str:
        return COMP_NAMES.get(self.flags & COMP_MASK, f"#{self.flags & COMP_MASK}")

    @property
    def payload_size(self) -> int:
        return sum(b.uncompressed for b in self.blocks)


# ---------------------------------------------------------------- 头
def _read_cstr(buf: bytes, pos: int) -> Tuple[str, int]:
    end = buf.find(b"\x00", pos)
    if end < 0:
        raise UnityFsError(f"偏移 {pos} 处的字符串没有结尾")
    return buf[pos:end].decode("utf-8", "replace"), end + 1


def read_header(path) -> Bundle:
    path = Path(path)
    with path.open("rb") as f:
        head = f.read(64)
        if len(head) < 32:
            raise UnityFsError("文件太短")
        if head[:8] != MAGIC:
            raise UnityFsError("不是 UnityFS 包（缺 UnityFS 魔数）")
        version, = struct.unpack_from(">I", head, 8)
        if not (1 <= version <= 8):
            raise UnityFsError(f"版本 {version} 不认识")
        pos = 12
        unity_version, pos = _read_cstr(head, pos)
        unity_revision, pos = _read_cstr(head, pos)
        if pos + 20 > len(head):
            raise UnityFsError("头部字段越界")
        size, = struct.unpack_from(">q", head, pos)
        cbinfo, = struct.unpack_from(">I", head, pos + 8)
        ubinfo, = struct.unpack_from(">I", head, pos + 12)
        flags, = struct.unpack_from(">I", head, pos + 16)
        pos += 20

        info_start = pos + (ALIGN - pos % ALIGN) % ALIGN
        bundle = Bundle(path=path, version=version,
                        unity_version=unity_version,
                        unity_revision=unity_revision,
                        flags=flags, header_size=info_start,
                        data_offset=info_start + cbinfo)
        bundle._size = size            # type: ignore[attr-defined]
        bundle._cbinfo = cbinfo        # type: ignore[attr-defined]
        bundle._ubinfo = ubinfo        # type: ignore[attr-defined]
        return bundle


# ---------------------------------------------------------------- 块信息
def _decompress(method: int, raw: bytes, expected: int = 0) -> bytes:
    try:
        return _decompress_inner(method, raw, expected)
    except UnityFsError:
        raise
    except Exception as exc:                    # noqa: BLE001 - 统一成格式错误
        raise UnityFsError(
            f"块解压失败（{COMP_NAMES.get(method, method)}）: "
            f"{type(exc).__name__}: {exc}") from exc


def _decompress_inner(method: int, raw: bytes, expected: int = 0) -> bytes:
    if method == COMP_NONE:
        out = raw
    elif method == COMP_LZMA:
        import lzma
        out = lzma.decompress(raw)
    elif method in (COMP_LZ4, COMP_LZ4HC):
        out = lz4_block.decompress_block(raw, expected)
    else:
        raise UnityFsError(f"压缩方式 {method} 不认识")
    # 注意：_decompress 内部抛的 Lz4Error / LZMAError 一律转成 UnityFsError，
    # 否则上层（引擎）只 catch UnityFsError，异常会漏到通用兜底里丢掉 notes。
    if expected and len(out) != expected:
        raise UnityFsError(f"解出 {len(out)} 字节，期望 {expected}")
    return out


def read_blocks(bundle: Bundle) -> Bundle:
    """读块表 + 目录，填好 blocks / nodes。"""
    cbinfo = bundle._cbinfo          # type: ignore[attr-defined]
    ubinfo = bundle._ubinfo          # type: ignore[attr-defined]
    with bundle.path.open("rb") as f:
        f.seek(bundle.header_size)
        raw = f.read(cbinfo)
    if len(raw) != cbinfo:
        raise UnityFsError("块信息读不全（文件被截断？）")
    info = _decompress(bundle.flags & COMP_MASK, raw, ubinfo)

    if len(info) < INFO_PROLOGUE + 4:
        raise UnityFsError("块信息太短")
    count, = struct.unpack_from(">I", info, INFO_PROLOGUE)
    if not (0 < count <= MAX_BLOCKS):
        raise UnityFsError(f"块数异常: {count}")
    pos = INFO_PROLOGUE + 4
    blocks: List[Block] = []
    cursor = bundle.data_offset
    for _ in range(count):
        if pos + 10 > len(info):
            raise UnityFsError("块表越界")
        unc, comp, fl = struct.unpack_from(">IIH", info, pos)
        pos += 10
        if unc == 0 or comp > unc or fl > 8:
            raise UnityFsError(f"块数据不合法（unc={unc} comp={comp} flags={fl}）")
        blocks.append(Block(uncompressed=unc, compressed=comp, flags=fl,
                            offset=cursor))
        cursor += comp

    if pos + 4 > len(info):
        raise UnityFsError("缺 node 数量")
    ncount, = struct.unpack_from(">I", info, pos)
    pos += 4
    if not (0 < ncount <= MAX_NODES):
        raise UnityFsError(f"文件数异常: {ncount}")
    nodes: List[Node] = []
    for _ in range(ncount):
        if pos + 20 > len(info):
            raise UnityFsError("目录越界")
        off, size, fl = struct.unpack_from(">qqI", info, pos)
        pos += 20
        end = info.find(b"\x00", pos)
        if end < 0:
            raise UnityFsError("文件名没有结尾")
        name = info[pos:end].decode("utf-8", "replace")
        pos = end + 1                       # 名字后面不补齐
        if off < 0 or size < 0:
            raise UnityFsError(f"{name}: 偏移/大小是负数")
        nodes.append(Node(offset=off, size=size, flags=fl, name=name))

    total = sum(b.uncompressed for b in blocks)
    for n in nodes:
        if n.offset + n.size > total:
            raise UnityFsError(
                f"{n.name}: 越出载荷（{n.offset}+{n.size} > {total}）")

    bundle.blocks = blocks
    bundle.nodes = nodes
    return bundle


def read_bundle(path) -> Bundle:
    return read_blocks(read_header(path))


# ---------------------------------------------------------------- 提取
@dataclass
class ExtractReport:
    written: int = 0
    total: int = 0
    bytes: int = 0
    warnings: List[str] = field(default_factory=list)


def iter_payload(bundle: Bundle):
    """逐块解压载荷，产出 (载荷偏移, 字节)。"""
    pos = 0
    with bundle.path.open("rb") as f:
        for b in bundle.blocks:
            f.seek(b.offset)
            raw = f.read(b.compressed)
            if len(raw) != b.compressed:
                raise UnityFsError(f"块 @{b.offset} 读不全")
            data = _decompress(b.flags & COMP_MASK or (bundle.flags & COMP_MASK),
                               raw, b.uncompressed)
            yield pos, data
            pos += len(data)


def extract_to(path, dest, progress: Optional[Callable] = None,
               names_only: Optional[set] = None) -> ExtractReport:
    """把包里的 node 按名字原样写到 dest 目录。"""
    bundle = read_bundle(path)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    rep = ExtractReport(total=len(bundle.nodes))

    wanted = {}
    for n in bundle.nodes:
        if names_only is not None and n.name not in names_only:
            continue
        safe = Path(n.name.replace("\\", "/"))
        target = dest / safe
        try:
            target.resolve().relative_to(dest.resolve())
        except ValueError:
            rep.warnings.append(f"跳过越界路径: {n.name}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        wanted[n.name] = {"node": n, "target": target, "written": 0,
                          "handle": None}

    done = 0
    for base, data in iter_payload(bundle):
        end = base + len(data)
        for name, info in wanted.items():
            n = info["node"]
            lo, hi = max(base, n.offset), min(end, n.offset + n.size)
            if lo >= hi:
                continue
            chunk = data[lo - base:hi - base]
            if info["handle"] is None:
                info["handle"] = open(info["target"], "wb")
            info["handle"].write(chunk)
            info["written"] += len(chunk)
            if info["written"] == n.size:
                info["handle"].close()
                info["handle"] = None
                rep.written += 1
                rep.bytes += n.size
                done += 1
                if progress:
                    progress(done, rep.total, name[-32:])

    for name, info in wanted.items():
        if info["handle"] is not None:
            info["handle"].close()
            rep.warnings.append(f"{name}: 只写出 {info['written']} 字节")

    return rep


def describe(bundle: Bundle) -> str:
    return (f"{bundle.path.name}: UnityFS v{bundle.version}"
            f"（Unity {bundle.unity_revision or bundle.unity_version}），"
            f"{len(bundle.blocks)} 块 / {len(bundle.nodes)} 个文件 / "
            f"载荷 {bundle.payload_size / 1024 / 1024:.1f} MB，"
            f"压缩 {bundle.compression}")
