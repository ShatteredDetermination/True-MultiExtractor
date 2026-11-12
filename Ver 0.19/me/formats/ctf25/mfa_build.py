# -*- coding: utf-8 -*-
"""CCN（成品）→ MFA（工程）的骨架级转换 —— V0.19。

现在能做的和还做不到的，都写在这：

**能做**：拿一份 MFA 当骨架（默认用 `Template.mfa`），把成品里的
应用信息（工程名 / 作者 / 版权）与图像库搬进去，写出一个结构完整、
能被我们自己的 `mfa.read_project` 读回来的 `.mfa`。

**还做不到**：帧内对象 / 实例 / 事件的重建。所以产出的工程里帧是骨架自带的
空帧：帧名、宽高（帧头 `0x3334`，flag=3 现在能解开）、最后查看点取自成品，
对象与事件仍是空的，**不是**原游戏的布局。这一点不许在对外说明里
含糊：产物是「资源级 mfa」，能打开、能看到图像资源，但看不到原游戏的摆位。

图像编码：成品 2.5+ 的图像解成 RGBA（`imagecodec.to_rgba`），写进 MFA 时
按 32 位条目（`graphic_mode = 8`）存 BGRA、zlib 压缩。
"""

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from me.formats.ctf25 import container, imagebank, imagecodec
from me.formats.ctf25 import chunks as chunk_mod
from me.formats.ctf25 import crypto, ids, mfa, mfa_objects


class MfaBuildError(Exception):
    pass


DEFAULT_TEMPLATE = Path(__file__).resolve().parent.parent.parent.parent / \
    "Template.mfa"


@dataclass
class BuildReport:
    ccn: str = ""
    template: str = ""
    out: str = ""
    app_name: str = ""
    author: str = ""
    copyright: str = ""
    images_total: int = 0
    images_written: int = 0
    images_skipped: int = 0
    frames: int = 0
    frame_names: List[str] = field(default_factory=list)
    frame_sizes: List[Tuple[int, int]] = field(default_factory=list)
    frame_layers: List[List[Tuple[str, int, float, float]]] = field(
        default_factory=list)
    frame_rects: List[Tuple[int, int, int, int]] = field(default_factory=list)
    instances_by_frame: List[int] = field(default_factory=list)
    frame_instances: List[List["CcnInstance"]] = field(default_factory=list)
    instances_total: int = 0
    objects_written: int = 0
    instances_written: int = 0
    instances_skipped: int = 0
    frame_rects_written: int = 0
    frames_decoded: int = 0
    objects: int = 0
    object_names: List[str] = field(default_factory=list)
    object_types: dict = field(default_factory=dict)
    object_headers_size: int = 0
    sounds_total: int = 0
    sounds_written: int = 0
    fonts_total: int = 0
    fonts_written: int = 0
    icons: int = 0
    width: int = 0
    height: int = 0
    fps: int = 0
    app_frames: int = 0
    out_size: int = 0
    manifest: str = ""
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        sounds = (f"{self.sounds_written}/{self.sounds_total} 段音频，"
                  if self.sounds_total else "")
        fonts = (f"{self.fonts_written}/{self.fonts_total} 个字体，"
                 if self.fonts_total else "")
        return (f"{self.images_written}/{self.images_total} 张图像，"
                f"{sounds}{fonts}{self.frames} 帧，{self.out_size:,} B → {self.out}")


def _rgba_to_bgra(rgba: bytes) -> bytes:
    """RGBA → BGRA（MFA 的 32 位条目按 BGRA 存）。"""
    out = bytearray(rgba)
    out[0::4] = rgba[2::4]
    out[2::4] = rgba[0::4]
    return bytes(out)


def _app_texts(buf, chs, unicode_mode):
    def one(chunk_id):
        ch = chunk_mod.find(chs, chunk_id)
        if ch is None:
            return ""
        try:
            data = chunk_mod.load(buf, ch)
        except Exception:
            return ""
        if unicode_mode:
            text = data.decode("utf-16-le", "ignore")
        else:
            text = data.decode("latin-1", "ignore")
        return text.rstrip("\x00")
    return (one(ids.CHUNK_APP_NAME), one(ids.CHUNK_APP_AUTHOR),
            one(ids.CHUNK_APP_COPYRIGHT))


