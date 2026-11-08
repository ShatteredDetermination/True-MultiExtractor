# -*- coding: utf-8 -*-
"""把解析出来的 GameMaker 资源落盘，同时驱动**全局进度**。

分工：``gamemaker_win`` 只负责把结构读出来，``gamemaker_image`` 只负责
像素，这个模块负责「哪个资源写到哪里、叫什么名字」。

全局进度为什么这里能做
----------------------
0.13 及以前没有全局进度，不是懒：那时资源是交给 UndertaleModTool 导的，
它是个黑盒 —— CLI 不知道里面有多少活，只能按「跑完一个子任务」报一次，
于是 total 一会儿 16 一会儿 254，进度条来回跳。

自研解析器把这件事变成了可计算的：精灵数、帧数、无人引用的图集块、音频
条目、要导出的页数，**开工前就能全部数出来**。所以这里的 total 从开工到
收工一个值都不变，done 只增不减 —— 进度条才是真的全局进度条。

纹理页是按需解码的：先把所有切片按 sheet 分组，一次只解一张页、切完就丢，
所以 160MB 的 data.win 也不会把内存吃爆。
"""

import re
from typing import Callable, Dict, List, Optional, Tuple

from me.collect import pngwrite
from me.formats import gamemaker_image as gimg
from me.formats.gamemaker_win import WinFile

# 音频按魔数认，认不出就按 .bin 落盘（总比丢了好）
_AUDIO_SIGS = (
    (b"RIFF", "wav"),
    (b"OggS", "ogg"),
    (b"ID3", "mp3"),
    (b"\xff\xfb", "mp3"),
    (b"\xff\xf3", "mp3"),
    (b"\xff\xf2", "mp3"),
    (b"fLaC", "flac"),
)

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe(name: str, limit: int = 96) -> str:
    """文件名安全化。空名给 unnamed。"""
    n = _BAD.sub("_", str(name)).strip().strip(".")
    if not n:
        n = "unnamed"
    if len(n) > limit:
        n = n[:limit]
    return n


def sniff_audio(blob: bytes) -> str:
    for sig, ext in _AUDIO_SIGS:
        if blob[:len(sig)] == sig:
            return ext
    return "bin"


