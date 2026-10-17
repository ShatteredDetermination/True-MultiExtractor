# -*- coding: utf-8 -*-
"""Clickteam Fusion 提取。

不用 CTFAK（容易崩），走自写的纯 Python 解析器 me/formats/ctf25。

支持两种形态：
  * 目录 + ``.dat`` 数据文件（scattered 发布）
  * 单文件 exe（CCN 数据拼在 exe 尾部）
"""

from pathlib import Path
from typing import List

from me.collect import gather
from me.engines.base import Context, EngineError, Outcome
from me.formats.ctf25 import container, extractor

KEY = "clickteam"
NAME = "Clickteam Fusion"

# 候选数据文件的后缀，按优先级排
DATA_EXT = (".dat", ".ccn", ".cct", ".exe", ".scr")

# 运行时文件不是游戏资源，但有些资源确实藏在里面，默认还是收一遍散落文件
SKIP_RUNTIME_EXT = {".dll", ".mfx", ".mvx", ".ift", ".sft", ".exe"}


def find_ctf_files(target: Path) -> List[Path]:
    """找出目录下所有含 CCN 数据的文件，大的排前面。"""
    target = Path(target)
    if target.is_file():
        return [target]

    cands: List[Path] = []
    for ext in DATA_EXT:
        cands += sorted(target.glob(f"*{ext}"))
    # 去重并保持顺序
    seen = set()
    uniq = []
    for p in cands:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)

    # 结构校验：只有真的含 CCN 数据才算
    good = []
    for p in uniq:
        try:
            mm, loc = container.open_file(p)
        except Exception:  # noqa: BLE001 - 读不动就跳过
            continue
        if loc is not None:
            good.append((p.stat().st_size, p, loc))
            mm.close()
    good.sort(key=lambda t: -t[0])
    return [p for _sz, p, _loc in good]


def extract(ctx: Context) -> Outcome:
    target = Path(ctx.target)
    out = Outcome()

    if not ctx.deep:
        out.notes.append("当前为「不解容器」模式，只收集散落文件")
        out.fallback = True
        return out

    files = find_ctf_files(target)
    if not files:
        raise EngineError(
            "没有找到含 CCN 数据的文件（需要 PAMU / PAME / CRUF 标记）。\n"
            "      如果这是加壳版本，先用 Enigma 脱壳再试。")

    total_imgs = total_snds = total_fonts = 0
    for path in files:
        try:
            rep = extractor.extract(path, ctx.writer, progress=ctx.progress,
                                    keep_runtime=False)
        except (extractor.CtfExtractError, OSError) as exc:
            out.notes.append(f"{path.name}: {exc}")
            continue

        total_imgs += rep.images_written
        total_snds += rep.sounds_written
        total_fonts += rep.fonts_written

        if rep.app_name:
            out.notes.append(f"应用名: {rep.app_name}")
        if rep.app_author:
            out.notes.append(f"作者: {rep.app_author}")
        out.notes.append(
            f"{path.name}: {rep.kind} build {rep.build}, "
            f"{rep.chunk_count} 个 chunk -> 图片 {rep.images_written}/"
            f"{rep.images_total}, 音频 {rep.sounds_written}/{rep.sounds_total}")
        out.notes += rep.notes

    out.written = total_imgs + total_snds + total_fonts
    out.detail = (f"图片 {total_imgs} / 音频 {total_snds} / 字体 {total_fonts}")

    # 数据文件之外散落的资源（有些游戏把说明文件、额外素材放在外面）
    if target.is_dir():
        n = gather.gather_tree(target, ctx.writer,
                               skip_exts=SKIP_RUNTIME_EXT | {".dat", ".ccn"},
                               origin=str(target))
        if n:
            out.written += n
            out.notes.append(f"数据文件之外另收集 {n} 个文件")

    if out.written == 0:
        out.fallback = True
    return out
