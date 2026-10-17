# -*- coding: utf-8 -*-
"""GameMaker data.win 的兜底抓取。

什么时候用
----------
UTMT 打不开目标文件的时候。UTMT 有自己的版本支持范围，遇到它不认识的
结构就直接抛异常、一个字节都不给你。

实测的一个例子（Downloads\\data.win）：容器本身完好，23 个 chunk 的链
一路走到底、长度字段全部自洽；但 FONT chunk 里 20 个指针中有 6 个指向
16.0~16.25MB —— 那 6 个字体条目确实物理上躺在字符串表区间里（它们开头
的字段是合法的字符串表指针，旁边还紧挨着 "fnt_papyrus"/"Papyrus" 这样
的字体名）。UTMT 因为「指针超出 chunk 边界」硬失败，清空 FONT 又会暴露
STRG 同样被污染。这种情况没法靠修补救回来。

做法
----
不解析容器，直接在字节流里扫内嵌资源，但**每种格式都按它自己的长度
字段走完并做结构校验**，宁可不收也不收垃圾：

    PNG   逐 chunk 走，必须 IHDR 开头 / IEND 结尾 / 尺寸合理
    WAV   RIFF+WAVE+fmt 子块，且长度字段与实际完全一致
    OGG   连续的 Ogg 页，页头结构必须合法

**不做 JPEG**。只按 SOI/EOI 硬切的话，在 64MB 的文件上能切出 2000 多个
垃圾片段，合计体积还超过文件本身（实测占 108.9%，物理上不可能），
而 GameMaker 的 data.win 里本来也不存 JPEG。

抓到的是**整张纹理页**（atlas），不是切好的单个精灵 —— 精灵坐标在 TPAG
chunk 里，而我们正是解析不了容器才走这条路。这一点会写进输出说明里。
"""

import mmap
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from me.collect import pngwrite   # noqa: F401  仅用于说明 PNG 是自己写的编码器


# 各格式的最小可信长度，挡掉明显的假阳性。
# 真正的把关靠下面的结构校验（PNG 要 chunk 链自洽、IHDR 开头 IEND 结尾；
# WAV 要长度字段和实际完全一致；OGG 要页头合法），这里只是防呆下限。
MIN_SIZE = {
    "png": 128,
    "wav": 64,
    "ogg": 256,
}

# 一次最多收多少个，防止在异常文件上失控
MAX_PER_KIND = 5000


@dataclass
class Found:
    """一个抓到的资源。"""
    kind: str          # png / wav / ogg
    start: int
    end: int
    meta: object = None

    @property
    def size(self) -> int:
        return self.end - self.start


# ------------------------------------------------------------------ 校验器
def _v_png(buf, s: int, e: int):
    """PNG：IHDR 开头、IEND 结尾、chunk 链自洽、尺寸合理。"""
    if buf[s:s + 8] != b"\x89PNG\r\n\x1a\n":
        return None
    off = s + 8
    first = None
    n = 0
    while off + 12 <= e:
        ln = struct.unpack_from(">I", buf, off)[0]
        typ = buf[off + 4:off + 8]
        if off + 12 + ln > e:
            return None
        # PNG 规范：chunk 类型是 4 个字母，首字母大小写表示 ancillary 位。
        # 所以 sRGB / gAMA / tEXt / pHYs 这些小写开头的是**合法**的 ——
        # 千万别要求全大写。踩过这个坑：加了 isupper() 之后，真实的纹理页
        # 因为带 sRGB 块被全部拒掉，结果一张图都抓不出来。
        if not typ.isalpha():
            return None
        if first is None:
            first = typ
        n += 1
        if typ == b"IEND":
            if off + 12 != e or first != b"IHDR":
                return None
            w, h = struct.unpack_from(">II", buf, s + 16)
            if not (0 < w <= 32768 and 0 < h <= 32768):
                return None
            return (w, h, n)
        off += 12 + ln
    return None