def run(w: WinFile, sink, progress: Optional[Callable] = None,
        deep: bool = True, export_pages: bool = False) -> Tuple[Dict[str, int],
                                                               List[str]]:
    """把一份 data.win 里的资源写进 sink。

    sink 需要提供 write_bytes(category, rel, data, origin) / wants(category)。
    progress(done, total, label) 里的 total 全程不变。
    返回 (各分类数量, 说明)。
    """
    counts: Dict[str, int] = {}
    notes: List[str] = []

    def bump(cat: str, n: int = 1) -> None:
        counts[cat] = counts.get(cat, 0) + n

    origin = str(w.path) if w.path else "data.win"

    # ---------------------------------------------------------- 数总工作量
    want_text = sink.wants("text")
    want_audio = sink.wants("audio")
    want_img = bool(deep) and sink.wants("image")

    # 外置音频清单也是一份产物，总数里要给它留一格
    ext_sounds = ([s for s in w.sounds() if s.file]
                  if (want_audio and want_text) else [])
    audo = w.audo() if want_audio else []
    tpag = w.tpag() if want_img else []
    sprites = w.sprites() if want_img else []
    backgrounds = w.backgrounds() if want_img else []
    textures = w.textures() if want_img else []

    # 切片清单：按纹理页分组，一次只解一张页
    jobs: Dict[int, List[Tuple[int, str, str]]] = {}

    def add(sheet: int, ti: int, rel: str, label: str) -> None:
        jobs.setdefault(sheet, []).append((ti, rel, label))

    orphans = 0
    if tpag and textures:
        used = set()
        for s in sprites:
            base = _safe(s.name)
            n_fr = len(s.frames)
            for fi, ti in enumerate(s.frames):
                if 0 <= ti < len(tpag):
                    used.add(ti)
                    # 单帧的不单独占目录，省得几千个精灵就是几千个文件夹
                    rel = (f"sprites/{base}.png" if n_fr <= 1
                           else f"sprites/{base}/{base}_{fi:03d}.png")
                    add(tpag[ti].sheet, ti, rel, base)

        for b in backgrounds:
            base = _safe(b.name)
            for fi, ti in enumerate(b.frames):
                if 0 <= ti < len(tpag):
                    used.add(ti)
                    add(tpag[ti].sheet, ti,
                        f"backgrounds/{base}_{fi:03d}.png", base)

        # 没有任何精灵/背景引用的 TPAG 记录也是真实的图集块（实测 Undertale
        # 552 条里就有 14 条没人引用）。丢掉它们等于少收资源。
        for ti in range(len(tpag)):
            if ti in used:
                continue
            r = tpag[ti]
            if r.w > 0 and r.h > 0:
                add(r.sheet, ti, f"atlas/tpag_{ti:05d}.png", f"tpag_{ti:05d}")
                orphans += 1

    n_slices = sum(len(v) for v in jobs.values())
    total = (n_slices + len(audo) + (1 if want_text else 0)
             + (1 if ext_sounds else 0))
    if export_pages:
        total += len(jobs)
    if total <= 0:
        total = 1                     # 空文件也别让百分比除零

    done = 0

    def tick(label: str) -> None:
        """唯一的推进点。done 只增不减，total 全程不变。"""
        nonlocal done
        done += 1
        if progress and done <= total:
            progress(done, total, label)

    if progress:
        # 先报一次 0，GUI 立刻拿到总数，不用等第一个切片出来
        progress(0, total, "解析完成，开始导出")

    # ---------------------------------------------------------- 文本
    if want_text:
        strs = w.strings()
        sink.write_bytes("text", "strings.txt",
                         "\n".join(strs).encode("utf-8"), origin)
        bump("text")
        notes.append(f"字符串表 {len(strs)} 条")
        tick("strings.txt")

    # ---------------------------------------------------------- 音频
    if want_audio:
        for i, (off, size) in enumerate(audo):
            name = f"audio_{i:04d}"
            if size > 0 and off + 4 + size <= len(w.raw):
                blob = w.audo_blob(off, size)
                ext = sniff_audio(blob)
                name = f"{name}.{ext}"
                # 分类目录由 writer 加，这里的 rel 不要再带 audio/
                sink.write_bytes("audio", name, blob, f"{origin} AUDO[{i}]")
                bump("audio")
            tick(name)

        external = [s for s in w.sounds() if s.file]
        if external and want_text:
            lines = [f"{s.name}\t{s.file}" for s in external]
            sink.write_bytes("text", "external_audio.txt",
                             "\n".join(lines).encode("utf-8"), origin)
            notes.append(f"外置音频引用 {len(external)} 条"
                         f"（见 text/external_audio.txt）")
            tick("external_audio.txt")

    if not deep:
        notes.append("不解容器模式：只收了字符串与音频")
        return counts, notes

    # ---------------------------------------------------------- 图像
    if not want_img:
        return counts, notes

    if not tpag or not textures:
        notes.append("没有 TPAG / TXTR，跳过图像")
        return counts, notes

    failed_pages = 0
    bad_kinds: Dict[str, int] = {}

    for sheet in sorted(jobs):
        t = textures[sheet] if sheet < len(textures) else None
        rgba = None
        pw = ph = 0
        kind = ""
        if t is not None:
            try:
                pw, ph, rgba, kind = gimg.decode_texture(w.raw, t.blob)
                bad_kinds[kind] = bad_kinds.get(kind, 0) + 1
            except gimg.ImageError as exc:
                failed_pages += 1
                if len(notes) < 12:
                    notes.append(f"纹理页 {sheet} 解码失败: {exc}")
        else:
            failed_pages += 1

        # 一页里的每一块都要 tick，哪怕这一页解不开 ——
        # 否则 done 到不了 total，进度条永远差一截
        for ti, rel, label in jobs[sheet]:
            if rgba is not None:
                r = tpag[ti]
                try:
                    if r.w > 0 and r.h > 0:
                        px = gimg.crop(rgba, pw, ph, r.x, r.y, r.w, r.h)
                        sink.write_bytes(
                            "image", rel,
                            pngwrite.encode_rgba(r.w, r.h, px), origin)
                        bump("image")
                except ValueError:
                    pass
            tick(label)

        if export_pages:
            page = None
            if rgba is not None:
                try:
                    if kind == "png":
                        # 本来就是 PNG，原样搬出去，省一次重编码
                        _pw, _ph, end = gimg.png_info(w.raw, t.blob)
                        page = bytes(w.raw[t.blob:end])
                    else:
                        page = pngwrite.encode_rgba(pw, ph, rgba, level=6)
                except (gimg.ImageError, ValueError):
                    page = None
            if page:
                sink.write_bytes("image", f"pages/page_{sheet:03d}.png",
                                 page, origin)
                bump("image")
            tick(f"page_{sheet:03d}.png")

        rgba = None                   # 一页切完立刻放掉

    if orphans:
        notes.append(f"无人引用的图集块 {orphans} 个（atlas/）")
    notes.append(f"精灵 {len(sprites)} 个 / 帧 {sum(len(s.frames) for s in sprites)}")
    notes.append(f"背景 {len(backgrounds)} 个")
    notes.append(f"纹理页 {len(textures)} 张，解码 {len(bad_kinds)} 张"
                 + (f"，失败 {failed_pages} 张" if failed_pages else ""))
    if bad_kinds:
        notes.append("页格式: " + " ".join(
            f"{k}x{v}" for k, v in sorted(bad_kinds.items())))
    return counts, notes
