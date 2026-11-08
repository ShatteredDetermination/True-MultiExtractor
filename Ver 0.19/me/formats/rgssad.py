# -*- coding: utf-8 -*-
"""RPG Maker 加密归档（RGSSAD / RGSS2A / RGSS3A）解析与解密。

格式依据（三条独立线索，结论一致的地方才写进代码）
--------------------------------------------------
1. mkxp-z（开源 RGSS 运行时，游戏实际跑在它上面）
   https://gitlab.com/mkxp-z/mkxp-z/-/blob/79fab330233b64fc19409d4a697e6bb504a31073/src/rgssad.cpp
   看 ``RGSS_openArchive``（v1/v2）与 ``RGSS3_openArchive``（v3）、``advanceMagic``、
   ``RGSS_ioRead``。
2. RPGMakerDecrypter（uuksu，C#，有真实样本回归测试）
   https://github.com/uuksu/RPGMakerDecrypter  →  ``RPGMakerDecrypter.RGSSAD/RGSSADv1.cs``、``RGSSADv3.cs``
3. fux2 的 RGSS3A 拆包算法说明（中文，2012）
   https://blog.csdn.net/fux3mario/article/details/7420231

和「凭记忆的常见说法」不一样、但被上面资料证实的关键点
------------------------------------------------------
* **八个字节的头**：``"RGSSAD\\x00" + 版本号``。v1=1，v3=3；VX 的 .rgss2a 里版本号
  也常写成 1（uuksu 的 RGSSADv1 同时接受 XP/VX），少数实现写 2（dec.py）。两种都收。
* **v1/v2 的 key 更新时机**：``nameLen``、每个文件名字节、``size``、数据正文
  用的都是「当前 key」，用完立刻 ``key = key * 7 + 3``。也就是说 **数据正文的第一
  个 4 字节用的是异或 size 之后那一轮的 key**，不再额外前进一次。
  （mkxp-z 存 ``entry.startMagic = magic`` 时 magic 已经是 size 之后的；uuksu 的
  ``archivedFile.Key = key`` 同理。dec.py 先前进再异或，与这两家差一轮，判定为它的 bug。）
* **v3 的名字不用滚动 key**：第 i 个字节异或的是 ``初始key*9+3`` 的第 ``i % 4``
  个字节（小端序），整个名字循环里 key 不变。
* **v3 的 key 更新只发生在数据正文**：正文每处理满 4 字节前进一次；尾部不足
  4 字节的余数用当前 key 的低字节异或，且不前进。v1/v2 同理（mkxp-z 的
  ``RGSS_ioRead`` 把 pre/postAlign 与对齐部分分开处理）。
* **v3 的表结束标记是「offset 字段为 0」**，不是靠文件尾。v1/v2 才是读到 EOF。
* **名字不以 \\0 结尾**，``nameLen`` 就是名字的实际字节数；路径分隔符是 ``\\``。
* 别被任务描述里的「v3 第三个字段应等于 0xDEADCAFE」带偏：实际格式里第三个字段
  是**该文件数据自己的起始 key**，各家实现都不校验它等于 0xDEADCAFE。
  ``0xDEADCAFE`` 只是 v1/v2 的固定初始 key，v3 的初始 key 存在文件头里。
  这里只把它当启发式线索用（见 ``guess_archive_type``）。

未做真实样本验证的部分见模块末尾 ``UNVERIFIED``。
"""

import struct
from pathlib import Path
from typing import List, NamedTuple, Optional

# 8 字节魔数：magic(6) + \0 + 版本号
MAGIC_AD = b"RGSSAD\x00"
MAGIC_2A = b"RGSS2A\x00"
MAGIC_3A = b"RGSS3A\x00"

# v1/v2 的固定初始 key
KEY_V1 = 0xDEADCAFE
# v3 头部里的「数据起始 key」在真实样本里通常是这个值，仅用于猜格式
KEY_MARKER = 0xDEADCAFE

# 读盘块大小；必须是 4 的倍数，才能保证数据正文的 key 不会跨块错位
_CHUNK = 1 << 20

UNVERIFIED = (
    "v1/v2/v3 的 key 前进时机、v3 表结束标记、名字是否含结尾 0 等细节均以公开实现"
    "（mkxp-z / RPGMakerDecrypter / fux2 说明）为准，但本仓库无真实 .rgssad 样本，"
    "往返测试只能证明加解密自洽，无法排除「实现之间互相抄错」的可能。"
)


class RgssadError(Exception):
    """归档结构异常（魔数不符、越界、截断等）。"""


class Entry(NamedTuple):
    """归档里的一个文件条目。"""
    name: str          # 归档内相对路径（已把 \\ 换成 /）
    size: int          # 明文长度
    offset: int        # 密文在文件里的起始偏移
    key: int           # 该条目数据正文的起始 key


# ------------------------------------------------------------------ 工具
def _advance(key: int) -> int:
    """滚动 key：key * 7 + 3，32 位回绕。"""
    return (key * 7 + 3) & 0xFFFFFFFF


def _decode_name(raw: bytes) -> str:
    """归档里的名字按 UTF-8 存（日文游戏也多是 UTF-8），坏了再退 cp932。"""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp932", "replace")
    # 归档内部用反斜杠分隔，统一成正斜杠；开头的斜杠会让写出路径变成绝对路径
    return text.replace("\\", "/").lstrip("/")


def guess_archive_type(path) -> str:
    """按 8 字节魔数猜类型，返回 ``rgssad`` / ``rgss2a`` / ``rgss3a`` / ``""``。

    只看扩展名不够可靠（有人把 .rgss3a 改名成 .rgssad），所以数据优先。

    **真实归档的魔数一律是 ``"RGSSAD\\x00"``，第 8 字节才是版本号**：
    ``1`` = v1 格式（XP / VX 共用），``3`` = v3 格式（VX Ace）。
    这一点是拿真实样本验证出来的 —— RPGMakerDecrypter 仓库里的
    ``Game.rgss2a`` 和 ``Game.rgss3a`` 头部都是 ``52 47 53 53 41 44 00 01/03``，
    并不是字面的 ``RGSS2A`` / ``RGSS3A``。
    （早期版本这里要求字面 ``RGSS3A``，结果真实 .rgss3a 一个都认不出来。）
    """
    path = Path(path)
    with open(path, "rb") as f:
        head = f.read(8)
    if len(head) < 8:
        return ""

    body, version = head[:7], head[7]
    ext = path.suffix.lower()

    # 主流实现（RPG Maker 自身、mkxp-z、RPGMakerDecrypter）都走这条
    if body == MAGIC_AD[:7]:
        if version == 3:
            return "rgss3a"
        if version in (1, 2):
            # v1 格式 XP 和 VX 共用，靠扩展名给个更准确的标签（只影响显示）
            return "rgss2a" if ext == ".rgss2a" else "rgssad"
        return ""

    # 容错：少数第三方打包工具会把魔数写成字面的 RGSS2A / RGSS3A
    if body == MAGIC_3A[:7] and version == 3:
        return "rgss3a"
    if body == MAGIC_2A[:7] and version in (1, 2):
        return "rgss2a"
    return ""


def format_label(kind: str) -> str:
    """类型 -> 给人看的名字。"""
    return {
        "rgssad": "RGSSAD (RPG Maker XP)",
        "rgss2a": "RGSS2A (RPG Maker VX)",
        "rgss3a": "RGSS3A (RPG Maker VX Ace)",
    }.get(kind, kind or "未知归档")


# ------------------------------------------------------------------ 解析
def _read_exact(f, n: int) -> bytes:
    data = f.read(n)
    if len(data) != n:
        raise RgssadError(f"文件被截断: 还差 {n - len(data)} 字节")
    return data


def _entries_v1(f, total: int) -> List[Entry]:
    """v1 / v2：从偏移 8 开始读到文件尾。"""
    entries: List[Entry] = []
    key = KEY_V1
    while f.tell() < total:
        head = f.read(4)
        if len(head) < 4:
            break  # 干净的结尾，没有多余的半截字段
        name_len = struct.unpack("<I", head)[0] ^ key
        key = _advance(key)
        if name_len > total:
            raise RgssadError(f"文件名长度异常: {name_len}")

        raw = bytearray(_read_exact(f, name_len))
        for i in range(name_len):
            raw[i] ^= key & 0xFF
            key = _advance(key)

        size = struct.unpack("<I", _read_exact(f, 4))[0] ^ key
        key = _advance(key)   # 这一步之后就轮到数据正文用 key 了

        offset = f.tell()
        if offset + size > total:
            raise RgssadError(
                f"{_decode_name(bytes(raw))}: 声明 {size} 字节，超出文件末尾")
        entries.append(Entry(_decode_name(bytes(raw)), size, offset, key))

        # 关键：正文不参与表 key 的递推，直接 seek 过去就行。
        #
        # 正文确实也用「每 4 字节让 key 前进一次」的节奏，但那是**条目自己的
        # key 副本**（见 _decrypt_chunk 的 tempKey），不会回灌到这张表的 key。
        # 这里如果「顺手」把 key 也走一遍，第一条之后的 nameLen 就会读到垃圾。
        #
        # 依据：RPGMakerDecrypter.RGSSAD/RGSSADv1.cs 的 ReadRGSSAD()——
        # 读完 Size 后只做 BaseStream.Seek(Size, Current)，没有动 key。
        # 这个坑往返测试查不出来（生成器和解析器会共享同一个错误理解），
        # 只能靠对公开实现。
        f.seek(offset + size)
    return entries