def read_ccn_assets(ccn_path, limit: Optional[int] = None):
    """读成品的应用信息与图像，返回 (app 三串, imagebank 条目列表, 总条数)。"""
    from me.formats.ctf25.extractor import _is_25plus

    mm, loc = container.open_file(ccn_path)
    if loc is None:
        raise MfaBuildError("未找到 Clickteam CCN 数据（PAMU / PAME / CRUF）")
    try:
        buf = mm.mm
        chs = chunk_mod.scan(buf, loc.chunk_start, len(buf))
        name, author, copyright_ = _app_texts(buf, chs, loc.unicode)
        img_chunk = chunk_mod.find(chs, ids.CHUNK_IMAGES)
        if img_chunk is None:
            return (name, author, copyright_), [], 0
        data = chunk_mod.load(buf, img_chunk)
        entries = list(imagebank.parse_images(data, _is_25plus(chs)))
        total = len(entries)
        if limit is not None:
            entries = entries[:limit]
        return (name, author, copyright_), entries, total
    finally:
        mm.close()


def to_mfa_image(entry) -> mfa.MfaImage:
    """把成品的一条图像转成 MFA 图像条目（32 位 BGRA + zlib）。"""
    rgba = imagecodec.to_rgba(entry)
    bgra = _rgba_to_bgra(rgba)
    payload = zlib.compress(bgra, 6)
    return mfa.MfaImage(
        handle=entry.handle,
        checksum=0,
        references=1,
        data_size=len(payload) + 4,
        width=entry.width,
        height=entry.height,
        graphic_mode=8,
        flags=0,
        hotspot_x=0,
        hotspot_y=0,
        action_x=0,
        action_y=0,
        transparent=b"\x00\x00\x00\x00",
        decl_size=len(bgra),
        pixels=bgra,
        payload=payload,
    )


def read_ccn_frames(ccn_path):
    """只读每帧的**帧名**（0x3335，普通 zlib chunk）。

    帧头 / 图层 / 虚拟矩形现在也读得出来了（flag=3 的密钥已定），见
    :func:`read_ccn_frames_info`；这个函数保留给「只要名字」的调用点。
    """
    out = []
    mm, loc = container.open_file(ccn_path)
    if loc is None:
        raise MfaBuildError("未找到 Clickteam CCN 数据（PAMU / PAME / CRUF）")
    try:
        buf = mm.mm
        chs = chunk_mod.scan(buf, loc.chunk_start, len(buf))
        for frame in chs:
            if frame.id != 0x3333:
                continue
            name = ""
            try:
                sub = chunk_mod.scan(buf, frame.data_offset,
                                     frame.data_offset + frame.size)
                name_chunk = chunk_mod.find(sub, 0x3335)
                if name_chunk is not None:
                    data = chunk_mod.load(buf, name_chunk)
                    name = data.decode("utf-16-le" if loc.unicode else "latin-1",
                                       "ignore").rstrip("\x00")
            except Exception:
                name = ""
            out.append(name)
        return out
    finally:
        mm.close()


@dataclass
class CcnFrameInfo:
    """成品里一帧能读出来的东西。

    帧名（0x3335）是普通 zlib 块；帧头（0x3334）、图层（0x3341）、虚拟矩形
    （0x3342）是 flag=3 块，要用「应用名（0x2224）+ 编辑器工程路径（0x222E）」
    当密钥才解得开（见 `crypto.app_key`）。解不开时只有 `name` 有值，
    `decoded` 为 False —— 转换器会照旧用应用窗口尺寸兜底，不瞎猜。
    """

    name: str = ""
    width: int = 0
    height: int = 0
    flags: int = 0
    rect: Tuple[int, int, int, int] = (0, 0, 0, 0)
    layers: List[Tuple[str, int, float, float]] = field(default_factory=list)
    instances: List["CcnInstance"] = field(default_factory=list)
    decoded: bool = False


@dataclass
class CcnInstance:
    """成品里的一条实例（帧内 0x3338 块）。

    记录 20 B：``u16 对象下标 / u16 序号 / i32 x / i32 y / u32 未知 / u32 图层``，
    末尾还有 4 B 校验。对象下标对得上 0x2253 对象表里的序号（实测 867 条全对得上，
    名字也说得通：`Ini`、`Font Embed Object`、`textbox string`…）。
    """

    obj_index: int = 0
    order: int = 0
    x: int = 0
    y: int = 0
    unk: int = 0
    layer: int = 0
    object_name: str = ""
    object_type: int = 0


def _chunk_text(buf, ch, unicode_mode) -> str:
    try:
        data = chunk_mod.load(buf, ch)
    except Exception:
        return ""
    if unicode_mode:
        return data.decode("utf-16-le", "ignore").rstrip("\x00")
    return data.decode("latin-1", "ignore").rstrip("\x00")


def _parse_frame_header(data):
    """0x3334 解出来是 16 B：宽 / 高 / 未知 / 帧标志。"""
    if not data or len(data) < 16:
        return None
    width, height, _unk, flags = struct.unpack_from("<IIII", data, 0)
    if not (0 < width <= 65535 and 0 < height <= 65535):
        return None
    return width, height, flags


