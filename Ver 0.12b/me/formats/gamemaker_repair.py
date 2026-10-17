# -*- coding: utf-8 -*-
"""试着把 UTMT 读不了的 GameMaker data.win「修」到能读。

思路
----
GameMaker 的 data.win 是一串顺序 chunk：``<名字4字节><u32长度><数据>``，
第一个 chunk（GEN8）比较特殊，它的长度写在偏移 12 处。

UTMT 读某个 chunk 时抛异常，就说明**那一个 chunk**它消化不了。既然
chunk 链本身是自洽的（长度字段能把整条链走到文件末尾），我们就可以
把出问题的那个 chunk 换成「空列表」，让 UTMT 跳过它继续往下读 ——
实际用起来，精灵和音频通常都在别的 chunk 里，照样能拆出来。

两条铁律
--------
1. **长度必须严格不变。** 这个文件里存着绝对文件偏移（chunk 内部的指针
   数组、字符串表偏移都是文件绝对位置）。chunk 一旦变短，后面所有偏移
   全部错位，本来能读的 chunk 也会跟着坏。实测过：把 FONT 从 149,296
   压成 4 字节，UTMT 立刻从「读不了 FONT」变成「读不了 STRG」。
   所以清空 = 写个 count=0，剩下原地填 0，长度一个字节不动。

2. **只清空「列表型」chunk。** 非列表型的 chunk（GEN8/OPTN/LANG/EXTN…）
   塞个 count=0 只会更糟。
"""

import struct
from pathlib import Path
from typing import List, Optional, Tuple

# 可以安全清空的列表型 chunk（内容 = count + 指针数组 + 条目）
REPAIRABLE = frozenset({
    "SOND", "SPRT", "BGND", "PATH", "SCPT", "TMLN", "OBJT", "ROOM",
    "FONT", "TPAG", "CODE", "VARI", "FUNC", "STRG", "TXTR", "AUDO",
    "AGRP", "EMBI", "SEQN", "PSEM", "TGIN", "LANG",
})

MAGIC_FORM = b"FORM"
MAGIC_GEN8 = b"GEN8"


class NotGamemaker(Exception):
    """不是 FORM/GEN8 容器。"""


def is_container(head: bytes) -> bool:
    """看开头判断是不是 GameMaker 的 FORM/GEN8 容器。"""
    return len(head) >= 12 and head[:4] == MAGIC_FORM and head[8:12] == MAGIC_GEN8


def walk_chunks(data) -> List[Tuple[str, int, int, int]]:
    """走出 chunk 表，返回 [(名字, 偏移, 长度, 结束), ...]。

    偏移指向名字本身（不是数据）。走不通就返回已走到的部分。
    """
    if not is_container(data[:12]):
        raise NotGamemaker("开头不是 FORM/GEN8")
    total = len(data)
    gen8_len = struct.unpack_from("<I", data, 12)[0]
    off = 16 + gen8_len
    out = []
    while off + 8 <= total:
        name = data[off:off + 4]
        if not all(65 <= c <= 90 or 48 <= c <= 57 for c in name):
            break
        ln = struct.unpack_from("<I", data, off + 4)[0]
        end = off + 8 + ln
        if end > total:
            break
        out.append((name.decode("ascii"), off, ln, end))
        off = end
    return out


def verify(data) -> Tuple[bool, str]:
    """chunk 链是否一路走到文件末尾（说明容器本身没坏）。"""
    try:
        chunks = walk_chunks(data)
    except NotGamemaker as exc:
        return False, str(exc)
    if not chunks:
        return False, "走不出任何 chunk"
    end = chunks[-1][3]
    if end != len(data):
        return False, f"链停在 {end:,}，文件是 {len(data):,}"
    return True, f"{len(chunks)} 个 chunk，链完整"


def neutralize(data: bytes, names) -> Tuple[bytes, List[str]]:
    """把指定 chunk 清成空列表，**长度严格不变**。

    返回 (新内容, 实际清空了哪些)。
    """
    names = {n.upper() for n in names}
    chunks = walk_chunks(data)
    gen8_len = struct.unpack_from("<I", data, 12)[0]

    out = bytearray()
    out += MAGIC_FORM + b"\x00\x00\x00\x00" + MAGIC_GEN8
    out += struct.pack("<I", gen8_len)
    out += data[16:16 + gen8_len]

    done = []
    for name, off, ln, end in chunks:
        body = bytearray(data[off + 8:end])
        if name in names and ln >= 4:
            # count = 0，其余原地填 0 —— 长度不变，绝对偏移全部保持有效
            body = bytearray(ln)
            done.append(name)
        out += name.encode("ascii") + struct.pack("<I", len(body)) + body

    struct.pack_into("<I", out, 4, len(out) - 8)
    if len(out) != len(data):
        # 理论上不可能，但真发生的话绝对不能拿去用
        raise AssertionError(
            f"修补后长度变了（{len(out)} != {len(data)}），绝对偏移会错位")
    return bytes(out), done


def pick_target(data: bytes, names) -> Optional[str]:
    """从候选里挑一个真的能清、且当前非空的 chunk 名。"""
    chunks = {c[0]: c[2] for c in walk_chunks(data)}
    for n in names:
        n = n.upper()
        if n in chunks and n in REPAIRABLE and chunks[n] > 4:
            return n
    return None


# UTMT 报错里会出现 chunk 名，尽量都认出来
_CHUNK_ERROR_PATTERNS = (
    "in chunk ",
    "while reading chunk ",
    "Padding error in ",
    "Error in chunk ",
)


def failing_chunks(output: str) -> List[str]:
    """从 UTMT 的输出里抠出它读失败的 chunk 名（可能有多个）。"""
    found: List[str] = []
    for line in output.splitlines():
        for pat in _CHUNK_ERROR_PATTERNS:
            idx = line.find(pat)
            if idx < 0:
                continue
            cand = line[idx + len(pat):idx + len(pat) + 4]
            if len(cand) == 4 and all(65 <= ord(c) <= 90 or 48 <= ord(c) <= 57
                                      for c in cand):
                if cand not in found:
                    found.append(cand)
    return found


def write_repaired(data: bytes, dest: Path) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest
