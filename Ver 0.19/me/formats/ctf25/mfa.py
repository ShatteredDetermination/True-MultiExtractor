# -*- coding: utf-8 -*-
"""MFA（Fusion 2.5 工程文件）读写的地基 —— V0.19 起。

V0.19 的目标是「从成品反编译出 .mfa」。成品里那份 CCN 数据流（PAMU）是**编译后**
的形态，MFA 是**编辑器**的形态，两者不是同一个容器：

    MFA = MFU2 头 + 工程字符串 + 缩略图 + 四个资源库 + 应用设置
          + 全局值/字符串/事件 + 图形模式 + 图标句柄 + 限定符 + 扩展
          + 帧偏移表 + 各帧 + 帧后的对象 chunk 列表

    CCN = PAMU 头 + 一长串 chunk（0x2223 起），帧是嵌套的 0x3333 容器

所以「导出 mfa」不是给 CCN 换个壳，而是按上面的顺序重新组一份工程文件。
本模块先只落地**已用真文件逐字节验证过的前段**，后面的段落各有一个入口，
没验证过的一律不写，免得写出「看起来像 mfa」的坏文件。

前段布局（实测）::

    4B   'MFU2'
    u16  版本（样本：6 / 7）
    u16  子版本（0）
    u32  编辑器版本（3）
    u32  编辑器构建号（292）
    u32  语言 ID（1033 = 0x0409）
    ...  CString 工程名
    ...  CString 工程描述（样本里是空串）
    ...  CString 编辑器内的文件路径
    u32  缩略图字节数 + 缩略图（样本：6196 B，64x48 预览）
    4B   'ATNF' 字体库 + 内容
    4B   'APMS' 音频库 + 内容
    4B   'ASUM' 音乐库 + 内容
    4B   'AGMI' 图标库 + 内容
    4B   'AGMI' 图像库 + 内容
    ...  应用名 / 作者 / 描述 / 版权 / 公司 / 版本 / 窗口尺寸 ...

CString 编码（实测）：u32 长度，最高位 ``0x80000000`` 表示 UTF-16LE，其余位是字符数；
空串就是 ``00 00 00 80`` 四个字节。

实测样本（公开示例工程，2026-10-18 核对）：

    Template.mfa          193,891 B  MFU2 v6  build 292  工程名 'Template'
    Platformer Engine.mfa 423,160 B  MFU2 v7  build 292  工程名 'Platformer Engine'

后续要补的三块（都还没落地，别在这儿假装读懂）：
  * 四个库的条目布局（图像条目 NebulaFD 也没实现，需要自己从真文件推）
  * 应用设置之后的全局值 / 全局字符串 / 全局事件
  * 帧（绝对偏移表指向）与帧后的对象 chunk 列表（u8 id + i32 size，id=0 收尾）
"""

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

MAGIC = b"MFU2"
TAG_FONTS = b"ATNF"
TAG_SOUNDS = b"APMS"
TAG_MUSIC = b"ASUM"
TAG_ICONS = b"AGMI"
TAG_IMAGES = b"AGMI"
BANK_TAGS = (TAG_FONTS, TAG_SOUNDS, TAG_MUSIC, TAG_ICONS, TAG_IMAGES)

UNICODE_FLAG = 0x80000000
MAX_STRING = 1 << 20


class MfaError(Exception):
    """MFA 结构不符合预期。"""


class Reader:
    def __init__(self, buf: bytes, pos: int = 0):
        self.buf = buf
        self.pos = pos

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.buf):
            raise MfaError(f"越界读取 {n} 字节 @0x{self.pos:X}")
        out = self.buf[self.pos:self.pos + n]
        self.pos += n
        return out

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def tag(self) -> bytes:
        return self.take(4)

    def cstring(self) -> str:
        raw_len = self.u32()
        uni = bool(raw_len & UNICODE_FLAG)
        n = raw_len & 0x7FFFFFFF
        if n > MAX_STRING:
            raise MfaError(f"字符串长度异常 {n} @0x{self.pos - 4:X}")
        data = self.take(n * (2 if uni else 1))
        return data.decode("utf-16-le" if uni else "latin-1", "replace")


class Writer:
    def __init__(self):
        self.parts: List[bytes] = []

    def raw(self, data: bytes) -> None:
        self.parts.append(bytes(data))

    def u16(self, v: int) -> None:
        self.parts.append(struct.pack("<H", v & 0xFFFF))

    def u32(self, v: int) -> None:
        self.parts.append(struct.pack("<I", v & 0xFFFFFFFF))

    def i32(self, v: int) -> None:
        self.parts.append(struct.pack("<i", v))

    def cstring(self, text: str) -> None:
        data = text.encode("utf-16-le")
        self.u32(UNICODE_FLAG | (len(data) // 2))
        self.raw(data)

    def getvalue(self) -> bytes:
        return b"".join(self.parts)


@dataclass
class MfaPreamble:
    """MFA 前段：头 + 三个工程字符串 + 缩略图 + 五个库标记（库内容原样保留）。"""
    version: int = 7
    subversion: int = 0
    editor_version: int = 3
    editor_build: int = 292
    language: int = 1033
    app_name: str = ""
    description: str = ""
    editor_path: str = ""
    thumbnail: bytes = b""
    banks_raw: bytes = b""       # 'ATNF' 起的全部字节，等库解析落地后再切开

    @property
    def header_size(self) -> int:
        return 4 + 2 + 2 + 4 + 4 + 4


def read_preamble(buf: bytes) -> MfaPreamble:
    """读 MFA 前段。

    库内容整段保留（``banks_raw``），因为库条目布局还没验证过 —— 这里只断言
    第一个库标记是 ``ATNF``，用来证明「字符串 + 缩略图」的长度算对了。
    """
    r = Reader(buf)
    if r.tag() != MAGIC:
        raise MfaError("不是 MFU2 文件")
    p = MfaPreamble(
        version=r.u16(),
        subversion=r.u16(),
        editor_version=r.u32(),
        editor_build=r.u32(),
        language=r.u32(),
    )
    p.app_name = r.cstring()
    p.description = r.cstring()
    p.editor_path = r.cstring()
    n = r.i32()
    if n < 0 or n > len(buf):
        raise MfaError(f"缩略图长度异常 {n}")
    p.thumbnail = r.take(n)
    rest = buf[r.pos:]
    if rest[:4] != TAG_FONTS:
        raise MfaError(
            f"库标记应为 ATNF，读到 {rest[:4]!r} @0x{r.pos:X}")
    p.banks_raw = rest
    return p


def build_preamble(p: MfaPreamble) -> bytes:
    """把前段写回字节流；``build_preamble(read_preamble(b)) == b[:len]``。"""
    w = Writer()
    w.raw(MAGIC)
    w.u16(p.version)
    w.u16(p.subversion)
    w.u32(p.editor_version)
    w.u32(p.editor_build)
    w.u32(p.language)
    w.cstring(p.app_name)
    w.cstring(p.description)
    w.cstring(p.editor_path)
    w.i32(len(p.thumbnail))
    w.raw(p.thumbnail)
    w.raw(p.banks_raw)
    return w.getvalue()


def describe(p: MfaPreamble) -> str:
    return (f"MFU2 v{p.version}（子 {p.subversion}）编辑器 {p.editor_version}"
            f"/{p.editor_build} 语言 {p.language} 工程名 {p.app_name!r} "
            f"缩略图 {len(p.thumbnail)} B 库数据 {len(p.banks_raw)} B")


def read_file(path) -> MfaPreamble:
    p = Path(path)
    return read_preamble(p.read_bytes())