def _entries_v3(f, total: int) -> List[Entry]:
    """v3：表在文件头，靠 offset == 0 结束；表里记录每个文件的绝对偏移。"""
    base_key = (struct.unpack("<I", _read_exact(f, 4))[0] * 9 + 3) & 0xFFFFFFFF
    entries: List[Entry] = []
    while True:
        head = f.read(16)
        if len(head) < 16:
            break
        offset, size, magic, name_len = struct.unpack("<IIII", head)
        offset ^= base_key
        if offset == 0:
            break
        size ^= base_key
        magic ^= base_key
        name_len ^= base_key
        if name_len > total:
            raise RgssadError(f"文件名长度异常: {name_len}")

        raw = bytearray(_read_exact(f, name_len))
        key_bytes = struct.pack("<I", base_key)   # 名字用固定 key 的字节循环异或
        for i in range(name_len):
            raw[i] ^= key_bytes[i % 4]

        if offset + size > total:
            raise RgssadError(
                f"{_decode_name(bytes(raw))}: 偏移 {offset} + {size} 超出文件末尾")
        entries.append(Entry(_decode_name(bytes(raw)), size, offset, magic))
    return entries


def read_entries(path) -> List[Entry]:
    """只读目录表，不解密正文。"""
    path = Path(path)
    kind = guess_archive_type(path)
    if not kind:
        raise RgssadError("不是 RGSS 归档（8 字节魔数不匹配）")
    total = path.stat().st_size
    with open(path, "rb") as f:
        f.seek(8)
        if kind == "rgss3a":
            return _entries_v3(f, total)
        return _entries_v1(f, total)


def summarize(path):
    """返回 (条目数, 明文总字节)。"""
    entries = read_entries(path)
    return len(entries), sum(e.size for e in entries)


# ------------------------------------------------------------------ 正文
def _decrypt_chunk(chunk: bytes, key: int) -> "tuple":
    """解密 4 字节对齐的一块数据，返回 (明文, 新的 key)。

    调用方必须保证除最后一块外，每块长度都是 4 的倍数——key 是按 4 字节前进的，
    跨块时才能对得上。
    """
    out = bytearray(chunk)
    n = len(chunk)
    aligned = n - (n % 4)
    for i in range(0, aligned, 4):
        val = struct.unpack_from("<I", out, i)[0] ^ key
        struct.pack_into("<I", out, i, val)
        key = _advance(key)
    if aligned < n:
        # 尾部不足 4 字节：只异或当前 key 的低字节，且不前进
        low = struct.pack("<I", key)
        for i in range(aligned, n):
            out[i] ^= low[i % 4]
    return bytes(out), key


def iter_data(path, entry: Entry):
    """流式产出某个条目的明文（大文件不会整个读进内存）。"""
    path = Path(path)
    with open(path, "rb") as f:
        f.seek(entry.offset)
        key = entry.key
        left = entry.size
        while left > 0:
            want = min(_CHUNK, left)
            chunk = f.read(want)
            if not chunk:
                break  # 归档被截断，有多少给多少
            plain, key = _decrypt_chunk(chunk, key)
            left -= len(chunk)
            yield plain


def read_data(path, entry: Entry) -> bytes:
    """读出某个条目的完整明文。"""
    return b"".join(iter_data(path, entry))


def extract(path, writer, origin: str = "", progress=None) -> int:
    """把归档里的文件解密后写进 writer，返回写出数量。

    writer 是 me.collect.writer.OutputWriter。
    """
    from me.collect import kinds

    path = Path(path)
    entries = read_entries(path)
    total = len(entries)
    written = 0
    for i, entry in enumerate(entries):
        if progress:
            progress(i + 1, total, (entry.name or "?")[-32:])
        if not entry.name:
            continue   # 空名字没法写盘，跳过
        if not writer.wants(kinds.classify(entry.name)):
            continue   # 提前跳过，省掉解密开销
        data = read_data(path, entry)
        if writer.write_bytes(kinds.classify(entry.name), entry.name, data,
                              origin or str(path)):
            written += 1
    if progress:
        progress(total, total, "完成")
    return written


def manifest_lines(path, entries: Optional[List[Entry]] = None) -> List[str]:
    """生成归档清单行：``相对路径<TAB>大小``。"""
    if entries is None:
        entries = read_entries(path)
    return [f"{e.name}\t{e.size}" for e in entries]