def _parse_frame_rect(data):
    """0x3342 是编辑器里的虚拟矩形：左 / 上 / 右 / 下。"""
    if not data or len(data) < 16:
        return None
    return struct.unpack_from("<iiii", data, 0)


def _parse_frame_layers(data):
    """0x3341：u32 条数，之后每条 ``u32 16 / f32 sx / f32 sy / u32 flags / u32 0``
    + 不带长度前缀的 UTF-16 名字（``00 00`` 收尾）。"""
    if not data or len(data) < 4:
        return []
    count = struct.unpack_from("<I", data, 0)[0]
    if not (0 <= count <= 64):
        return []
    pos = 4
    out = []
    for _ in range(count):
        if pos + 20 > len(data):
            break
        _unk, scale_x, scale_y, flags, _unk2 = struct.unpack_from(
            "<IffII", data, pos)
        pos += 20
        end = pos
        while end + 1 < len(data) and data[end:end + 2] != b"\x00\x00":
            end += 2
        name = data[pos:end].decode("utf-16-le", "ignore")
        pos = end + 2
        out.append((name, flags, scale_x, scale_y))
    return out


def _parse_frame_instances(data, objects_by_index=None):
    """0x3338：u32 条数 + 每条 20 B + 4 B 校验。"""
    if not data or len(data) < 4:
        return []
    count = struct.unpack_from("<I", data, 0)[0]
    if not (0 <= count <= 100000) or len(data) < 4 + count * 20:
        return []
    objects_by_index = objects_by_index or {}
    out = []
    for i in range(count):
        idx, order, x, y, unk, layer = struct.unpack_from(
            "<HHiiII", data, 4 + i * 20)
        obj = objects_by_index.get(idx)
        out.append(CcnInstance(
            obj_index=idx, order=order, x=x, y=y, unk=unk, layer=layer,
            object_name=(obj[1] if obj else ""),
            object_type=(obj[0] if obj else 0)))
    return out


def read_ccn_frames_info(ccn_path, objects_by_index=None):
    """一次扫描读出所有帧：帧名 + 帧头 + 虚拟矩形 + 图层 + 实例，外加解密密钥来源。

    ``objects_by_index`` 是 ``{对象下标: (类型, 名字)}``，用来把实例里的对象下标
    翻成名字；不传就自己读一遍对象表（0x2253 / 0x2254）。
    """
    mm, loc = container.open_file(ccn_path)
    if loc is None:
        raise MfaBuildError("未找到 Clickteam CCN 数据（PAMU / PAME / CRUF）")
    try:
        buf = mm.mm
        app_name = editor = ""
        frame_chunks = []
        for ch in chunk_mod.scan(buf, loc.chunk_start, len(buf)):
            if ch.id == ids.CHUNK_APP_NAME:
                app_name = _chunk_text(buf, ch, loc.unicode)
            elif ch.id == ids.CHUNK_APP_EDITOR:
                editor = _chunk_text(buf, ch, loc.unicode)
            elif ch.id == ids.CHUNK_FRAME:
                frame_chunks.append(ch)

        if objects_by_index is None:
            try:
                table = read_ccn_objects(ccn_path)
                objects_by_index = {o.index: (o.type_id, o.name)
                                    for o in table.objects}
            except Exception:
                objects_by_index = {}

        key = crypto.app_key(app_name, editor)
        infos = []
        for frame in frame_chunks:
            info = CcnFrameInfo()
            payloads = {}
            for s in chunk_mod.scan(buf, frame.data_offset,
                                    frame.data_offset + frame.size):
                if s.id == ids.CHUNK_FRAME_NAME:
                    info.name = _chunk_text(buf, s, loc.unicode)
                elif s.flag == 3 and s.id in (ids.CHUNK_FRAME_HEADER,
                                              ids.CHUNK_FRAME_LAYERS,
                                              ids.CHUNK_FRAME_RECT,
                                              ids.CHUNK_FRAME_INSTANCES):
                    body = buf[s.data_offset:s.data_offset + s.size]
                    try:
                        info_decl, data = crypto.decrypt_chunk_body(
                            body, s.id, key=key)
                    except crypto.CryptoError:
                        continue
                    if len(data) == info_decl:
                        payloads[s.id] = data
            header = _parse_frame_header(payloads.get(ids.CHUNK_FRAME_HEADER))
            if header:
                info.width, info.height, info.flags = header
                info.decoded = True
            rect = _parse_frame_rect(payloads.get(ids.CHUNK_FRAME_RECT))
            if rect:
                info.rect = rect
            info.layers = _parse_frame_layers(
                payloads.get(ids.CHUNK_FRAME_LAYERS, b""))
            info.instances = _parse_frame_instances(
                payloads.get(ids.CHUNK_FRAME_INSTANCES), objects_by_index)
            infos.append(info)
        return infos
    finally:
        mm.close()


