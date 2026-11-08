# -*- coding: utf-8 -*-
"""GameMaker 提取（两条通道，V0.17）。

默认**第三方**：``tools/utmt/UndertaleModCli.exe dump``，导出字符串、反编译代码、
纹理页与精灵帧。UTMT 只认它版本表里的 GameMaker 版本，表外的一个字节都不给。

**自研**（实验性功能 + 自研开关同时启用时）：``me/formats/gamemaker_win.py``
这一套自己写的解析器。它不查版本表，chunk 内布局按文件自己实测出来，所以
UTMT 读不了的文件它也能拆 —— 实测 ``Downloads/data.win``（61MB，2583 精灵）
UTMT 硬失败，自研能出全套。

两条通道的覆盖不一样，报表里会写清楚：

    第三方 UTMT  字符串 / GML 代码 / 纹理页 / 精灵帧
    自研         以上之外还有音频、字体、路径、选项表

第三方解不出东西时自动退回自研（不放弃任何一个能拆的文件）。

实测（自研通道，本机样本）：

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
from me.tools import paths as tool_paths
from me.tools import stream

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


def _rebuild_first(data_file: Path, ctx: Context, out: Outcome) -> Path:
    """先重建结构，再拿重建后的文件去解析（V0.15「修复/重建」）。

    重建不拆包、不改文件长度：chunk 内指向自己块内的指针按新布局重算，
    指向块外的原样保留。重建失败（模型不成立）就退回原文件，
    绝不拿一个没把握的结果去覆盖用户的输入。
    """
    from me.formats import gamemaker_rebuild as rb

    w = WinFile.load(data_file)
    bad = [f for f in rb.diagnose(w) if f.level == "err"]
    for f in bad[:6]:
        out.notes.append(f"{f.chunk}: {f.message}")
    if not bad:
        out.notes.append("重建: 跳过, 无结构问题")
        return data_file

    dest = Path(ctx.work_dir) / (data_file.stem + "_rebuilt" + data_file.suffix)
    try:
        rep = rb.rebuild_file(data_file, dest)
    except Exception as exc:                       # noqa: BLE001
        out.notes.append(f"重建放弃: {type(exc).__name__}: {exc}")
        return data_file

    out.notes.append(f"重建产物: {dest.name}")
    out.notes += list(rep.rebuilt)
    out.notes += list(rep.notes)
    return dest


def extract(ctx: Context) -> Outcome:
    """两条通道（V0.17）：默认第三方 UTMT，勾了自研才走自研解析器。

    第三方解不出来（UTMT 的版本表里没有这个 GameMaker 版本、文件结构被改过）
    时自动退回自研 —— 自研本来就是为了兜住这一类文件，没理由让它闲着。
    """
    target = Path(ctx.target)
    out = Outcome()

    data_file = find_data_file(target)
    if data_file is None:
        raise EngineError("未找到 data.win / game.droid 数据文件")

    if ctx.use_self:
        out.notes.append("拆包: 自研（me/formats/gamemaker_*.py）")
        n = _extract_self(data_file, ctx, out)
    else:
        out.notes.append("拆包: 第三方 UndertaleModTool CLI（dump）")
        n = 0
        try:
            n = _extract_third(data_file, ctx, out)
        except EngineError as exc:
            out.notes.append(f"第三方通道不可用: {exc}")
        if n == 0:
            out.notes.append("第三方没解出内容, 退回自研解析器")
            n = _extract_self(data_file, ctx, out)

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


# UTMT dump 的导出项。实测（tools/utmt 这份）：
#   -s 字符串      -> strings.txt
#   -t 纹理        -> EmbeddedTextures/Texture N.png
#   --sprites      -> Sprites/<精灵>_<帧>.png
#   -c UMT_DUMP_ALL-> CodeEntries/*.gml（全部反编译代码）
# UTMT 不导音频、不导字体，这两类只有自研通道有。
UTMT_DUMP_FLAGS = ("-s", "-t", "--sprites")
UTMT_CODE_ALL = "UMT_DUMP_ALL"


def _extract_third(data_file: Path, ctx: Context, out: Outcome) -> int:
    """第三方通道：把 UTMT dump 出来的东西收进 writer，返回写出数量。"""
    cli = tool_paths.find("utmt")
    if cli is None:
        return 0

    dest = ctx.sub_work("utmt_dump")
    argv = ([str(cli), "dump", str(data_file), "-o", str(dest)]
            + list(UTMT_DUMP_FLAGS) + ["-c", UTMT_CODE_ALL])
    res = stream.run_live(argv, timeout=_utmt_timeout(), echo=ctx.verbose,
                          banner=f"UTMT dump {data_file.name}：")
    if not res.ok:
        tail = res.tail(1).strip()[:200]
        out.notes.append(f"UTMT 退出码 {res.code}" + (f": {tail}" if tail else ""))
        return 0

    n = gather.gather_tree(dest, ctx.writer, origin=str(data_file),
                           progress=ctx.progress)
    out.notes.append(
        f"UTMT 导出 {n} 个文件（字符串 / 代码 / 纹理 / 精灵帧）")
    out.notes.append("UTMT 不导音频与字体：要这两类请开自研通道")
    return n


def _extract_self(data_file: Path, ctx: Context, out: Outcome) -> int:
    """自研通道：WinFile 解析 + gamemaker_extract 导出。"""
    if getattr(ctx, "fix", False):
        try:
            data_file = _rebuild_first(data_file, ctx, out)
        except Exception as exc:                   # noqa: BLE001
            out.notes.append(f"重建出错, 用原文件: {type(exc).__name__}: {exc}")

    try:
        w = WinFile.load(data_file)
    except NotGamemaker as exc:
        raise EngineError(f"{data_file.name} 不是 GameMaker 容器: {exc}")
    except ParseError as exc:
        raise EngineError(f"{data_file.name} 结构解析失败: {exc}")
    except OSError as exc:
        raise EngineError(f"{data_file.name} 读取失败: {exc}")

    out.notes.append(f"数据文件: {data_file.name}")
    out.notes.append(
        f"bytecode {w.bytecode()}, chunk {len(w.order)} 个"
        + ("" if w.chain_ok else ", 链不完整"))

    pages = os.environ.get("ME_TEXTURE_PAGES") == "1"
    counts, notes = gamemaker_extract.run(
        w, ctx.writer, progress=ctx.progress, deep=ctx.deep,
        export_pages=pages)
    out.notes += notes
    return sum(counts.values())


def _utmt_timeout() -> int:
    from me.config import TOOL_TIMEOUT
    return TOOL_TIMEOUT
