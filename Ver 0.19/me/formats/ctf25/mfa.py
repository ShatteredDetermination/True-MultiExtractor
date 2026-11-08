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

后续要补的两块（都还没落地，别在这儿假装读懂）：
  * **图像条目布局**：库头（graphicMode / 调色板 / 图像数）已经读得出来，
    但条目字段宽度不是 4 字节对齐的（真样本 0x1CFA 起：00 00 00 00 /
    63 92 00 00 / 01 00 00 00 / 00 4F 09 00 / 00 30 00 30 00 …），
    照 CCN 的 2.5+ 布局去读会得到「6151x0」这种荒谬尺寸；
  * 应用设置之后的全局值 / 全局字符串 / 全局事件、帧（绝对偏移表指向）、
    帧后的对象 chunk 列表（u8 id + i32 size，id=0 收尾）。
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


# ------------------------------------------------------------------ 四个资源库
# 布局依据：真样本逐字段对齐（Template v6 / Platformer v7），不是猜的。
#
# 字体条目（实测偏移，Platformer 的 "Free Pixel"）::
#
#     u32 handle / i32 checksum / u32 references / i32 dataSize
#     i32 height(负=字号) i32 width i32 escapement i32 orientation i32 weight
#     u8 italic / u8 underline / u8 strikeOut / u8 charSet
#     u8 outPrecision / u8 clipPrecision / u8 quality / u8 pitchAndFamily
#     32 个 UTF-16 字符的名字（64 字节，定长，不带头）
#     dataSize 字节的字体数据（内嵌 TTF 时才有）
#
# 图像库头（图标库与图像库同一种）::
#
#     u32 graphicMode / u16 paletteVersion / u16 paletteEntries
#     paletteEntries × 4 字节调色板
#     u32 图像数
#
# **图像条目本身还没拿下**：真样本里条目的字段宽度不是 4 字节对齐的
# （0x1CFA 起 00 00 00 00 / 63 92 00 00 / 01 00 00 00 / 00 4F 09 00 /
#  00 30 00 30 00 …），照 CCN 的 2.5+ 布局去读会得到「6151x0」这种荒谬尺寸。
# 所以这里只解析库头，条目按原始字节留着，等把布局推出来再补。

FONT_NAME_CHARS = 32


@dataclass
class FontEntry:
    handle: int = 0
    checksum: int = 0
    references: int = 0
    data_size: int = 0
    height: int = 0
    width: int = 0
    escapement: int = 0
    orientation: int = 0
    weight: int = 0
    italic: int = 0
    underline: int = 0
    strike_out: int = 0
    charset: int = 0
    out_precision: int = 0
    clip_precision: int = 0
    quality: int = 0
    pitch_family: int = 0
    name: str = ""
    data: bytes = b""

    @property
    def size_px(self) -> int:
        return abs(self.height)

    def __repr__(self) -> str:
        return (f"<Font #{self.handle} {self.name!r} {self.size_px}px "
                f"weight={self.weight} ttf={len(self.data)}B>")


@dataclass
class SoundEntry:
    handle: int = 0
    checksum: int = 0
    references: int = 0
    data_size: int = 0
    flags: int = 0
    frequency: int = 0
    name: str = ""
    data: bytes = b""

    def __repr__(self) -> str:
        return (f"<Sound #{self.handle} {self.name!r} {len(self.data)}B "
                f"freq={self.frequency} flags=0x{self.flags:08X}>")


@dataclass
class MusicEntry:
    handle: int = 0
    checksum: int = 0
    references: int = 0
    data_size: int = 0
    flags: int = 0
    frequency: int = 0
    name: str = ""
    data: bytes = b""

    def __repr__(self) -> str:
        return (f"<Music #{self.handle} {self.name!r} {len(self.data)}B>")


@dataclass
class ImageBankHeader:
    """图标库 / 图像库的公共头。条目本身还没解析（见上面的说明）。"""
    label: str = ""
    graphic_mode: int = 0
    palette_version: int = 0
    palette_entries: int = 0
    palette: List[bytes] = None
    count: int = 0
    entries_offset: int = 0
    raw_tail: bytes = b""

    def __repr__(self) -> str:
        return (f"<{self.label} mode={self.graphic_mode} "
                f"palette={self.palette_version}/{self.palette_entries} "
                f"count={self.count}>")


@dataclass
class MfaBanks:
    fonts: List[FontEntry] = None
    sounds: List[SoundEntry] = None
    music: List[MusicEntry] = None
    icons: Optional[ImageBankHeader] = None
    images: Optional[ImageBankHeader] = None

    def __post_init__(self):
        self.fonts = self.fonts or []
        self.sounds = self.sounds or []
        self.music = self.music or []