@dataclass
class CcnAppInfo:
    """成品应用头（0x2223，112 B）里已确认的字段。"""
    width: int = 0
    height: int = 0
    frames: int = 0
    fps: int = 0


def read_ccn_app(ccn_path) -> CcnAppInfo:
    """读成品的应用头（0x2223）。

    实测布局（dtlg-v1.1.0.exe，112 B）：
    ``u32 长度 / u32 版本 / u32 flags / u16 宽 / u16 高 / …(边框尺寸等)… /
    +100 i32 帧数 / +104 i32 帧率 / +108 i32 256``。
    交叉验证：+100 读出 31，正好等于 ``0x3333`` 帧容器数量、也等于帧名个数；
    +104 读出 60，和 Fusion 工程的默认帧率量级一致。其余字段不猜。
    """
    import struct

    mm, loc = container.open_file(ccn_path)
    if loc is None:
        raise MfaBuildError("未找到 Clickteam CCN 数据（PAMU / PAME / CRUF）")
    try:
        buf = mm.mm
        chs = chunk_mod.scan(buf, loc.chunk_start, len(buf))
        info = CcnAppInfo()
        ch = chunk_mod.find(chs, 0x2223)
        if ch is None:
            return info
        data = chunk_mod.load(buf, ch)
        if len(data) < 16:
            return info
        width, height = struct.unpack_from("<HH", data, 12)
        if 0 < width <= 16384 and 0 < height <= 16384:
            info.width, info.height = width, height
        if len(data) >= 108:
            frames, fps = struct.unpack_from("<ii", data, 100)
            if 0 < frames <= 65535:
                info.frames = frames
            if 0 < fps <= 1000:
                info.fps = fps
        return info
    finally:
        mm.close()


@dataclass
class CcnObject:
    """成品里的一个对象：16 B 记录里已确认的字段 + 原始字节。"""
    index: int = 0
    type_id: int = 0
    flag: int = 0
    rest: bytes = b""
    name: str = ""

    def __repr__(self) -> str:
        return f"<Obj #{self.index} type={self.type_id} {self.name!r}>"


@dataclass
class CcnObjectTable:
    names: List[str] = field(default_factory=list)
    objects: List[CcnObject] = field(default_factory=list)
    headers_size: int = 0

    def type_counts(self):
        from collections import Counter
        return Counter(o.type_id for o in self.objects)


def parse_object_record(rec: bytes, name: str = "") -> CcnObject:
    """解一条 16 B 对象记录。

    已确认（有交叉验证的）：``u16 序号`` + ``u16 类型号`` + ``u16 标志``。
    其余 10 字节照原样留在 ``rest`` 里 —— 字段含义还没定，不编。
    证据：867 条记录的序号正好是 0..866；类型号只有 21 种取值，
    其中 ``text`` 是 2、``counter`` 是 7、``Font Embed Object`` 是 32（≥32 是扩展）。
    """
    import struct

    if len(rec) < 16:
        raise MfaBuildError(f"对象记录只有 {len(rec)} B，应为 16 B")
    index, type_id, flag = struct.unpack_from("<HHH", rec, 0)
    return CcnObject(index=index, type_id=type_id, flag=flag,
                     rest=rec[6:16], name=name)


def read_ccn_objects(ccn_path) -> CcnObjectTable:
    """读成品里的对象名（0x2254）与对象记录（0x2253）。

    对象名就是**一串以 ``00 00`` 收尾的 UTF-16LE 字符串**，没有条数前缀。
    对象记录 16 B 一条，条数 = 对象名个数（dtlg：867 条 / 13,872 B）。
    """
    mm, loc = container.open_file(ccn_path)
    if loc is None:
        raise MfaBuildError("未找到 Clickteam CCN 数据（PAMU / PAME / CRUF）")
    try:
        buf = mm.mm
        chs = chunk_mod.scan(buf, loc.chunk_start, len(buf))
        table = CcnObjectTable()
        ch = chunk_mod.find(chs, 0x2254)
        if ch is not None:
            text = chunk_mod.load(buf, ch).decode("utf-16-le", "ignore")
            table.names = [p for p in text.split("\x00") if p]
        hch = chunk_mod.find(chs, 0x2253)
        if hch is not None:
            raw = chunk_mod.load(buf, hch)
            table.headers_size = len(raw)
            count = len(raw) // 16
            for i in range(count):
                rec = raw[i * 16:(i + 1) * 16]
                nm = table.names[i] if i < len(table.names) else ""
                table.objects.append(parse_object_record(rec, nm))
        return table
    finally:
        mm.close()


