# -*- coding: utf-8 -*-
"""Electron / NW.js 游戏提取。

主要工作是把 resources/app.asar 解开。
asar 之外还有一堆 Chromium 运行时文件（dll / pak / bin / license），
那些不是游戏资源，默认跳过，免得输出目录里全是垃圾。
"""

import os
from pathlib import Path

from me.collect import gather
from me.engines.base import Context, Outcome
from me.formats import asar

KEY = "electron"
NAME = "Electron"

# 这些后缀是 Chromium 运行时，不是游戏资源
SKIP_EXT = {
    ".dll", ".so", ".dylib", ".exe", ".pak", ".bin", ".dat", ".pdb",
    ".lib", ".exp", ".node", ".asi",
}
# 这些文件名同理
SKIP_NAMES = {
    "license.electron.txt", "licenses.chromium.html",
    "vk_swiftshader_icd.json", "snapshot_blob.bin",
    "v8_context_snapshot.bin", "icudtl.dat",
}

# asar 可能的存放位置
ASAR_HINTS = (
    "resources/app.asar",
    "app.asar",
    "resources/app.asar.unpacked/app.asar",
)


def find_asar(target: Path):
    """找到 asar 文件。"""
    target = Path(target)
    if target.is_file() and target.name.lower().endswith(".asar"):
        return target
    for hint in ASAR_HINTS:
        p = target / hint
        if p.exists():
            return p
    # 兜底：在目录里搜
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d.lower() not in ("node_modules",)]
        for fn in files:
            if fn.lower().endswith(".asar"):
                return Path(root) / fn
    return None


def extract(ctx: Context) -> Outcome:
    target = Path(ctx.target)
    out = Outcome()

    asar_path = find_asar(target)

    if asar_path and ctx.deep:
        n, total, dirs = asar.summarize(asar_path)
        out.notes.append(
            f"app.asar: {n} 个文件 / {_mb(total)} / {dirs} 个目录")
        written = asar.extract(asar_path, ctx.writer,
                               origin=str(asar_path),
                               progress=ctx.progress)
        out.written += written
        out.detail = f"从 {asar_path.name} 解出 {written} 个文件"
        if ctx.categories and len(ctx.categories) < 3:
            out.notes.append("asar 内文件多")
    elif asar_path:
        out.notes.append(f"发现 {asar_path.name}；不解容器模式，未解包")

    # asar 之外的散落资源（有些游戏把音频/图片放在外面）
    if target.is_dir():
        n = gather.gather_tree(
            target, ctx.writer,
            skip_exts=SKIP_EXT | {".asar"},
            origin=str(target),
            progress=ctx.progress,
        )
        # gather 会把 SKIP_NAMES 之外的也收进来，这里单独排除
        out.written += n
        if n:
            out.notes.append(f"asar 之外另收集 {n} 个文件")

    if out.written == 0:
        out.fallback = True
    return out


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"
