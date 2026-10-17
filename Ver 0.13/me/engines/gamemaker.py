# -*- coding: utf-8 -*-
"""GameMaker 提取（借 UndertaleModTool 的 CLI）。

UTMT 自带的导出脚本是交互式的（弹目录选择框），无人值守跑不了。
所以本工具在 tools/utmt/Scripts/MultiExtractor/ 下放了自己的脚本，
输出目录通过环境变量 ME_OUT 传递。

UTMT 读不了的时候怎么办
-----------------------
UTMT 只认它支持的那些 GameMaker 版本，碰到不认识的 chunk 结构就直接抛
异常、一个字节都不给你。所以这里分两层往下退：

    1. 直接跑 UTMT（绝大多数游戏走这条）
    2. UTMT 失败 -> 把出问题的 chunk 原长占位清空，再跑一次
       （见 me/formats/gamemaker_repair.py，长度绝对不能变）

**不做「绕开 UTMT 自己按字节切资源」那套。** 曾经有过一层抓取兜底，但它只能
按结构把内嵌的 PNG / WAV 整块捞出来 —— 捞到的是**整张纹理页（atlas）**，
不是切好的单个精灵（精灵坐标在 TPAG chunk 里）。那种产物实用性差、又容易被
误当成最终素材，所以删掉了。资源一律经 UTMT 解析，解析不了就如实报失败。

实测：
  * Undertale True Genocide/data.win（7.8MB）-> 552 图 / 98 音频 / 3595 字符串
"""

import os
from pathlib import Path
from typing import List, Optional, Tuple

from me.collect import gather
from me.engines.base import Context, EngineError, Outcome
from me.formats import gamemaker_repair as repair
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

# 修补重试最多几轮（每轮要跑一次 UTMT，61MB 的文件一次半分钟起）
MAX_REPAIR_ROUNDS = 3

# 超过这个大小就不做修补：neutralize() 要在内存里重建整份文件
# （原始 bytes + bytearray），太大吃不消。
REPAIR_SIZE_LIMIT = 200 * 1024 * 1024


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