def read_ccn_sounds(ccn_path, limit: Optional[int] = None):
    """读成品里的音频条目（0x2250 之类，走现成的 soundbank 解析）。"""
    from me.formats.ctf25 import soundbank
    from me.formats.ctf25.extractor import _is_25plus

    mm, loc = container.open_file(ccn_path)
    if loc is None:
        raise MfaBuildError("未找到 Clickteam CCN 数据（PAMU / PAME / CRUF）")
    try:
        buf = mm.mm
        chs = chunk_mod.scan(buf, loc.chunk_start, len(buf))
        ch = chunk_mod.find(chs, ids.CHUNK_SOUNDS)
        if ch is None:
            return []
        data = chunk_mod.load(buf, ch)
        try:
            entries = list(soundbank.parse_sounds(data, _is_25plus(chs)))
        except Exception:
            entries = list(soundbank.parse_sounds(data, False))
        return entries[:limit] if limit else entries
    finally:
        mm.close()


def wav_frequency(data: bytes) -> int:
    """从 WAV 头里取采样率；不是 WAV 就返回 0（不瞎填）。"""
    if len(data) < 28 or data[:4] not in (b"RIFF", b"RIFX"):
        return 0
    import struct
    rate = struct.unpack_from("<I", data, 24)[0]
    return rate if 0 < rate <= 384000 else 0


def to_mfa_sound(entry) -> mfa.SoundEntry:
    """把成品的一条音频转成 MFA 音频条目（频率只认 WAV 头，其余留 0）。"""
    return mfa.SoundEntry(
        handle=entry.handle,
        checksum=entry.checksum,
        references=entry.references,
        data_size=len(entry.data),
        flags=0,
        frequency=wav_frequency(entry.data),
        name=entry.name,
        data=entry.data,
    )


def read_ccn_icons(ccn_path):
    """读成品图标块（0x2235，Icon 16x16）。

    布局实测（dtlg-v1.1.0.exe，decl 1352 B，正好对得上）::

        u32 40 / u32 宽 16 / u32 高 16 / u16 1 / u16 8 / 20 B 保留
        256 × 4 B 调色板（BGRA，alpha 都写 0；前几项是 黑/红/绿/黄/蓝/品红）
        高 × (宽 + 2) 字节的 8bpp 索引（每行按 2 字节对齐补 0）

    验证：40 + 1024 + 16×18 = 1352 —— 与块长完全相等，不是凑出来的。
    返回 [(宽, 高, 索引字节, 调色板)]。
    """
    import struct

    mm, loc = container.open_file(ccn_path)
    if loc is None:
        raise MfaBuildError("未找到 Clickteam CCN 数据（PAMU / PAME / CRUF）")
    try:
        buf = mm.mm
        chs = chunk_mod.scan(buf, loc.chunk_start, len(buf))
        ch = chunk_mod.find(chs, 0x2235)
        if ch is None:
            return []
        data = chunk_mod.load(buf, ch)
        if len(data) < 40:
            return []
        _, width, height, count, bpp = struct.unpack_from("<IIIHH", data, 0)
        if not (0 < width <= 256 and 0 < height <= 256) or count != 1 or bpp != 8:
            return []
        size = len(data) - 40 - 1024
        if size <= 0 or size % height:
            return []
        stride = size // height          # 实测 16 宽的行是 18 字节（多 2 字节）
        if stride < width:
            return []
        palette = [bytes(data[40 + i * 4:40 + i * 4 + 4]) for i in range(256)]
        base = 40 + 1024
        indexes = b"".join(bytes(data[base + y * stride:base + y * stride + width])
                           for y in range(height))
        return [(width, height, indexes, palette)]
    finally:
        mm.close()


def to_mfa_icon(icon) -> mfa.MfaImage:
    """把一条 8bpp 图标展开成 MFA 的 32 位 BGRA 条目（zlib 压缩）。"""
    width, height, indexes, palette = icon
    out = bytearray()
    for idx in indexes:
        b, g, r, a = palette[idx] if idx < len(palette) else (0, 0, 0, 0)
        out += bytes((b, g, r, 255))
    payload = zlib.compress(bytes(out), 6)
    return mfa.MfaImage(
        handle=0, checksum=0, references=1,
        data_size=len(payload) + 4,
        width=width, height=height, graphic_mode=8, flags=0,
        transparent=b"\x00\x00\x00\x00", decl_size=len(out),
        pixels=bytes(out), payload=payload,
    )


