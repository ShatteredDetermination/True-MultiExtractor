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

已经落地的部分（都有真样本逐字节回写验证）：
  * 前段：头 + 三条 CString + 缩略图；
  * 五个库：字体（108 B 定长条目）、音频、音乐、图标库、图像库
    （图像条目 = 40 B 头 + zlib 流）；build_banks 能把它们原样写回；
  * 应用设置段：六条字符串 + 宽高/颜色/flags + 帮助/Vitalize + 分数/命/帧率
    + 构建类型 + 四个路径 + 安装数据 + 玩家控制（每人类型/键数/键码）。

还没落地（按顺序）：
  * 菜单栏（MenuBar）、窗口菜单号、菜单图片；
  * 全局值 / 全局字符串 / 全局事件；
  * 图形模式、图标句柄、限定符、扩展；
  * 帧偏移表 + 各帧 + 帧后的对象 chunk 列表（u8 id + i32 size，id=0 收尾）。
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
    name_raw: bytes = b""      # 定长 64 字节，原样留着，写回时不重新补 0
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
    name_raw: bytes = b""
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
    name_raw: bytes = b""
    data: bytes = b""

    def __repr__(self) -> str:
        return (f"<Music #{self.handle} {self.name!r} {len(self.data)}B>")


@dataclass
class MfaImage:
    """MFA 图像库 / 图标库的一条图像（40 字节头 + zlib 数据）。"""
    handle: int = 0
    checksum: int = 0
    references: int = 0
    data_size: int = 0
    width: int = 0
    height: int = 0
    graphic_mode: int = 0
    flags: int = 0
    hotspot_x: int = 0
    hotspot_y: int = 0
    action_x: int = 0
    action_y: int = 0
    transparent: bytes = b"\x00\x00\x00\x00"
    decl_size: int = 0
    pixels: bytes = b""
    payload: bytes = b""       # 盘上的 zlib 流，写回时原样用

    @property
    def channels(self) -> int:
        """每像素字节数；不是整数时返回 0（见 ``slack``）。"""
        n = self.width * self.height
        if not n:
            return 0
        return len(self.pixels) // n if len(self.pixels) % n == 0 else 0

    @property
    def slack(self) -> int:
        """解压数据比 ``宽×高×通道数`` 多出来的字节数。

        真样本里有这种条目（Template 的 #5：50x50 却解压出 10100 字节，
        正好多 100 = 50 行 × 2 字节），说明某些模式下每行还带一点附加数据。
        这里只如实记录，不猜它是什么。
        """
        n = self.width * self.height
        if not n or not self.pixels:
            return 0
        ch = len(self.pixels) // n
        return len(self.pixels) - n * ch

    def __repr__(self) -> str:
        extra = f" +{self.slack}B" if self.slack else ""
        return (f"<Image #{self.handle} {self.width}x{self.height} "
                f"mode={self.graphic_mode} flags=0x{self.flags:02X} "
                f"{self.channels}ch {self.decl_size}B{extra}>")


@dataclass
class ImageBankHeader:
    """图标库 / 图像库：头 + 全部条目。"""
    label: str = ""
    graphic_mode: int = 0
    palette_version: int = 0
    palette_entries: int = 0
    palette: List[bytes] = None
    count: int = 0
    entries_offset: int = 0
    entries: List[MfaImage] = None

    def __post_init__(self):
        self.palette = self.palette or []
        self.entries = self.entries or []

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
    app_offset: int = 0        # 图像库之后、应用设置段的起始偏移

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
    f.name_raw = r.take(FONT_NAME_CHARS * 2)
    f.name = f.name_raw.decode("utf-16-le", "replace").rstrip("\x00").strip()
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
    s.name_raw = inner.take(name_len * 2)
    s.name = s.name_raw.decode("utf-16-le", "replace").rstrip("\x00")
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
    m.name_raw = r.take(name_len * 2)
    m.name = m.name_raw.decode("utf-16-le", "replace").rstrip("\x00")
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


def _read_image(r: Reader) -> MfaImage:
    """一条图像：40 字节头 + zlib 流。

    头（实测，图标库与图像库同一个布局）::

        u32 handle / i32 checksum / u32 references / u32 dataSize
        u16 width / u16 height / u8 graphicMode / u8 flags / i16 保留
        i16 hotspotX / i16 hotspotY / i16 actionX / i16 actionY
        4B  transparent / u32 declSize
        dataSize - 4 字节 zlib 流（解压后正好 declSize 字节）

    实测：48x48 mode=7 -> decl 6912 = 48*48*3；16x16 mode=4 -> 768。
    12 条图标正好收在下一个 AGMI，15 条图像正好收在应用设置段的工程名前面。
    """
    import zlib

    img = MfaImage()
    img.handle = r.u32()
    img.checksum = r.i32()
    img.references = r.u32()
    img.data_size = r.u32()
    img.width = r.u16()
    img.height = r.u16()
    img.graphic_mode = r.take(1)[0]
    img.flags = r.take(1)[0]
    r.take(2)                                # 保留
    img.hotspot_x = struct.unpack("<h", r.take(2))[0]
    img.hotspot_y = struct.unpack("<h", r.take(2))[0]
    img.action_x = struct.unpack("<h", r.take(2))[0]
    img.action_y = struct.unpack("<h", r.take(2))[0]
    img.transparent = r.take(4)
    img.decl_size = r.u32()

    if img.data_size < 4 or img.data_size > len(r.buf):
        raise MfaError(f"图像数据长度异常 {img.data_size} @0x{r.pos - 40:X}")
    payload = r.take(img.data_size - 4)
    img.payload = payload
    try:
        img.pixels = zlib.decompress(payload)
    except zlib.error as exc:
        raise MfaError(f"图像 #{img.handle} zlib 解压失败: {exc}") from exc
    if len(img.pixels) != img.decl_size:
        raise MfaError(f"图像 #{img.handle} 解压后 {len(img.pixels)} B，"
                       f"头里写 {img.decl_size} B")
    if img.width and img.height and len(img.pixels) < img.width * img.height:
        raise MfaError(f"图像 #{img.handle} 像素数比尺寸还少")
    return img


