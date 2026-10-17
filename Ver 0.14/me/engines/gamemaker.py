# -*- coding: utf-8 -*-
"""GameMaker 提取（自研解析，不依赖任何外部 CLI）。

0.13 及以前是借 UndertaleModTool 的 CLI：先跑它，跑不动就把它消化不了的
chunk 原长清空再跑一次。UTMT 只认它版本表里那些 GameMaker 版本，表外的一
个字节都不给；而实测确实有「容器完好、链完整、UTMT 却硬失败」的文件
（``Downloads/data.win``，61MB，2583 个精灵一个都拿不出来）。

0.14 起改成自研，见 ``me/formats/gamemaker_win.py``。那套解析器不查版本表，
chunk 内布局按文件自己实测出来（指针差量条目尺寸、扫字段定位 blob 与帧数组
偏移），所以上面那个文件能正常拆出 2583 个精灵。

实测（本机样本）：

    Undertale True Genocide/data.win   7.8MB  bytecode 16
        248 精灵 / 517 帧 / 552 TPAG / 3595 字符串
    DELTARUNE SWORD ROUTE chapter1    23.6MB  bytecode 17（PNG 纹理）
        1106 精灵 / 2990 帧 / 14726 字符串
    Deltarune chapter1（原版）        13.3MB  bytecode 17（2zoq 纹理）
        1097 精灵 / 2937 帧 / 14177 字符串
    Downloads/data.win                61.0MB  bytecode 16  ← UTMT 读不了
        2583 精灵 / 6333 帧 / 63977 字符串
"""

import os
from pathlib import Path
from typing import Optional

from me.collect import gather
from me.engines.base import Context, EngineError, Outcome
from me.formats import gamemaker_extract
from me.formats.gamemaker_win import NotGamemaker, ParseError, WinFile

KEY = "gamemaker"
NAME = "GameMaker"

# GameMaker 的数据文件可能的文件名
DATA_NAMES = ("data.win", "game.droid", "game.ios", "game.unx", "game.emu",
              "data.win.unx", "game.win")


def find_data_file(target) -> Optional[Path]:
    """找到 GameMaker 数据文件。"""
    target = Path(target)
    if target.is_file():
        return target
    for name in DATA_NAMES:
        for p in target.rglob(name):
            return p
    for p in target.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".win", ".droid", ".ios", ".unx") \
                and p.stem.lower() in ("data", "game"):
            return p
    return None


def extract(ctx: Context) -> Outcome:
    target = Path(ctx.target)
    out = Outcome()

    data_file = find_data_file(target)
    if data_file is None:
        raise EngineError("未找到 data.win / game.droid 之类的 GameMaker 数据文件")

    try:
        w = WinFile.load(data_file)
    except NotGamemaker as exc:
        raise EngineError(f"{data_file.name} 不是 GameMaker 容器：{exc}")
    except ParseError as exc:
        raise EngineError(f"{data_file.name} 结构读不下去：{exc}")
    except OSError as exc:
        raise EngineError(f"{data_file.name} 读不出来：{exc}")

    out.notes.append(f"数据文件: {data_file.name}")
    out.notes.append(
        f"bytecode {w.bytecode()}，chunk {len(w.order)} 个"
        + ("" if w.chain_ok else "，链不完整"))
    out.notes.append("解析器: 自研（无外部 CLI）")

    pages = os.environ.get("ME_TEXTURE_PAGES") == "1"
    counts, notes = gamemaker_extract.run(
        w, ctx.writer, progress=ctx.progress, deep=ctx.deep,
        export_pages=pages)
    out.notes += notes
    n = sum(counts.values())

    # ---- data 文件旁边散落的资源（有些游戏音频是外置的）----
    # progress 必须往下传：不传的话这些文件写出去了却不记账，
    # 进度条的 total 就会比 result.written 少一截（实测 Undertale 603 vs 620）。
    if target.is_dir():
        extra = gather.gather_tree(
            target, ctx.writer,
            skip_exts={".win", ".droid", ".ios", ".unx", ".exe", ".dll"},
            origin=str(target), progress=ctx.progress)
        if extra:
            n += extra
            out.notes.append(f"data 文件之外另收集 {extra} 个文件")

    out.written = n
    out.detail = f"从 {data_file.name} 导出 {n} 个文件"
    if out.written == 0:
        out.fallback = True
    return out
