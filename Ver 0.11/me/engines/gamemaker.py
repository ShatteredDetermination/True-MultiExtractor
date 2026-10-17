# -*- coding: utf-8 -*-
"""GameMaker 提取（借 UndertaleModTool 的 CLI）。

UTMT 自带的导出脚本是交互式的（弹目录选择框），无人值守跑不了。
所以本工具在 tools/utmt/Scripts/MultiExtractor/ 下放了自己的脚本，
输出目录通过环境变量 ME_OUT 传递。

实测：
  * Undertale True Genocide/data.win（7.8MB）-> 552 图 / 98 音频 / 3595 字符串
"""

import os
from pathlib import Path
from typing import List, Optional

from me.collect import gather
from me.engines.base import Context, EngineError, Outcome
from me.tools import paths as tool_paths
from me.tools import stream

KEY = "gamemaker"
NAME = "GameMaker"

# GameMaker 的数据文件可能的文件名
DATA_NAMES = ("data.win", "game.droid", "game.ios", "game.unx", "game.emu",
              "data.win.unx", "game.win")

# 我们自己写的非交互导出脚本
SCRIPT_DIR = Path("Scripts") / "MultiExtractor"
SCRIPTS = ("ME_ExportImages.csx", "ME_ExportSounds.csx", "ME_ExportStrings.csx")


def find_data_file(target) -> Optional[Path]:
    """找到 GameMaker 数据文件。"""
    target = Path(target)
    if target.is_file():
        return target
    # 先找顶层
    for name in DATA_NAMES:
        for p in target.rglob(name):
            return p
    for p in target.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".win", ".droid", ".ios", ".unx") \
                and p.stem.lower() in ("data", "game"):
            return p
    return None


def _script_paths(cli: Path) -> List[Path]:
    """定位我们自己的脚本，找不到就返回空。"""
    base = cli.parent / SCRIPT_DIR
    out = []
    for name in SCRIPTS:
        p = base / name
        if p.exists():
            out.append(p)
    return out


def extract(ctx: Context) -> Outcome:
    target = Path(ctx.target)
    out = Outcome()

    cli = tool_paths.find("utmt")
    if cli is None:
        raise EngineError(
            "缺少 UndertaleModTool CLI，无法提取 GameMaker 资源。\n"
            "      修复：python tools/setup_tools.py utmt")

    data_file = find_data_file(target)
    if data_file is None:
        raise EngineError("没有找到 data.win / game.droid 之类的 GameMaker 数据文件")

    scripts = _script_paths(cli)
    if not scripts:
        raise EngineError(
            f"没有找到导出脚本：{cli.parent / SCRIPT_DIR}\n"
            f"      这些脚本随本工具一起发布，请确认 tools/utmt 目录完整。")

    if not ctx.deep:
        out.notes.append("当前为「不解容器」模式，未调用 UTMT")
        out.fallback = True
        return out

    work = ctx.sub_work("gamemaker")
    env = {"ME_OUT": str(work)}
    # 整张纹理页体积大且和精灵重复，默认不要
    if os.environ.get("ME_TEXTURE_PAGES") == "1":
        env["ME_TEXTURE_PAGES"] = "1"

    argv = [str(cli), "load", str(data_file)]
    for s in scripts:
        argv += ["-s", str(s)]

    out.notes.append(f"数据文件: {data_file.name}")
    out.notes.append(f"调用: {cli.name} load ... ({len(scripts)} 个导出脚本)")
    if ctx.verbose:
        print("      UTMT 正在导出资源。图片多的时候会跑一会儿，"
              "它的输出会实时显示在下面：")

    # 注意：这里**不能**用 conhost --headless 包一层（proc.run 的 console=True）。
    # 实测那样包会让 UTMT 一个文件都不导出、退出码还是 0，非常隐蔽。
    res = stream.run_live(
        argv,
        timeout=_timeout(ctx),
        env_extra=env,
        echo=ctx.verbose,
        banner="UndertaleModTool 输出：",
    )
    if not res.ok:
        tail = res.tail(8)
        out.notes.append(f"UTMT 退出码 {res.code}")
        if tail:
            out.notes.append("UTMT 输出: " + tail.replace("\n", " | ")[:400])
    for line in res.lines:
        line = line.strip()
        if line and "导出完成" in line:
            out.notes.append(line)

    # ---- 收集 UTMT 写出来的文件 ----
    n = gather.gather_tree(work, ctx.writer, origin=f"utmt:{data_file.name}")
    out.written = n

    # ---- data 文件旁边散落的资源（有些游戏音频是外置的）----
    if target.is_dir():
        extra = gather.gather_tree(
            target, ctx.writer,
            skip_exts={".win", ".droid", ".ios", ".unx", ".exe", ".dll"},
            origin=str(target))
        if extra:
            out.written += extra
            out.notes.append(f"data 文件之外另收集 {extra} 个文件")

    out.detail = f"从 {data_file.name} 导出 {n} 个文件"
    if out.written == 0:
        out.fallback = True
    return out


def _timeout(ctx: Context) -> int:
    from me.config import TOOL_TIMEOUT
    return TOOL_TIMEOUT