INSTANCE_CAP = 4000


def _manifest_instances(rep: "BuildReport", cap: int = INSTANCE_CAP):
    """实例明细：帧名 + 每条的对象名/类型/x/y/图层。

    超过 ``cap`` 条就只给条数，并在文本里写明截断 —— 清单是给人看的，
    不该因为一个上万实例的成品膨胀成几十兆。
    """
    total = sum(len(items) for items in rep.frame_instances)
    if total > cap:
        return {"truncated": True, "total": total,
                "frames": [{"frame": name, "count": len(items)}
                           for name, items in zip(rep.frame_names,
                                                  rep.frame_instances)]}
    out = []
    for name, items in zip(rep.frame_names, rep.frame_instances):
        out.append({
            "frame": name,
            "count": len(items),
            "items": [{"object": it.object_name, "type": it.object_type,
                       "x": it.x, "y": it.y, "layer": it.layer}
                      for it in items],
        })
    return {"truncated": False, "total": total, "frames": out}


def write_manifest(path, rep: "BuildReport", table=None) -> str:
    """把「从成品读到了什么」写成 JSON 清单，落在 mfa 旁边。

    这不是 mfa 的一部分，是给人/给后续工具看的清单：工程名、窗口、帧率、
    帧名、对象名与类型、各库条数，以及所有「跳过 / 没做到」的说明。
    帧内布局与事件还没重建，清单里也会写明。
    """
    import json

    data = {
        "source": rep.ccn,
        "template": rep.template,
        "mfa": rep.out,
        "app": {
            "name": rep.app_name,
            "author": rep.author,
            "copyright": rep.copyright,
            "width": rep.width,
            "height": rep.height,
            "fps": rep.fps,
            "frames_declared": rep.app_frames,
        },
        "counts": {
            "images": rep.images_total,
            "images_written": rep.images_written,
            "sounds": rep.sounds_total,
            "sounds_written": rep.sounds_written,
            "fonts": rep.fonts_total,
            "fonts_written": rep.fonts_written,
            "frames": rep.frames,
            "objects": rep.objects,
        },
        "frames": list(rep.frame_names),
        "frame_sizes": [list(sz) for sz in rep.frame_sizes],
        "frame_rects": [list(r) for r in rep.frame_rects],
        "instances_total": rep.instances_total,
        "objects_written": rep.objects_written,
        "instances_written": rep.instances_written,
        "instances_skipped": rep.instances_skipped,
        "instances": _manifest_instances(rep),
        "frame_layers": [
            [{"name": name, "flags": flags, "scale_x": round(sx, 4),
              "scale_y": round(sy, 4)}
             for (name, flags, sx, sy) in layers]
            for layers in rep.frame_layers
        ],
        "object_types": {str(k): v for k, v in (rep.object_types or {}).items()},
        "objects": ([{"index": o.index, "name": o.name, "type": o.type_id,
                      "flag": o.flag} for o in table.objects] if table else []),
        "notes": list(rep.notes),
        "not_reconstructed": [
            "帧内对象实例与事件（帧头已解开：宽高、虚拟矩形、图层已取；实例 / 事件未拆）",
            "对象仍未写进 MFA 的对象 chunk 列表",
        ],
    }
    text = json.dumps(data, ensure_ascii=False, indent=2)
    Path(path).write_text(text, encoding="utf-8")
    return str(path)


def read_ccn_fonts(ccn_path):
    """读成品的字体：元数据（0x2246 之类）+ 内嵌 TTF。

    用现成的 ``fontbank.parse_font_meta`` / ``parse_truetype``；两者条数对不上时
    只按小的那个配对，并在返回里如实告诉调用方（不硬凑）。
    """
    from me.formats.ctf25 import fontbank
    from me.formats.ctf25.extractor import _is_25plus

    mm, loc = container.open_file(ccn_path)
    if loc is None:
        raise MfaBuildError("未找到 Clickteam CCN 数据（PAMU / PAME / CRUF）")
    try:
        buf = mm.mm
        chs = chunk_mod.scan(buf, loc.chunk_start, len(buf))
        two = _is_25plus(chs)
        metas = []
        mch = chunk_mod.find(chs, ids.CHUNK_FONTS)
        if mch is not None:
            try:
                metas = list(fontbank.parse_font_meta(
                    chunk_mod.load(buf, mch), two, loc.unicode))
            except Exception:
                metas = list(fontbank.parse_font_meta(
                    chunk_mod.load(buf, mch), False, loc.unicode))
        ttf = []
        tch = chunk_mod.find(chs, ids.CHUNK_FONTS_2)
        if tch is not None:
            try:
                ttf = list(fontbank.parse_truetype(chunk_mod.load(buf, tch)))
            except Exception:
                ttf = []
        return metas, ttf
    finally:
        mm.close()


