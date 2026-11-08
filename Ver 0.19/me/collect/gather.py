# -*- coding: utf-8 -*-
"""目录遍历收集。

把一棵目录树里的文件按分类收进 writer。这是最通用的提取方式：
对已经解包的游戏目录直接可用；对容器格式，解包后再调它。
"""

import os
from pathlib import Path
from typing import Callable, Iterable, Optional

from me.collect import kinds


def list_files(src, skip_exts: Iterable[str] = (), exts: Iterable[str] = ()):
    """列出目录下所有文件，先按扩展名过滤，避免白跑一遍。"""
    src = Path(src)
    skip = {e.lower() for e in skip_exts}
    want = {e.lower() for e in exts}
    out = []
    for root, dirs, names in os.walk(src):
        dirs[:] = [d for d in dirs if d not in ("__MACOSX",)]
        for fn in names:
            ext = os.path.splitext(fn)[1].lower()
            if ext in skip:
                continue
            if want and ext not in want:
                continue
            out.append(Path(root) / fn)
    return out


def gather_tree(src, writer, rel_prefix: str = "", origin: str = "",
                skip_exts: Iterable[str] = (), exts: Iterable[str] = (),
                progress: Optional[Callable] = None) -> int:
    """把 src 目录树里的文件收进 writer，返回写出数量。"""
    src = Path(src)
    if not src.exists():
        return 0

    if src.is_file():
        rel = rel_prefix or src.name
        return 1 if writer.copy_from(kinds.classify(rel), rel, src,
                                     origin or str(src)) else 0

    files = list_files(src, skip_exts=skip_exts, exts=exts)
    total = len(files)
    written = 0
    for i, path in enumerate(files):
        rel = path.relative_to(src).as_posix()
        if rel_prefix:
            rel = f"{rel_prefix}/{rel}"
        if progress:
            progress(i + 1, total, path.name[:32])
        if writer.copy_from(kinds.classify(rel), rel, path, origin or str(src)):
            written += 1
    return written


def describe_tree(src, limit: int = 12) -> list:
    """统计目录下各扩展名数量，用于「没有可提取内容」时给出说明。"""
    from collections import Counter
    src = Path(src)
    c = Counter()
    for path in list_files(src):
        c[os.path.splitext(path.name)[1].lower() or "(无扩展名)"] += 1
    return c.most_common(limit)
