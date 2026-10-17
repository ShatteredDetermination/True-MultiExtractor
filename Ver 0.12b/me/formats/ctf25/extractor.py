# -*- coding: utf-8 -*-
"""CTF 提取入口。

把上面那些零件拼起来：定位 CCN -> 扫 chunk -> 挑出图像/音频/字体 -> 写文件。

实测样本：
  * Undertale Last Breath Scrapped.dat（66MB，Fusion 2.5 build 295）
    2138 张图 / 68 段音频
  * dtlg-v1.1.0.exe（137MB，Fusion 2.5 build 292，单文件）
    1515 张图 / 172 段音频 + 内嵌 TTF
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from me.collect import pngwrite
from me.formats.ctf25 import chunks as chunk_mod
from me.formats.ctf25 import container, fontbank, ids, imagebank, imagecodec
from me.formats.ctf25 import soundbank
from me.formats.ctf25.binary import Reader


@dataclass
class CtfReport:
    """一次 CTF 提取的统计。"""
    app_name: str = ""
    app_author: str = ""
    app_copyright: str = ""
    build: int = 0
    kind: str = ""
    chunk_count: int = 0

    images_total: int = 0
    images_written: int = 0
    sounds_total: int = 0
    sounds_written: int = 0
    fonts_total: int = 0
    fonts_written: int = 0
    pack_entries: int = 0

    notes: List[str] = field(default_factory=list)

    @property
    def written(self) -> int:
        return self.images_written + self.sounds_written + self.fonts_written


class CtfExtractError(Exception):
    pass


# ------------------------------------------------------------------ 打开
def inspect(path):
    """只看结构不提取，返回 (CcnLocation, [Chunk])。"""
    mm, loc = container.open_file(path)
    if loc is None:
        return None, None, None
    chs = chunk_mod.scan(mm, loc.chunk_start, len(mm))
    return mm, loc, chs


# ------------------------------------------------------------------ 提取
def extract(path, writer, progress: Optional[Callable] = None,
            keep_runtime: bool = False) -> CtfReport:
    """提取一个 CTF 文件。"""
    path = Path(path)
    mm, loc = container.open_file(path)
    if loc is None:
        raise CtfExtractError("没有找到 Clickteam 的 CCN 数据（缺少 PAMU/PAME/CRUF 标记）")

    try:
        return _extract_locked(mm, loc, writer, progress, keep_runtime)
    finally:
        mm.close()


def _extract_locked(mm, loc, writer, progress, keep_runtime) -> CtfReport:
    buf = mm.mm
    rep = CtfReport(build=loc.product_build, kind=loc.label,
                    pack_entries=len(loc.pack_entries))

    chs = chunk_mod.scan(buf, loc.chunk_start, len(buf))
    rep.chunk_count = len(chs)

    # ---- 应用信息 ----
    rep.app_name = _text_chunk(buf, chs, ids.CHUNK_APP_NAME, loc.unicode)
    rep.app_author = _text_chunk(buf, chs, ids.CHUNK_APP_AUTHOR, loc.unicode)
    rep.app_copyright = _text_chunk(buf, chs, ids.CHUNK_APP_COPYRIGHT, loc.unicode)

    # ---- 2.5+ 判定：看 0x2253 是否出现在图像库之前 ----
    twofiveplus = _is_25plus(chs)

    # ---- 统计总数，方便报进度 ----
    img_chunk = chunk_mod.find(chs, ids.CHUNK_IMAGES)
    snd_chunk = chunk_mod.find(chs, ids.CHUNK_SOUNDS)
    ttf_chunk = chunk_mod.find(chs, ids.CHUNK_FONTS_2)
    meta_chunk = chunk_mod.find(chs, ids.CHUNK_FONTS)

    rep.images_total = _count_of(buf, img_chunk)
    rep.sounds_total = _count_of(buf, snd_chunk)
    rep.fonts_total = _count_of(buf, meta_chunk)

    total = rep.images_total + rep.sounds_total + rep.fonts_total
    done = 0

    def tick(label: str = ""):
        nonlocal done
        done += 1
        if progress:
            progress(done, total, label)

    # ---- 图像 ----
    if img_chunk and writer.wants("image"):
        data = chunk_mod.load(buf, img_chunk)
        got = 0
        failed = 0
        for i, entry in enumerate(imagebank.parse_images(
                data, twofiveplus=twofiveplus, build=loc.product_build)):
            try:
                rgba = imagecodec.to_rgba(entry)
                name = f"{entry.handle:05d}_{entry.width}x{entry.height}.png"
                payload = pngwrite.encode_rgba(entry.width, entry.height, rgba)
                if writer.write_bytes("image", name, payload, "ctf:images"):
                    got += 1
            except imagecodec.ImageCodecError as exc:
                failed += 1
                if failed <= 3:
                    rep.notes.append(f"图像 #{entry.handle} 跳过: {exc}")
            tick(f"图像 {i + 1}")
        rep.images_written = got
        if failed:
            rep.notes.append(f"有 {failed} 张图因为格式不支持被跳过")
    elif img_chunk:
        rep.notes.append("已跳过图像（未选择该分类）")

    # ---- 音频 ----
    if snd_chunk and writer.wants("audio"):
        data = chunk_mod.load(buf, snd_chunk)
        got = 0
        for i, entry in enumerate(soundbank.parse_sounds(data, compressed=True)):
            ext = entry.ext
            name = f"{entry.handle:04d}_{entry.safe_name()}{ext}"
            if writer.write_bytes("audio", name, entry.data, "ctf:sounds"):
                got += 1
            tick(f"音频 {i + 1}")
        rep.sounds_written = got
    elif snd_chunk:
        rep.notes.append("已跳过音频（未选择该分类）")

    # ---- 字体 ----
    if writer.wants("font"):
        got = 0
        if ttf_chunk:
            data = chunk_mod.load(buf, ttf_chunk)
            for i, font in enumerate(fontbank.parse_truetype(data)):
                ext = fontbank.font_ext(font)
                if writer.write_bytes("font", f"embedded_{i:02d}{ext}", font,
                                      "ctf:truetype"):
                    got += 1
        if meta_chunk:
            data = chunk_mod.load(buf, meta_chunk)
            for meta in fontbank.parse_font_meta(data, True, loc.unicode):
                rep.notes.append(
                    f"字体声明: {meta.face_name or '(无名)'} "
                    f"{meta.height}px 粗体={meta.weight}")
        rep.fonts_written = got
    else:
        rep.notes.append("已跳过字体（未选择该分类）")

    # ---- 打包的运行时文件（可选）----
    if keep_runtime and loc.pack_entries:
        n = _dump_pack(buf, loc, writer)
        rep.notes.append(f"另外导出了 {n} 个打包的运行时文件")

    # ---- 应用信息写一份文本 ----
    if writer.wants("text"):
        writer.write_bytes("text", "appinfo.txt",
                           _appinfo_text(rep, loc).encode("utf-8"), "ctf:meta")

    return rep


# ------------------------------------------------------------------ 小工具
def _is_25plus(chs) -> bool:
    """0x2253 出现在图像库之前就按 2.5+ 解析图像。"""
    idx25 = idx_img = None
    for i, ch in enumerate(chs):
        if ch.id == ids.CHUNK_25_OBJECT_HEADERS and idx25 is None:
            idx25 = i
        elif ch.id == ids.CHUNK_IMAGES and idx_img is None:
            idx_img = i
    if idx25 is None:
        return False
    return idx_img is None or idx25 < idx_img


def _count_of(buf, chunk) -> int:
    """读 bank chunk 开头的「条目数」。"""
    if chunk is None:
        return 0
    try:
        data = chunk_mod.load(buf, chunk)
    except chunk_mod.ChunkError:
        return 0
    if len(data) < 4:
        return 0
    n = Reader(data).i32()
    return n if 0 <= n <= 5_000_000 else 0


def _text_chunk(buf, chs, chunk_id: int, unicode: bool) -> str:
    """读一个纯文本 chunk（应用名/作者/版权）。"""
    ch = chunk_mod.find(chs, chunk_id)
    if ch is None:
        return ""
    try:
        data = chunk_mod.load(buf, ch)
    except chunk_mod.ChunkError:
        return ""
    if unicode:
        text = data.decode("utf-16-le", "replace")
    else:
        text = data.decode("latin-1", "replace")
    return text.replace("\x00", "").strip()


def _appinfo_text(rep: CtfReport, loc) -> str:
    lines = [
        "=== Clickteam Fusion 应用信息 ===",
        f"名称    : {rep.app_name or '(无)'}",
        f"作者    : {rep.app_author or '(无)'}",
        f"版权    : {rep.app_copyright or '(无)'}",
        f"格式    : {loc.label}",
        f"构建号  : {loc.product_build}",
        f"运行时  : {loc.runtime_version}.{loc.runtime_subversion}",
        f"chunk 数: {rep.chunk_count}",
        f"打包项  : {rep.pack_entries}",
        "",
        f"图像    : {rep.images_written}/{rep.images_total}",
        f"音频    : {rep.sounds_written}/{rep.sounds_total}",
        f"字体    : {rep.fonts_written}",
    ]
    if rep.notes:
        lines += ["", "备注:"] + [f"  - {n}" for n in rep.notes]
    return "\n".join(lines) + "\n"


def _dump_pack(buf, loc, writer) -> int:
    """导出 wwww 表里的打包文件（运行时 dll / mfx，一般不是游戏资源）。"""
    import zlib
    n = 0
    for e in loc.pack_entries:
        if not e.name:
            continue
        raw = bytes(buf[e.data_offset:e.data_offset + e.size])
        if e.compressed:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                continue
        if writer.write_bytes("other", f"runtime/{e.name}", raw, "ctf:pack"):
            n += 1
    return n


def describe(rep: CtfReport) -> str:
    parts = []
    if rep.images_written:
        parts.append(f"图片 {rep.images_written}")
    if rep.sounds_written:
        parts.append(f"音频 {rep.sounds_written}")
    if rep.fonts_written:
        parts.append(f"字体 {rep.fonts_written}")
    return " / ".join(parts) if parts else "无"