def _v_wav(buf, s: int, e: int):
    """WAV：RIFF/WAVE，fmt 子块参数合理，长度字段和实际一致。"""
    if buf[s:s + 4] != b"RIFF" or buf[s + 8:s + 12] != b"WAVE":
        return None
    if struct.unpack_from("<I", buf, s + 4)[0] + 8 != (e - s):
        return None
    off = s + 12
    fmt = None
    while off + 8 <= e:
        cid = buf[off:off + 4]
        ln = struct.unpack_from("<I", buf, off + 4)[0]
        if off + 8 + ln > e:
            return None
        if cid == b"fmt ":
            if ln < 16:
                return None
            tag, ch, rate = struct.unpack_from("<HHI", buf, off + 8)
            if tag not in (1, 3, 6, 7, 0xFFFE):
                return None
            if not (1 <= ch <= 8 and 1000 <= rate <= 384000):
                return None
            fmt = (tag, ch, rate)
        if cid == b"data" and fmt:
            return (fmt, ln)
        off += 8 + ln + (ln & 1)
    return None


def _v_ogg(buf, s: int, e: int):
    """OGG：连续的 Ogg 页，且正好铺满 [s, e)。"""
    off = s
    pages = 0
    while off + 27 <= e:
        if buf[off:off + 4] != b"OggS" or buf[off + 4] != 0:
            return None
        nseg = buf[off + 26]
        if off + 27 + nseg > e:
            return None
        body = 0
        for i in range(nseg):
            body += buf[off + 27 + i]
        off += 27 + nseg + body
        pages += 1
        if pages > 200000:      # 防止畸形数据把我们拖死
            return None
    return pages if off == e else None


# ------------------------------------------------------------------ 扫描
def _end_of_png(buf, i: int, n: int) -> Optional[int]:
    off = i + 8
    while off + 12 <= n:
        ln = struct.unpack_from(">I", buf, off)[0]
        if ln > n or off + 12 + ln > n:
            return None
        if buf[off + 4:off + 8] == b"IEND":
            return off + 12
        off += 12 + ln
    return None


def _end_of_wav(buf, i: int, n: int) -> Optional[int]:
    if buf[i + 8:i + 12] != b"WAVE":
        return None
    end = i + 8 + struct.unpack_from("<I", buf, i + 4)[0]
    return end if end <= n else None


def _end_of_ogg(buf, i: int, n: int) -> Optional[int]:
    off = i
    while off + 27 <= n and buf[off:off + 4] == b"OggS":
        nseg = buf[off + 26]
        if off + 27 + nseg > n:
            return None
        body = 0
        for k in range(nseg):
            body += buf[off + 27 + k]
        off += 27 + nseg + body
    return off if off > i else None


_SPECS = (
    ("png", b"\x89PNG\r\n\x1a\n", _end_of_png, _v_png),
    ("wav", b"RIFF", _end_of_wav, _v_wav),
    ("ogg", b"OggS", _end_of_ogg, _v_ogg),
)


def carve_bytes(buf, progress: Optional[Callable] = None) -> List[Found]:
    """在字节流里抓资源。buf 支持 bytes 或 mmap。"""
    n = len(buf)
    out: List[Found] = []
    for kind, sig, find_end, validate in _SPECS:
        hits = 0
        i = 0
        step = max(1, len(sig))
        while hits < MAX_PER_KIND:
            i = buf.find(sig, i)
            if i < 0:
                break
            end = find_end(buf, i, n)
            if end is not None and end - i >= MIN_SIZE[kind]:
                info = validate(buf, i, end)
                if info:
                    out.append(Found(kind, i, end, info))
                    hits += 1
                    i = end
                    continue
            i += step
        if progress:
            progress(hits, hits, kind)
    return out


def carve_file(path, progress: Optional[Callable] = None) -> List[Found]:
    """对文件做抓取。用 mmap，避免把几百 MB 整个读进内存。"""
    path = Path(path)
    with open(path, "rb") as f:
        size = path.stat().st_size
        if size == 0:
            return []
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            return carve_bytes(mm, progress)


def dedupe(buf, found: List[Found]) -> List[Found]:
    """按内容去重，保留第一次出现的位置。"""
    import hashlib
    seen = {}
    for f in found:
        h = hashlib.sha256(buf[f.start:f.end]).digest()
        if h not in seen:
            seen[h] = f
    return list(seen.values())


def summarize(found: List[Found]) -> str:
    """一行摘要，给输出说明用。"""
    from collections import Counter
    c = Counter(f.kind for f in found)
    if not c:
        return "没抓到任何可识别的资源"
    parts = [f"{k.upper()} {v}" for k, v in sorted(c.items(), key=lambda kv: -kv[1])]
    total = sum(f.size for f in found)
    return f"{' / '.join(parts)}，共 {len(found)} 个（{total / 1024 / 1024:.1f} MB）"