def to_mfa_font(meta, data: bytes) -> mfa.FontEntry:
    """MFA 字体条目的字高按「负值」存（和真样本一致），其余原样映射。"""
    height = abs(meta.height) or 12
    return mfa.FontEntry(
        handle=meta.handle,
        checksum=meta.checksum,
        references=meta.references,
        data_size=len(data),
        height=-height,
        width=meta.width or 0,
        weight=meta.weight or 400,
        italic=meta.italic,
        underline=meta.underline,
        strike_out=meta.strikeout,
        charset=meta.charset,
        name=meta.face_name,
        data=data,
    )


def build_from_ccn(ccn_path, out_path, template_path=None, app_name: str = "",
                   limit: Optional[int] = None, frame_names: bool = True,
                   objects: bool = False,
                   progress: Optional[Callable] = None) -> BuildReport:
    """骨架级转换：应用信息 + 图像库 从成品搬进骨架，写出 ``out_path``。"""
    template_path = Path(template_path) if template_path else DEFAULT_TEMPLATE
    if not template_path.is_file():
        raise MfaBuildError(f"骨架 MFA 不存在：{template_path}")
    if not Path(ccn_path).is_file():
        raise MfaBuildError(f"成品不存在：{ccn_path}")

    rep = BuildReport(ccn=str(ccn_path), template=str(template_path))
    (rep.app_name, rep.author, rep.copyright), entries, total = \
        read_ccn_assets(ccn_path, limit)
    rep.images_total = total
    if app_name:
        rep.app_name = app_name

    pr = mfa.read_project(template_path.read_bytes())
    bank = pr.layout.banks.images
    bank.entries = []
    for i, entry in enumerate(entries):
        try:
            bank.entries.append(to_mfa_image(entry))
            rep.images_written += 1
        except Exception as exc:                      # 单张坏了不拖垮整包
            rep.images_skipped += 1
            rep.notes.append(f"#{entry.handle} 跳过：{exc}")
        if progress and (i + 1) % 32 == 0:
            progress(i + 1, len(entries))
    bank.count = len(bank.entries)
    bank.graphic_mode = 8
    pr.globals.graphic_mode = 8      # 与图像库保持一致，否则体检会报不一致

    try:
        icons = read_ccn_icons(ccn_path)
        if icons:
            ibank = pr.layout.banks.icons
            ibank.entries = []
            for i, icon in enumerate(icons):
                img = to_mfa_icon(icon)
                img.handle = i
                ibank.entries.append(img)
            ibank.count = len(ibank.entries)
            ibank.graphic_mode = 8
            ibank.palette = list(icons[0][3])
            ibank.palette_version = 768
            ibank.palette_entries = 256
            rep.icons = len(ibank.entries)
    except Exception as exc:
        rep.notes.append(f"图标读不出来：{exc}")

    try:
        metas, ttfs = read_ccn_fonts(ccn_path)
        rep.fonts_total = len(metas)
        pr.layout.banks.fonts = []
        for i, meta in enumerate(metas):
            data = ttfs[i] if i < len(ttfs) else b""
            try:
                pr.layout.banks.fonts.append(to_mfa_font(meta, data))
                rep.fonts_written += 1
            except Exception as exc:
                rep.notes.append(f"字体 #{meta.handle} 跳过：{exc}")
        if ttfs and len(ttfs) != len(metas):
            rep.notes.append(f"字体元数据 {len(metas)} 条、内嵌 TTF {len(ttfs)} 份，"
                             "按条数少的配对")
    except Exception as exc:
        rep.notes.append(f"字体读不出来：{exc}")

    try:
        sounds = read_ccn_sounds(ccn_path, limit)
        rep.sounds_total = len(sounds)
        pr.layout.banks.sounds = []
        for entry in sounds:
            try:
                pr.layout.banks.sounds.append(to_mfa_sound(entry))
                rep.sounds_written += 1
            except Exception as exc:
                rep.notes.append(f"音频 #{entry.handle} 跳过：{exc}")
    except Exception as exc:
        rep.notes.append(f"音频读不出来：{exc}")

    if frame_names:
        try:
            infos = read_ccn_frames_info(ccn_path)
        except Exception as exc:
            infos = []
            rep.notes.append(f"帧头读不出来：{exc}")
        names = [info.name for info in infos]
        try:
            app = read_ccn_app(ccn_path)
            rep.width, rep.height = app.width, app.height
            rep.fps = app.fps
            rep.app_frames = app.frames
            if app.frames and names and app.frames != len(names):
                rep.notes.append(f"应用头写 {app.frames} 帧，实际读到 "
                                 f"{len(names)} 帧")
        except Exception as exc:
            rep.notes.append(f"应用尺寸读不出来：{exc}")
        if infos:
            import copy
            base = pr.frames[0]
            pr.frames = []
            for i, info in enumerate(infos):
                f = copy.deepcopy(base)
                f.handle = i
                f.name = info.name or f"Frame {i + 1}"
                if info.decoded:
                    rep.frames_decoded += 1
                    f.width, f.height = info.width, info.height
                elif rep.width and rep.height:
                    f.width, f.height = rep.width, rep.height
                f.last_x, f.last_y = f.width // 2, f.height // 2
                if info.decoded and info.rect and info.rect != (0, 0, 0, 0):
                    try:
                        f.rest_raw = mfa.set_frame_rect(f.rest_raw, info.rect)
                        rep.frame_rects_written += 1
                    except mfa.MfaError as exc:
                        rep.notes.append(f"帧 {f.name!r} 的虚拟矩形没写进去：{exc}")
                if objects and info.instances:
                    try:
                        events_raw, chunks_raw = mfa_objects.split_rest(f.rest_raw)
                        blobs, handles, inst_blobs, skipped = \
                            mfa_objects.build_frame_objects(info.instances)
                        if handles:
                            f.rest_raw = mfa_objects.build_frame_rest(
                                blobs, handles, inst_blobs, events_raw, chunks_raw)
                            f.object_count = len(blobs)   # 帧头里的对象数要跟着改
                            rep.objects_written += len(handles)
                            rep.instances_written += len(inst_blobs)
                            rep.instances_skipped += skipped
                    except Exception as exc:
                        rep.notes.append(f"帧 {f.name!r} 的对象没写进去：{exc}")
                pr.frames.append(f)
                rep.frame_sizes.append((f.width, f.height))
                rep.frame_layers.append(list(info.layers))
                rep.frame_rects.append(tuple(info.rect))
                rep.instances_by_frame.append(len(info.instances))
                rep.frame_instances.append(list(info.instances))
                rep.instances_total += len(info.instances)
            rep.frame_names = [f.name for f in pr.frames]
            if rep.frames_decoded:
                rep.notes.append(
                    f"帧头解密成功 {rep.frames_decoded}/{len(pr.frames)} 帧，"
                    "尺寸与图层按帧头（密钥 = 应用名 + 编辑器工程路径）")
            if rep.objects_written:
                rep.notes.append(
                    f"对象写了 {rep.objects_written} 个（占位：只有类型/名字/图标，"
                    "Active 无动画、String 无文字、Counter 无外观）、"
                    f"实例 {rep.instances_written} 条，跳过 {rep.instances_skipped} 条"
                    "（扩展对象未支持）")
            if rep.frame_rects_written:
                rep.notes.append(
                    f"虚拟矩形写进 MFA {rep.frame_rects_written} 帧"
                    "（帧尾 chunk 0x21，滚动区跟成品一致）")
            if rep.instances_total:
                rep.notes.append(
                    f"实例读到 {rep.instances_total} 条（对象下标 + x/y + 图层），"
                    "已写进清单；还没写进 MFA 的帧体")
            if rep.frames_decoded < len(pr.frames):
                missing = len(pr.frames) - rep.frames_decoded
                rep.notes.append(
                    f"还有 {missing} 帧的帧头没解开（flag=3 密钥对不上），"
                    "这几帧尺寸按应用窗口兜底")
    rep.frames = len(pr.frames)

    table = None
    try:
        table = read_ccn_objects(ccn_path)
        rep.object_names = list(table.names)
        rep.objects = len(table.objects) or len(table.names)
        rep.object_headers_size = table.headers_size
        rep.object_types = dict(table.type_counts().most_common(5))
    except Exception as exc:
        rep.notes.append(f"对象名读不出来：{exc}")

    app = pr.layout.app
    if rep.width and rep.height:
        app.width, app.height = rep.width, rep.height
    if rep.fps:
        app.frame_rate = rep.fps
    if rep.app_name:
        app.app_name = rep.app_name
        pr.layout.preamble.app_name = rep.app_name   # 前段里的工程名也一起换
    if rep.author:
        app.author = rep.author
    if rep.copyright:
        app.copyright = rep.copyright

    data = mfa.build_project(pr)
    mfa.read_project(data)          # 自检：写出来必须能被自己读回去
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    rep.out = str(out)
    rep.out_size = len(data)
    try:
        rep.manifest = write_manifest(str(out) + ".json", rep, table)
    except Exception as exc:
        rep.notes.append(f"清单写不出来：{exc}")
    return rep