# ------------------------------------------------------------------ UTMT
def _run_utmt(cli: Path, scripts: List[Path], data_file: Path,
              out_dir: Path, env: dict, ctx: Context) -> Tuple[object, str]:
    """跑一趟 UTMT，返回 (LiveResult, 它的输出文本)。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    argv = [str(cli), "load", str(data_file)]
    for s in scripts:
        argv += ["-s", str(s)]
    run_env = dict(env)
    run_env["ME_OUT"] = str(out_dir)

    if ctx.verbose:
        print("      UTMT 导出中")
    res = stream.run_live(
        argv,
        timeout=_timeout(ctx),
        env_extra=run_env,
        echo=ctx.verbose,
        banner="UndertaleModTool 输出：",
    )
    text = "\n".join(res.lines)
    return res, text


def _collect(out_dir: Path, ctx: Context, origin: str) -> int:
    """把 UTMT 写出来的文件收进结果。"""
    if not Path(out_dir).exists():
        return 0
    return gather.gather_tree(out_dir, ctx.writer, origin=origin)


def _try_repair(ctx: Context, cli: Path, scripts: List[Path],
                data_file: Path, work: Path, env: dict,
                first_text: str) -> Tuple[int, List[str]]:
    """第 2 层：原长占位清空 UTMT 消化不了的 chunk，再试。

    返回 (导出文件数, 要写到结果里的说明)。
    """
    notes: List[str] = []
    try:
        size = data_file.stat().st_size
    except OSError as exc:
        return 0, [f"无法获取文件信息，跳过修补：{exc}"]

    if size > REPAIR_SIZE_LIMIT:
        notes.append(f"文件 {size / 1024 / 1024:.0f}MB 超过修补上限"
                     f"（{REPAIR_SIZE_LIMIT // 1024 // 1024}MB），跳过修补")
        return 0, notes

    try:
        data = data_file.read_bytes()
    except OSError as exc:
        return 0, [f"无法读出文件内容，跳过修补：{exc}"]

    ok, why = repair.verify(data)
    if not ok:
        # 连 chunk 链都不完整，说明不是「某个 chunk 读不懂」，
        # 而是文件本身残缺，清 chunk 只会越弄越糟
        notes.append(f"chunk 链: {why}，跳过修补")
        return 0, notes
    notes.append(f"chunk 链: {why}")

    text = first_text
    for rnd in range(1, MAX_REPAIR_ROUNDS + 1):
        names = repair.failing_chunks(text)
        picked = repair.pick_target(data, names)
        if not picked:
            notes.append(f"UTMT 报的 {names or '未知 chunk'} 无法安全清空，"
                         f"放弃修补")
            break
        try:
            data, done = repair.neutralize(data, {picked})
        except (AssertionError, repair.NotGamemaker) as exc:
            notes.append(f"修补失败：{exc}")
            break
        notes.append(f"第 {rnd} 轮修补: 清空 {', '.join(done)}")

        rep_file = work / f"repaired_{rnd}.win"
        repair.write_repaired(data, rep_file)
        pass_dir = work / f"pass{rnd}"
        # 清理上一轮可能留下的东西，免得旧文件被当成新结果
        res, text = _run_utmt(cli, scripts, rep_file, pass_dir, env, ctx)
        n = _collect(pass_dir, ctx, origin=f"utmt(修补):{rep_file.name}")
        if n:
            notes.append(f"修补后 UTMT 成功导出 {n} 个文件")
            return n, notes

    return 0, notes


# ------------------------------------------------------------------ 主流程
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
        raise EngineError("未找到 data.win / game.droid 之类的 GameMaker 数据文件")

    scripts = _script_paths(cli)
    if not scripts:
        raise EngineError(
            f"未找到导出脚本：{cli.parent / SCRIPT_DIR}")

    if not ctx.deep:
        out.notes.append("不解容器模式：未调用 UTMT")
        out.fallback = True
        return out

    work = ctx.sub_work("gamemaker")
    env = {"ME_OUT": str(work)}
    # 整张纹理页体积大且和精灵重复，默认不要
    if os.environ.get("ME_TEXTURE_PAGES") == "1":
        env["ME_TEXTURE_PAGES"] = "1"

    out.notes.append(f"数据文件: {data_file.name}")
    out.notes.append(f"调用: {cli.name} load ... ({len(scripts)} 个导出脚本)")

    # ---- 第 1 层：直接跑 UTMT ----
    pass0 = work / "pass0"
    res, text = _run_utmt(cli, scripts, data_file, pass0, env, ctx)
    n = _collect(pass0, ctx, origin=f"utmt:{data_file.name}")
    if n == 0:
        if not res.ok:
            out.notes.append(f"UTMT 退出码 {res.code}")
        tail = res.tail(6)
        if tail:
            out.notes.append("UTMT 报错: " + tail.replace("\n", " | ")[:400])

    # ---- 第 2 层：修补后重试 ----
    if n == 0:
        n2, notes = _try_repair(ctx, cli, scripts, data_file, work, env, text)
        out.notes += notes
        n = n2

    if n == 0:
        out.notes.append("UTMT 无法读取该文件；修补未成功；未生成输出文件")

    # ---- data 文件旁边散落的资源（有些游戏音频是外置的）----
    if target.is_dir():
        extra = gather.gather_tree(
            target, ctx.writer,
            skip_exts={".win", ".droid", ".ios", ".unx", ".exe", ".dll"},
            origin=str(target))
        if extra:
            n += extra
            out.notes.append(f"data 文件之外另收集 {extra} 个文件")

    out.written = n
    out.detail = f"从 {data_file.name} 导出 {n} 个文件"
    if out.written == 0:
        out.fallback = True
    return out


def _timeout(ctx: Context) -> int:
    from me.config import TOOL_TIMEOUT
    return TOOL_TIMEOUT