def read_banks(buf: bytes, start: int) -> MfaBanks:
    """从 ``ATNF`` 开始把五个库（字体/音频/音乐/图标/图像）全部读出来。"""
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
    for _ in range(b.icons.count):
        b.icons.entries.append(_read_image(r))

    if r.tag() != TAG_IMAGES:
        raise MfaError(f"图像库标记不对 @0x{r.pos - 4:X}")
    b.images = _read_image_bank_header(r, "图像库")
    for _ in range(b.images.count):
        b.images.entries.append(_read_image(r))

    b.app_offset = r.pos
    return b


def read_project_prefix(buf: bytes):
    """读「前段 + 五个库」，返回 (preamble, banks)。"""
    pre = read_preamble(buf)
    start = len(buf) - len(pre.banks_raw)
    return pre, read_banks(buf, start)


def describe_banks(b: MfaBanks) -> str:
    parts = [f"字体 {len(b.fonts)}", f"音频 {len(b.sounds)}", f"音乐 {len(b.music)}"]
    if b.icons:
        parts.append(f"图标 {b.icons.count}")
    if b.images:
        parts.append(f"图像 {b.images.count}")
    return " / ".join(parts)


# ------------------------------------------------------------------ 库的写入
def _write_font(w: Writer, f: FontEntry) -> None:
    w.u32(f.handle)
    w.i32(f.checksum)
    w.u32(f.references)
    w.i32(f.data_size if f.data_size else len(f.data))
    w.i32(f.height)
    w.i32(f.width)
    w.i32(f.escapement)
    w.i32(f.orientation)
    w.i32(f.weight)
    for v in (f.italic, f.underline, f.strike_out, f.charset,
              f.out_precision, f.clip_precision, f.quality, f.pitch_family):
        w.raw(bytes([v & 0xFF]))
    name_raw = f.name_raw
    if not name_raw:
        name_raw = f.name.encode("utf-16-le").ljust(FONT_NAME_CHARS * 2, b"\x00")
    w.raw(name_raw[:FONT_NAME_CHARS * 2].ljust(FONT_NAME_CHARS * 2, b"\x00"))
    w.raw(f.data)