def _read_font(r: Reader) -> FontEntry:
    f = FontEntry()
    f.handle = r.u32()
    f.checksum = r.i32()
    f.references = r.u32()
    f.data_size = r.i32()
    f.height = r.i32()
    f.width = r.i32()
    f.escapement = r.i32()
    f.orientation = r.i32()
    f.weight = r.i32()
    f.italic = r.take(1)[0]
    f.underline = r.take(1)[0]
    f.strike_out = r.take(1)[0]
    f.charset = r.take(1)[0]
    f.out_precision = r.take(1)[0]
    f.clip_precision = r.take(1)[0]
    f.quality = r.take(1)[0]
    f.pitch_family = r.take(1)[0]
    raw_name = r.take(FONT_NAME_CHARS * 2)
    f.name = raw_name.decode("utf-16-le", "replace").rstrip("\x00").strip()
    if f.data_size < 0 or f.data_size > len(r.buf):
        raise MfaError(f"字体数据长度异常 {f.data_size}")
    f.data = r.take(f.data_size)
    return f


def _read_sound(r: Reader) -> SoundEntry:
    s = SoundEntry()
    s.handle = r.u32() - 1
    s.checksum = r.i32()
    s.references = r.u32()
    s.data_size = r.i32()
    s.flags = r.u32()
    s.frequency = r.i32()
    name_len = r.i32()
    if s.data_size < 0 or s.data_size > len(r.buf):
        raise MfaError(f"音频数据长度异常 {s.data_size}")
    blob = r.take(s.data_size)
    inner = Reader(blob)
    if name_len < 0 or name_len * 2 > len(blob):
        raise MfaError(f"音频名字长度异常 {name_len}")
    s.name = inner.take(name_len * 2).decode("utf-16-le", "replace").rstrip("\x00")
    s.data = blob[inner.pos:]
    return s


def _read_music(r: Reader) -> MusicEntry:
    m = MusicEntry()
    m.handle = r.u32()
    m.checksum = r.i32()
    m.references = r.u32()
    m.data_size = r.i32()
    m.flags = r.u32()
    m.frequency = r.i32()
    name_len = r.i32()
    if m.data_size < 0 or m.data_size > len(r.buf):
        raise MfaError(f"音乐数据长度异常 {m.data_size}")
    if name_len < 0 or name_len > m.data_size:
        raise MfaError(f"音乐名字长度异常 {name_len}")
    m.name = r.take(name_len * 2).decode("utf-16-le", "replace").rstrip("\x00")
    m.data = r.take(m.data_size - name_len * 2)
    return m


def _read_image_bank_header(r: Reader, label: str) -> ImageBankHeader:
    h = ImageBankHeader(label=label)
    h.graphic_mode = r.u32()
    h.palette_version = r.u16()
    h.palette_entries = r.u16()
    if h.palette_entries > 4096:
        raise MfaError(f"{label} 调色板条目异常 {h.palette_entries}")
    h.palette = [r.take(4) for _ in range(h.palette_entries)]
    h.count = r.u32()
    h.entries_offset = r.pos
    return h


def read_banks(buf: bytes, start: int) -> MfaBanks:
    """从 ``ATNF`` 开始把四个库读出来。

    图标库和图像库的条目还没落地，所以这两段只读到「库头 + 图像数」；
    图标条目按「下一个 AGMI」切开（真样本里 AGMI 在条目数据中不出现），
    图像库之后的内容不再推进 —— 调用方需要的话看 ``entries_offset``。
    """
    r = Reader(buf, start)
    b = MfaBanks()

    if r.tag() != TAG_FONTS:
        raise MfaError(f"字体库标记不对 @0x{start:X}")
    for _ in range(r.u32()):
        b.fonts.append(_read_font(r))

    if r.tag() != TAG_SOUNDS:
        raise MfaError(f"音频库标记不对 @0x{r.pos - 4:X}")
    for _ in range(r.u32()):
        b.sounds.append(_read_sound(r))

    if r.tag() != TAG_MUSIC:
        raise MfaError(f"音乐库标记不对 @0x{r.pos - 4:X}")
    for _ in range(r.u32()):
        b.music.append(_read_music(r))

    if r.tag() != TAG_ICONS:
        raise MfaError(f"图标库标记不对 @0x{r.pos - 4:X}")
    b.icons = _read_image_bank_header(r, "图标库")

    # 图标条目布局未落地：按下一个 AGMI 切开
    nxt = buf.find(TAG_IMAGES, r.pos)
    if nxt < 0:
        raise MfaError("找不到图像库标记")
    b.icons.raw_tail = buf[r.pos:nxt]

    r.pos = nxt
    if r.tag() != TAG_IMAGES:
        raise MfaError("图像库标记不对")
    b.images = _read_image_bank_header(r, "图像库")
    b.images.raw_tail = b""            # 后面是应用设置，不能猜长度
    return b


def describe_banks(b: MfaBanks) -> str:
    parts = [f"字体 {len(b.fonts)}", f"音频 {len(b.sounds)}", f"音乐 {len(b.music)}"]
    if b.icons:
        parts.append(f"图标 {b.icons.count}")
    if b.images:
        parts.append(f"图像 {b.images.count}")
    return " / ".join(parts)
