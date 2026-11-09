# -*- coding: utf-8 -*-
"""CCN（成品）→ MFA（工程）的骨架级转换 —— V0.19。

现在能做的和还做不到的，都写在这：

**能做**：拿一份 MFA 当骨架（默认用 `Template.mfa`），把成品里的
应用信息（工程名 / 作者 / 版权）与图像库搬进去，写出一个结构完整、
能被我们自己的 `mfa.read_project` 读回来的 `.mfa`。

**还做不到**：帧内对象 / 实例 / 事件的重建。所以产出的工程里帧是骨架自带的
空帧（尺寸、背景色取自骨架），**不是**原游戏的布局。这一点不许在对外说明里
含糊：产物是「资源级 mfa」，能打开、能看到图像资源，但看不到原游戏的摆位。

图像编码：成品 2.5+ 的图像解成 RGBA（`imagecodec.to_rgba`），写进 MFA 时
按 32 位条目（`graphic_mode = 8`）存 BGRA、zlib 压缩。
"""

import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from me.formats.ctf25 import container, imagebank, imagecodec
from me.formats.ctf25 import chunks as chunk_mod
from me.formats.ctf25 import ids, mfa


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
    width: int = 0
    height: int = 0
    out_size: int = 0
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{self.images_written}/{self.images_total} 张图像，"
                f"{self.frames} 帧，{self.out_size:,} B → {self.out}")


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
    """读成品里每帧的**帧名**（0x3335 是普通 zlib chunk，读得出来）。

    帧头（0x3334）在 2.5+ 里是 flag=3（zlib + 加密），数据是异或过的，
    所以宽高/背景色现在读不出来 —— 这里只返回名字，不猜尺寸。
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


def read_ccn_app(ccn_path):
    """读成品的应用头（0x2223）里的窗口宽高。

    布局实测（dtlg-v1.1.0.exe）：``u32 长度 / u32 版本 / u32 flags /
    u16 宽 / u16 高 / ...``，宽高在偏移 12 与 14（640x480 就是
    ``80 02 e0 01``）。只取这两个 u16，别的字段不猜。
    """
    import struct

    mm, loc = container.open_file(ccn_path)
    if loc is None:
        raise MfaBuildError("未找到 Clickteam CCN 数据（PAMU / PAME / CRUF）")
    try:
        buf = mm.mm
        chs = chunk_mod.scan(buf, loc.chunk_start, len(buf))
        ch = chunk_mod.find(chs, 0x2223)
        if ch is None:
            return 0, 0
        data = chunk_mod.load(buf, ch)
        if len(data) < 16:
            return 0, 0
        width, height = struct.unpack_from("<HH", data, 12)
        if not (0 < width <= 16384 and 0 < height <= 16384):
            return 0, 0
        return width, height
    finally:
        mm.close()


def build_from_ccn(ccn_path, out_path, template_path=None, app_name: str = "",
                   limit: Optional[int] = None, frame_names: bool = True,
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

    if frame_names:
        try:
            names = read_ccn_frames(ccn_path)
        except Exception as exc:
            names = []
            rep.notes.append(f"帧名读不出来：{exc}")
        try:
            rep.width, rep.height = read_ccn_app(ccn_path)
        except Exception as exc:
            rep.notes.append(f"应用尺寸读不出来：{exc}")
        if names:
            import copy
            base = pr.frames[0]
            pr.frames = []
            for i, nm in enumerate(names):
                f = copy.deepcopy(base)
                f.handle = i
                f.name = nm or f"Frame {i + 1}"
                if rep.width and rep.height:
                    f.width, f.height = rep.width, rep.height
                    f.last_x, f.last_y = rep.width // 2, rep.height // 2
                pr.frames.append(f)
            rep.frame_names = [f.name for f in pr.frames]
            if rep.width and rep.height:
                rep.notes.append(
                    f"帧尺寸取应用窗口 {rep.width}x{rep.height}"
                    "（成品帧头是 flag=3 加密块，逐帧宽高读不出）")
            else:
                rep.notes.append("帧尺寸取骨架；成品帧头是 flag=3 加密块")
    rep.frames = len(pr.frames)

    app = pr.layout.app
    if rep.app_name:
        app.app_name = rep.app_name
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
    return rep