def _write_sound(w: Writer, s: SoundEntry) -> None:
    name_raw = s.name_raw or s.name.encode("utf-16-le")
    w.u32(s.handle + 1)
    w.i32(s.checksum)
    w.u32(s.references)
    w.i32(len(name_raw) + len(s.data))
    w.u32(s.flags)
    w.i32(s.frequency)
    w.i32(len(name_raw) // 2)
    w.raw(name_raw)
    w.raw(s.data)


def _write_music(w: Writer, m: MusicEntry) -> None:
    name_raw = m.name_raw or m.name.encode("utf-16-le")
    w.u32(m.handle)
    w.i32(m.checksum)
    w.u32(m.references)
    w.i32(len(name_raw) + len(m.data))
    w.u32(m.flags)
    w.i32(m.frequency)
    w.i32(len(name_raw) // 2)
    w.raw(name_raw)
    w.raw(m.data)


def _write_image(w: Writer, img: MfaImage) -> None:
    payload = img.payload
    if not payload:
        import zlib
        payload = zlib.compress(img.pixels, 9)
    w.u32(img.handle)
    w.i32(img.checksum)
    w.u32(img.references)
    w.u32(len(payload) + 4)
    w.u16(img.width)
    w.u16(img.height)
    w.raw(bytes([img.graphic_mode & 0xFF, img.flags & 0xFF]))
    w.u16(0)
    for v in (img.hotspot_x, img.hotspot_y, img.action_x, img.action_y):
        w.raw(struct.pack("<h", v))
    w.raw(img.transparent)
    w.u32(img.decl_size if img.decl_size else len(img.pixels))
    w.raw(payload)


def _write_image_bank(w: Writer, h: ImageBankHeader, tag: bytes) -> None:
    w.raw(tag)
    w.u32(h.graphic_mode)
    w.u16(h.palette_version)
    w.u16(h.palette_entries if h.palette_entries else len(h.palette))
    for color in h.palette:
        w.raw(color)
    w.u32(len(h.entries))
    for img in h.entries:
        _write_image(w, img)


def build_banks(b: MfaBanks) -> bytes:
    """把五个库写回字节流；``build_banks(read_banks(buf, s)) == buf[s:b.app_offset]``。"""
    w = Writer()
    w.raw(TAG_FONTS)
    w.u32(len(b.fonts))
    for f in b.fonts:
        _write_font(w, f)

    w.raw(TAG_SOUNDS)
    w.u32(len(b.sounds))
    for s in b.sounds:
        _write_sound(w, s)

    w.raw(TAG_MUSIC)
    w.u32(len(b.music))
    for m in b.music:
        _write_music(w, m)

    if b.icons:
        _write_image_bank(w, b.icons, TAG_ICONS)
    if b.images:
        _write_image_bank(w, b.images, TAG_IMAGES)
    return w.getvalue()

# ------------------------------------------------------------------ 应用设置段
# 布局（两个真样本逐字段核对，落点完全一致）：
#
#   yuniversal 应用名 / 作者 / 描述 / 版权 / 公司 / 版本
#   i32 宽 / i32 高 / 4B 边框色 / u32 显示 flags / u32 图形 flags
#   yuniversal 帮助文件 / Vitalize 预览
#   i32 初始分 / i32 初始命 / i32 帧率 / i32 构建类型
#   yuniversal 目标文件 / 效果目录 / 命令行 / 关于
#   i32 安装数据大小
#   u32 二进制文件数（真样本都是 0；非 0 时布局未知，直接报错不猜）
#   u32 玩家数，每人：i32 类型 / i32 键数 / 键数 x i32 键码
#
# 实测：两个样本都是 640x360、帧率 60、4 个玩家、每人 16 个键
# （前八个 38/40/37/39 = 上下左右，然后 32 空格、17 Ctrl、16 Shift、13 回车）。


@dataclass
class MfaPlayerControl:
    type: int = 0
    keys: List[int] = None

    def __post_init__(self):
        self.keys = self.keys if self.keys is not None else []


@dataclass
class MfaAppHeader:
    app_name: str = ""
    author: str = ""
    description: str = ""
    copyright: str = ""
    company: str = ""
    version: str = ""
    width: int = 0
    height: int = 0
    border_color: bytes = b"\x00\x00\x00\x00"
    display_flags: int = 0
    graphic_flags: int = 0
    help_file: str = ""
    vitalize_preview: str = ""
    init_score: int = 0
    init_lives: int = 0
    frame_rate: int = 60
    build_type: int = 0
    target_filename: str = ""
    effects_folder: str = ""
    command_line: str = ""
    about: str = ""
    installer_size: int = 0
    binary_count: int = 0
    players: List[MfaPlayerControl] = None

    def __post_init__(self):
        self.players = self.players if self.players is not None else []


def read_app_header(buf: bytes, pos: int):
    """读应用设置段，返回 (MfaAppHeader, 段后偏移)。"""
    r = Reader(buf, pos)
    h = MfaAppHeader()
    h.app_name = r.cstring()
    h.author = r.cstring()
    h.description = r.cstring()
    h.copyright = r.cstring()
    h.company = r.cstring()
    h.version = r.cstring()
    h.width = r.i32()
    h.height = r.i32()
    h.border_color = r.take(4)
    h.display_flags = r.u32()
    h.graphic_flags = r.u32()
    h.help_file = r.cstring()
    h.vitalize_preview = r.cstring()
    h.init_score = r.i32()
    h.init_lives = r.i32()
    h.frame_rate = r.i32()
    h.build_type = r.i32()
    h.target_filename = r.cstring()
    h.effects_folder = r.cstring()
    h.command_line = r.cstring()
    h.about = r.cstring()
    h.installer_size = r.i32()
    h.binary_count = r.u32()
    if h.binary_count:
        raise MfaError(f"二进制文件段有 {h.binary_count} 条，布局还没落地")
    for _ in range(r.u32()):
        p = MfaPlayerControl(type=r.i32())
        n = r.i32()
        if n < 0 or n > 256:
            raise MfaError(f"玩家按键数异常 {n}")
        p.keys = [r.i32() for _ in range(n)]
        h.players.append(p)
    if not (0 <= h.width <= 16384 and 0 <= h.height <= 16384):
        raise MfaError(f"窗口尺寸异常 {h.width}x{h.height}")
    return h, r.pos


def build_app_header(h: MfaAppHeader) -> bytes:
    w = Writer()
    for s in (h.app_name, h.author, h.description, h.copyright, h.company, h.version):
        w.cstring(s)
    w.i32(h.width)
    w.i32(h.height)
    w.raw(h.border_color)
    w.u32(h.display_flags)
    w.u32(h.graphic_flags)
    w.cstring(h.help_file)
    w.cstring(h.vitalize_preview)
    w.i32(h.init_score)
    w.i32(h.init_lives)
    w.i32(h.frame_rate)
    w.i32(h.build_type)
    for s in (h.target_filename, h.effects_folder, h.command_line, h.about):
        w.cstring(s)
    w.i32(h.installer_size)
    w.u32(h.binary_count)
    if h.binary_count:
        raise MfaError("二进制文件段还不支持写")
    w.u32(len(h.players))
    for p in h.players:
        w.i32(p.type)
        w.i32(len(p.keys))
        for k in p.keys:
            w.i32(k)
    return w.getvalue()


def build_project_prefix(pre: "MfaPreamble", banks: MfaBanks) -> bytes:
    """前段 + 五个库写回；真样本上等于原文件 ``[:banks.app_offset]``。"""
    import dataclasses
    head = dataclasses.replace(pre, banks_raw=b"")
    return build_preamble(head) + build_banks(banks)


def describe_app(h: MfaAppHeader) -> str:
    return (f"{h.app_name!r} 作者 {h.author!r} {h.width}x{h.height} "
            f"帧率 {h.frame_rate} 玩家 {len(h.players)}")


# ---------------------------------------------------------------- 整份工程进度尺
# 应用设置段之后（菜单栏 → 菜单图形 → 全局值/全局字符串 → 全局事件 → 图形模式
# → 限定符 → 扩展 → 帧偏移表 → 帧 → 对象块清单）还在逐个段落落地中。
# read_layout 把这之前的四段串起来，用 tail_size / coverage 报「还剩多少没解析」，
# 既是自测断言点，也是后面写 CCN → MFA 时的填坑清单。

@dataclass
class MfaLayout:
    preamble: MfaPreamble
    banks: MfaBanks
    app: MfaAppHeader
    banks_offset: int        # 'ATNF' 起始偏移
    app_offset: int          # 应用设置段起始偏移
    tail_offset: int         # 还没解析的尾巴起始偏移（= 应用设置段结束）
    file_size: int

    @property
    def tail_size(self) -> int:
        return self.file_size - self.tail_offset

    @property
    def coverage(self) -> float:
        """已落地段占比（0~100）。"""
        if not self.file_size:
            return 0.0
        return self.tail_offset * 100.0 / self.file_size


def read_layout(buf: bytes) -> MfaLayout:
    """读前段 + 五个库 + 应用设置段，返回 ``MfaLayout``。"""
    if not buf:
        raise MfaError("空文件")
    pre = read_preamble(buf)
    banks_offset = len(buf) - len(pre.banks_raw)
    banks = read_banks(buf, banks_offset)
    app, app_end = read_app_header(buf, banks.app_offset)
    if app_end > len(buf):
        raise MfaError(f"应用设置段越过文件尾 0x{app_end:X} > 0x{len(buf):X}")
    return MfaLayout(
        preamble=pre,
        banks=banks,
        app=app,
        banks_offset=banks_offset,
        app_offset=banks.app_offset,
        tail_offset=app_end,
        file_size=len(buf),
    )


def describe_layout(lay: MfaLayout) -> str:
    return (f"{lay.preamble.app_name!r} "
            f"{len(lay.preamble.thumbnail)} B 缩略图 "
            f"库@{lay.banks_offset:#x}({describe_banks(lay.banks)}) "
            f"应用设置@{lay.app_offset:#x}({lay.app.width}x{lay.app.height}) "
            f"已解析 {lay.tail_offset:#x}/{lay.file_size:#x}"
            f"({lay.coverage:.1f}%) 尾巴 {lay.tail_size} B")


# -------------------------------------------------------------------- 菜单区
# 应用设置段之后立刻是菜单区，布局按公开实现（NebulaFD MFAPackageData.cs /
# CTFAK MFA.cs 的读序，两处一致）：
#
#     u32 menuSize          菜单块字节数（不含这个长度字段自己）
#     menuSize 字节         菜单块（AppMenu 内部结构，本轮先整块保留）
#     i32 windowMenu        窗口菜单索引
#     i32 菜单图形数 + 每项 i32 id / i32 图像序号
#
# 菜单块内部（每个菜单的标题、菜单项、快捷键）还没拆，所以整块按原字节留存 ——
# 只要「长度字段 + 跳过 + 后面两段」对得上，就说明边界找对了。

@dataclass
class MfaMenuRegion:
    menu_size: int = 0
    menu_raw: bytes = b""
    window_menu: int = 0
    menu_images: List[tuple] = None

    def __post_init__(self):
        self.menu_images = self.menu_images if self.menu_images is not None else []


def read_menu_region(buf: bytes, pos: int):
    """读菜单区，返回 (MfaMenuRegion, 区后偏移)。"""
    r = Reader(buf, pos)
    m = MfaMenuRegion(menu_size=r.u32())
    if m.menu_size > len(buf) - r.pos:
        raise MfaError(f"菜单块长度 {m.menu_size} B 超出文件尾 @0x{pos:X}")
    m.menu_raw = r.take(m.menu_size)
    m.window_menu = r.i32()
    n = r.u32()
    if n > 4096:
        raise MfaError(f"菜单图形条目数异常 {n}")
    for _ in range(n):
        m.menu_images.append((r.i32(), r.i32()))
    return m, r.pos


def build_menu_region(m: MfaMenuRegion) -> bytes:
    w = Writer()
    w.u32(m.menu_size if m.menu_size else len(m.menu_raw))
    w.raw(m.menu_raw)
    w.i32(m.window_menu)
    w.u32(len(m.menu_images))
    for mid, img in m.menu_images:
        w.i32(mid)
        w.i32(img)
    return w.getvalue()


def describe_menu(m: MfaMenuRegion) -> str:
    return (f"菜单块 {m.menu_size} B 窗口菜单 #{m.window_menu} "
            f"菜单图形 {len(m.menu_images)} 项")


# ------------------------------------------------------------ 全局量 / 图形模式 / 图标
# 菜单区之后到帧偏移表之前，按实测顺序（两份样本逐字节一样，且首尾都对得上）：
#
#     u32 全局值条数 + 条目            （非空条目的字段布局还没有样本，先只支持空）
#     u32 全局字符串条数 + 条目        （同上）
#     u32 全局事件字节数 + 事件数据
#     u32 图形模式                     （与图像库头里的 graphicMode 一致）
#     u32 图标句柄数 + 每条 i32 句柄
#     u32 限定符条数 + 条目            （先只支持空）
#     u32 扩展条数 + 条目              （先只支持空）
#     u32 帧数 + 每帧绝对偏移 + i32 回程偏移
#
# 「先只支持空」不是偷懒：这两份样本里全是 0，硬猜非空布局等于编数据。
# 真项目里遇到非空就抛 MfaError，把「这里还没落地」摆在明面上。

@dataclass
class MfaGlobals:
    values: List[tuple] = None        # (名字, 值)，非空布局未观测
    strings: List[tuple] = None       # (名字, 值)，非空布局未观测
    events_raw: bytes = b""           # 全局事件段原样（事件树还没拆）
    graphic_mode: int = 0
    icon_handles: List[int] = None
    qualifiers_raw: bytes = b""
    extensions_raw: bytes = b""
    frame_offsets: List[int] = None
    return_offset: int = 0            # 帧之后的回程偏移（对象 chunk 列表起点）

    def __post_init__(self):
        self.values = self.values if self.values is not None else []
        self.strings = self.strings if self.strings is not None else []
        self.icon_handles = self.icon_handles if self.icon_handles is not None else []
        self.frame_offsets = (self.frame_offsets
                              if self.frame_offsets is not None else [])


def _read_named_list(r: Reader, what: str) -> List[tuple]:
    n = r.u32()
    if n:
        raise MfaError(f"{what}有 {n} 条，但非空条目的布局还没观测到样本")
    return []


def read_globals(buf: bytes, pos: int):
    """读全局量区 + 帧偏移表，返回 (MfaGlobals, 段后偏移 = 第一帧起点)。"""
    r = Reader(buf, pos)
    g = MfaGlobals()
    g.values = _read_named_list(r, "全局值")
    g.strings = _read_named_list(r, "全局字符串")
    n = r.u32()
    if n > len(buf) - r.pos:
        raise MfaError(f"全局事件段 {n} B 越界 @0x{r.pos - 4:X}")
    g.events_raw = r.take(n)
    g.graphic_mode = r.u32()
    icons = r.u32()
    if icons > 4096:
        raise MfaError(f"图标句柄数异常 {icons}")
    g.icon_handles = [r.i32() for _ in range(icons)]
    n = r.u32()
    if n:
        raise MfaError(f"限定符有 {n} 条，布局还没观测到样本")
    n = r.u32()
    if n:
        raise MfaError(f"扩展有 {n} 条，布局还没观测到样本")
    n = r.u32()
    if n > 4096:
        raise MfaError(f"帧数异常 {n}")
    for _ in range(n):
        off = r.i32()
        if not (0 < off < len(buf)):
            raise MfaError(f"帧偏移 {off} 越界")
        g.frame_offsets.append(off)
    g.return_offset = r.i32()
    if not (0 < g.return_offset <= len(buf)):
        raise MfaError(f"回程偏移 {g.return_offset} 越界")
    return g, r.pos


def build_globals(g: MfaGlobals) -> bytes:
    w = Writer()
    for items, what in ((g.values, "全局值"), (g.strings, "全局字符串")):
        if items:
            raise MfaError(f"{what}非空条目的布局还没落地，写不出来")
        w.u32(0)
    w.u32(len(g.events_raw))
    w.raw(g.events_raw)
    w.u32(g.graphic_mode)
    w.u32(len(g.icon_handles))
    for h in g.icon_handles:
        w.i32(h)
    w.u32(0)          # 限定符
    w.u32(0)          # 扩展
    w.u32(len(g.frame_offsets))
    for off in g.frame_offsets:
        w.i32(off)
    w.i32(g.return_offset)
    return w.getvalue()


def describe_globals(g: MfaGlobals) -> str:
    return (f"全局值 {len(g.values)} / 全局字符串 {len(g.strings)} / "
            f"全局事件 {len(g.events_raw)} B / 图形模式 {g.graphic_mode} / "
            f"图标句柄 {len(g.icon_handles)} / 帧 {len(g.frame_offsets)} "
            f"{[hex(o) for o in g.frame_offsets]} / 回程 {g.return_offset:#x}")


# ---------------------------------------------------------------- 对象 chunk 列表
# 帧之后（回程偏移处）是整份工程的对象 chunk 列表，一条接一条：
#
#     u8 chunk_id + i32 size + size 字节数据
#
# 收尾是**一个字节的 0**（不是「0 + 长度」）。实测两份样本各 47 条、id 序列完全
# 一样、走到最后一个 0x00 正好落在文件尾 —— 这条链子错了就对不上 EOF。

@dataclass
class MfaChunk:
    chunk_id: int = 0
    size: int = 0
    data: bytes = b""


@dataclass
class MfaChunkList:
    chunks: List[MfaChunk] = None
    end: int = 0
    terminated: bool = False     # 是否遇到那一个字节的 0 收尾

    def __post_init__(self):
        self.chunks = self.chunks if self.chunks is not None else []


def read_chunk_list(buf: bytes, pos: int) -> MfaChunkList:
    """从回程偏移处读对象 chunk 列表，按收尾字节判断结束。"""
    cl = MfaChunkList()
    q = pos
    while True:
        if q >= len(buf):
            raise MfaError(f"chunk 列表在 0x{q:X} 处没读到收尾")
        cid = buf[q]
        q += 1
        if cid == 0:
            cl.terminated = True
            cl.end = q
            return cl
        if q + 4 > len(buf):
            raise MfaError(f"chunk 0x{cid:02X} 的长度字段越界 @0x{q - 1:X}")
        size = struct.unpack_from("<i", buf, q)[0]
        q += 4
        if size < 0 or q + size > len(buf):
            raise MfaError(f"chunk 0x{cid:02X} 长度 {size} 越界 @0x{q - 5:X}")
        cl.chunks.append(MfaChunk(chunk_id=cid, size=size, data=buf[q:q + size]))
        q += size
        if len(cl.chunks) > 4096:
            raise MfaError(f"chunk 列表在 0x{pos:X} 起超过 4096 条，不像真的")


def build_chunk_list(cl: MfaChunkList) -> bytes:
    w = Writer()
    for c in cl.chunks:
        w.raw(bytes([c.chunk_id]))
        w.i32(c.size if c.size else len(c.data))
        w.raw(c.data)
    w.raw(b"\x00")          # 收尾就这一个字节
    return w.getvalue()


def describe_chunk_list(cl: MfaChunkList) -> str:
    ids = " ".join(f"{c.chunk_id:02X}" for c in cl.chunks)
    return (f"{len(cl.chunks)} 条 chunk，收尾 {'有' if cl.terminated else '无'}，"
            f"到 0x{cl.end:X}：{ids}")


# ---------------------------------------------------------------- 整份工程（读）
# 到这里所有段都能定位了，只剩帧体（每帧的 MFAFrameInfo：对象、图层、事件、
# 虚拟矩形）还没拆字段 —— 所以帧体按原字节保留，其余全部结构化。
# 有了它才有资格说「整份 MFA 能逐字节重建」：段与段首尾相接、chunk 列表收到 EOF。

@dataclass
class MfaProject:
    layout: MfaLayout = None
    menu: MfaMenuRegion = None
    globals: MfaGlobals = None
    frames: List[MfaFrame] = None
    frames_raw: bytes = b""          # 帧体原样（各帧首尾相接的那一段）
    chunks: MfaChunkList = None
    size: int = 0

    def __post_init__(self):
        self.frames = self.frames if self.frames is not None else []

    @property
    def frame_bytes(self) -> int:
        return len(self.frames_raw)


def read_project(buf: bytes) -> MfaProject:
    """读一份完整 MFA：前段、库、应用设置、菜单、全局量、帧、chunk 列表。"""
    lay = read_layout(buf)
    menu, pos = read_menu_region(buf, lay.tail_offset)
    glob, pos = read_globals(buf, pos)
    frames_start = glob.frame_offsets[0] if glob.frame_offsets else pos
    if frames_start != pos:
        raise MfaError(f"帧起点 0x{frames_start:X} 与全局量段尾 0x{pos:X} 不接")
    if glob.return_offset < frames_start:
        raise MfaError(f"回程偏移 0x{glob.return_offset:X} 落在帧区之前")
    chunks = read_chunk_list(buf, glob.return_offset)
    if chunks.end != len(buf):
        raise MfaError(f"chunk 列表收到 0x{chunks.end:X}，文件尾 0x{len(buf):X}")
    frames = []
    for i, start in enumerate(glob.frame_offsets):
        end = (glob.frame_offsets[i + 1] if i + 1 < len(glob.frame_offsets)
               else glob.return_offset)
        if end <= start:
            raise MfaError(f"第 {i + 1} 帧的边界不成立 0x{start:X}~0x{end:X}")
        frames.append(read_frame(buf, start, end))
    pr = MfaProject(layout=lay, menu=menu, globals=glob, frames=frames,
                    frames_raw=buf[frames_start:glob.return_offset],
                    chunks=chunks, size=len(buf))
    return pr


def build_project(pr: MfaProject) -> bytes:
    """把 MfaProject 写回字节流（每帧的图层之后仍是原字节）。

    帧偏移与回程偏移在 MFA 里是**绝对文件偏移**，所以只要前面的段长度变了
    （比如换了图像库），这两个值就得跟着重算 —— 这里按实际写出的位置重算，
    而不是照抄读进来的旧值。骨架没改动时算出来与原值相同，回环仍然逐字节相等。
    """
    import dataclasses

    head = (build_project_prefix(pr.layout.preamble, pr.layout.banks)
            + build_app_header(pr.layout.app)
            + build_menu_region(pr.menu))
    frames = [build_frame(f) for f in pr.frames]
    # 全局量段里装着帧数与帧偏移表，长度随帧数变；先用同样条数的占位表量长度
    placeholder = dataclasses.replace(
        pr.globals, frame_offsets=[0] * len(frames), return_offset=0)
    pos = len(head) + len(build_globals(placeholder))
    offsets = []
    for blob in frames:
        offsets.append(pos)
        pos += len(blob)
    glob = dataclasses.replace(pr.globals, frame_offsets=offsets,
                               return_offset=pos)
    return (head + build_globals(glob) + b"".join(frames)
            + build_chunk_list(pr.chunks))


def describe_project(pr: MfaProject) -> str:
    return (f"{pr.layout.preamble.app_name!r} {pr.size} B："
            f"缩略图 {len(pr.layout.preamble.thumbnail)} B / "
            f"{describe_banks(pr.layout.banks)} / "
            f"菜单 {pr.menu.menu_size} B / "
            f"帧 {len(pr.globals.frame_offsets)} 个："
            f"{' / '.join(f.name for f in pr.frames)!r} "
            f"（头部已拆，其余 {sum(len(f.rest_raw) for f in pr.frames)} B 原样）/ "
            f"chunk {len(pr.chunks.chunks)} 条")


# ------------------------------------------------------------------ 结构体检
# 给任意 .mfa 做「结构自洽」检查（自研产物与第三方产物都能查）。规则都来自
# 两份真样本上验证过的事实，不是凭感觉写的；最后一条是整份逐字节回环 ——
# 通过了就等于「这份文件我们能完整读写，没有猜出来的字节」。

VALID_VERSIONS = (6, 7)


def validate(buf: bytes) -> List[str]:
    """返回问题清单；空列表表示结构自洽。不抛异常。"""
    issues: List[str] = []
    if len(buf) < 8:
        return [f"文件只有 {len(buf)} B，连 MFU2 头都不够"]
    if buf[:4] != MAGIC:
        return [f"不是 MFA：开头是 {buf[:4]!r}，应为 {MAGIC!r}"]

    try:
        pr = read_project(buf)
    except MfaError as exc:
        return [f"解析失败：{exc}"]

    pre = pr.layout.preamble
    if pre.version not in VALID_VERSIONS:
        issues.append(f"MFU2 版本 {pre.version} 不在已知范围 {VALID_VERSIONS}")
    if not pre.app_name:
        issues.append("工程名为空")
    if not pre.thumbnail:
        issues.append("没有缩略图")

    bank = pr.layout.banks.images
    if bank.count != len(bank.entries):
        issues.append(f"图像库头写 {bank.count} 条，实际读出 "
                      f"{len(bank.entries)} 条")
    icons = pr.layout.banks.icons
    if icons.count != len(icons.entries):
        issues.append(f"图标库头写 {icons.count} 条，实际读出 "
                      f"{len(icons.entries)} 条")
    for i, img in enumerate(bank.entries):
        if img.width <= 0 or img.height <= 0:
            issues.append(f"图像 #{i} 尺寸异常 {img.width}x{img.height}")
        elif img.decl_size < img.width * img.height:
            issues.append(f"图像 #{i} 解压数据比像素数还少")

    app = pr.layout.app
    if not (0 < app.width <= 16384 and 0 < app.height <= 16384):
        issues.append(f"应用窗口尺寸异常 {app.width}x{app.height}")
    if app.frame_rate <= 0 or app.frame_rate > 1000:
        issues.append(f"帧率异常 {app.frame_rate}")

    if pr.globals.graphic_mode != bank.graphic_mode:
        issues.append(f"全局图形模式 {pr.globals.graphic_mode} 与图像库 "
                      f"{bank.graphic_mode} 不一致")

    offsets = pr.globals.frame_offsets
    if not offsets:
        issues.append("没有帧")
    elif offsets != sorted(offsets):
        issues.append("帧偏移不是递增的")
    for i, f in enumerate(pr.frames):
        if not f.name:
            issues.append(f"第 {i + 1} 帧没有名字")
        if not (0 < f.width <= 65535 and 0 < f.height <= 65535):
            issues.append(f"第 {i + 1} 帧尺寸异常 {f.width}x{f.height}")
        if len(f.palette) not in (0, 256):
            issues.append(f"第 {i + 1} 帧调色板 {len(f.palette)} 色（应是 256）")

    if not pr.chunks.chunks:
        issues.append("对象 chunk 列表是空的")
    if not pr.chunks.terminated:
        issues.append("对象 chunk 列表没有收尾字节")

    try:
        rebuilt = build_project(pr)
    except MfaError as exc:
        issues.append(f"重建失败：{exc}")
    else:
        if rebuilt != buf:
            issues.append(f"重建出来 {len(rebuilt)} B，与原文 {len(buf)} B 不等")
    return issues


# -------------------------------------------------------------------- 帧（头部）
# 每帧的开头按 CTFAK Frame.Write 的顺序，与两份样本逐字段对得上：
#
#     i32 句柄 / CString 帧名 / i32 宽 / i32 高 / 4 B 背景色 / u32 帧 flags
#     i32 最大对象数 / CString 密码 / CString 未知串（实测 12 字节的二进制样内容）
#     i32 最后查看 X/Y（两份样本正好是画面中心）
#     u32 调色板条数 + 每条 4 B（两份都是 256）
#     i32 图章句柄 / i32 当前图层
#     ... 图层 / 转场 / 对象 / 文件夹 / 实例 / 事件 / 帧内 chunk 列表（还没拆）
#
# 这一段之后整帧剩余部分按原字节保留 —— 帧内对象与事件树是 CCN → MFA 真正要
# **重建**的东西，先别拿原始字节冒充「已解析」。

@dataclass
class MfaLayer:
    """帧内一层：名字 + flags + 两个缩放系数（实测每条 16 B）。"""
    name: str = ""
    flags: int = 0
    scale_x: float = 1.0
    scale_y: float = 1.0

    def __repr__(self) -> str:
        return f"<Layer {self.name!r} flags={self.flags}>"


@dataclass
class MfaFrame:
    handle: int = 0
    name: str = ""
    width: int = 0
    height: int = 0
    background: bytes = b"\x00\x00\x00\x00"
    flags: int = 0
    max_objects: int = 0
    password: str = ""
    unk_string: bytes = b""
    last_x: int = 0
    last_y: int = 0
    palette: List[bytes] = None
    stamp_handle: int = 0
    active_layer: int = 0
    layers: List[MfaLayer] = None
    fade_in: int = 0
    fade_out: int = 0
    object_count: int = 0
    rest_raw: bytes = b""        # 对象记录及其后（文件夹/实例/事件/帧内 chunk）原样

    def __post_init__(self):
        self.palette = self.palette if self.palette is not None else []
        self.layers = self.layers if self.layers is not None else []


def read_frame(buf: bytes, start: int, end: int) -> MfaFrame:
    """读一帧的头部（到当前图层为止），其余按原字节挂 ``rest_raw``。"""
    f = MfaFrame()
    r = Reader(buf, start)
    f.handle = r.i32()
    f.name = r.cstring()
    f.width = r.i32()
    f.height = r.i32()
    f.background = r.take(4)
    f.flags = r.u32()
    f.max_objects = r.i32()
    f.password = r.cstring()
    n = r.u32()
    if n > 512:
        raise MfaError(f"帧 {f.name!r} 的未知串长度 {n} 异常")
    f.unk_string = r.take(n)
    f.last_x = r.i32()
    f.last_y = r.i32()
    n = r.u32()
    if n > 4096:
        raise MfaError(f"帧 {f.name!r} 的调色板条数 {n} 异常")
    for _ in range(n):
        f.palette.append(r.take(4))
    f.stamp_handle = r.i32()
    f.active_layer = r.i32()
    n = r.u32()
    if n > 256:
        raise MfaError(f"帧 {f.name!r} 的图层数 {n} 异常")
    for _ in range(n):
        layer = MfaLayer(name=r.cstring(), flags=r.u32())
        layer.scale_x = struct.unpack("<f", r.take(4))[0]
        layer.scale_y = struct.unpack("<f", r.take(4))[0]
        f.layers.append(layer)
    f.fade_in = r.take(1)[0]
    f.fade_out = r.take(1)[0]
    f.object_count = r.u32()
    if f.object_count > 65535:
        raise MfaError(f"帧 {f.name!r} 的对象数 {f.object_count} 异常")
    if not (0 <= f.width <= 65535 and 0 <= f.height <= 65535):
        raise MfaError(f"帧 {f.name!r} 尺寸异常 {f.width}x{f.height}")
    if r.pos > end:
        raise MfaError(f"帧 {f.name!r} 头部越过帧尾 0x{end:X}")
    f.rest_raw = buf[r.pos:end]
    return f


def build_frame(f: MfaFrame) -> bytes:
    w = Writer()
    w.i32(f.handle)
    w.cstring(f.name)
    w.i32(f.width)
    w.i32(f.height)
    w.raw(f.background)
    w.u32(f.flags)
    w.i32(f.max_objects)
    w.cstring(f.password)
    w.u32(len(f.unk_string))
    w.raw(f.unk_string)
    w.i32(f.last_x)
    w.i32(f.last_y)
    w.u32(len(f.palette))
    for c in f.palette:
        w.raw(c)
    w.i32(f.stamp_handle)
    w.i32(f.active_layer)
    w.u32(len(f.layers))
    for layer in f.layers:
        w.cstring(layer.name)
        w.u32(layer.flags)
        w.raw(struct.pack("<f", layer.scale_x))
        w.raw(struct.pack("<f", layer.scale_y))
    w.raw(bytes((f.fade_in, f.fade_out)))
    w.u32(f.object_count)
    w.raw(f.rest_raw)
    return w.getvalue()


def describe_frame(f: MfaFrame) -> str:
    return (f"帧 {f.name!r} {f.width}x{f.height} 背景 {f.background.hex(' ')} "
            f"flags {f.flags:#x} 调色板 {len(f.palette)} 色 图章 {f.stamp_handle} "
            f"当前层 {f.active_layer} 图层 {len(f.layers)} 对象 {f.object_count} "
            f"其余 {len(f.rest_raw)} B 原样")


def extract_strings(body: bytes, min_chars: int = 3, limit: int = 200) -> List[str]:
    """把一段字节里「看起来是 CString 的 UTF-16 文本」都捞出来。

    帧体（对象记录、文件夹、实例、事件）里混着不少名字：对象名、动画名、
    可改变量名，全用同一种 CString 编码。这里只做**无损旁证**：不假设字段位置，
    只在字节流里按「u32 长度 + 高位标记 + UTF-16」扫，遇到就收，其余跳过。
    所以它捞出来的东西可信（编码对得上），但**不代表帧结构已解析**。
    """
    out: List[str] = []
    seen = set()
    i = 0
    n = len(body)
    while i + 4 <= n:
        v = struct.unpack_from("<I", body, i)[0]
        if v & 0x80000000:
            chars = v & 0x7FFFFFFF
            if min_chars <= chars <= 512 and i + 4 + chars * 2 <= n:
                raw = body[i + 4:i + 4 + chars * 2]
                try:
                    text = raw.decode("utf-16-le")
                except UnicodeDecodeError:
                    text = ""
                if text and all(32 <= ord(c) <= 0x2FFF or c in "\t\n" for c in text):
                    if text not in seen:
                        seen.add(text)
                        out.append(text)
                        if len(out) >= limit:
                            return out
                    i += 4 + chars * 2
                    continue
        i += 2
    return out
