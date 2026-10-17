# -*- coding: utf-8 -*-
"""GameMaker 提取（借 UndertaleModTool 的 CLI）。

UTMT 自带的导出脚本是交互式的（弹目录选择框），无人值守跑不了。
所以本工具在 tools/utmt/Scripts/MultiExtractor/ 下放了自己的脚本，
输出目录通过环境变量 ME_OUT 传递。

UTMT 读不了的时候怎么办
-----------------------
UTMT 只认它支持的那些 GameMaker 版本，碰到不认识的 chunk 结构就直接抛
异常、一个字节都不给你。所以这里分三层往下退：

    1. 直接跑 UTMT（绝大多数游戏走这条）
    2. UTMT 失败 -> 试着把出问题的 chunk 原长占位清空，再跑一次
       （见 me/formats/gamemaker_repair.py，长度绝对不能变）
    3. 还是不行 -> 放弃解析容器，直接按结构抓内嵌资源
       （见 me/formats/gamemaker_carve.py）

第 3 层抓出来的是**整张纹理页**而不是切好的单个精灵，因为精灵坐标在
TPAG chunk 里 —— 而这正是我们解析不了容器才走到这一步的。这一点会
明确写进输出说明，不让用户以为拿到的就是最终素材。

实测：
  * Undertale True Genocide/data.win（7.8MB）-> 552 图 / 98 音频 / 3595 字符串
  * Downloads/data.win（61MB）-> UTMT 死在 FONT chunk（20 个字体里有 6 个
    的指针指向字符串表区间，超出 chunk 边界），修补也救不回来，
    最后走第 3 层抓出 32 张纹理页 + 221 个 WAV。
"""

import mmap
import os
from pathlib import Path
from typing import List, Optional, Tuple

from me.collect import gather
from me.engines.base import Context, EngineError, Outcome
from me.formats import gamemaker_carve as carve
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
# （原始 bytes + bytearray），太大吃不消。抓取那层用 mmap，不受此限。
REPAIR_SIZE_LIMIT = 200 * 1024 * 1024

# 实验性「指针重建」的开关。默认**关闭** —— 那一层要反复调用 UTMT 做预言机，
# 又慢又不保证能修好，不该在用户不知情的时候跑。
EXPERIMENTAL_ENV = "ME_EXPERIMENTAL_REBUILD"

# 重建比修补更吃内存（要留着原文件 + 每次改动的副本），门槛压得更低
REBUILD_SIZE_LIMIT = 128 * 1024 * 1024


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
        print("      UTMT 正在导出资源。图片多的时候会跑一会儿，"
              "它的输出会实时显示在下面：")
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
        return 0, [f"读不到文件信息，跳过修补：{exc}"]

    if size > REPAIR_SIZE_LIMIT:
        notes.append(f"文件 {size / 1024 / 1024:.0f}MB 超过修补上限"
                     f"（{REPAIR_SIZE_LIMIT // 1024 // 1024}MB），跳过修补")
        return 0, notes

    try:
        data = data_file.read_bytes()
    except OSError as exc:
        return 0, [f"读不出文件内容，跳过修补：{exc}"]

    ok, why = repair.verify(data)
    if not ok:
        # 连 chunk 链都不完整，说明不是「某个 chunk 读不懂」，
        # 而是文件本身残缺，清 chunk 只会越弄越糟
        notes.append(f"chunk 链不完整（{why}），修补没有意义，跳过")
        return 0, notes
    notes.append(f"检查发现 chunk 链是完整的（{why}）——问题出在某个 chunk 内部")

    text = first_text
    for rnd in range(1, MAX_REPAIR_ROUNDS + 1):
        names = repair.failing_chunks(text)
        picked = repair.pick_target(data, names)
        if not picked:
            notes.append(f"UTMT 报的是 {names or '未知 chunk'}，"
                         f"这个没法安全清空，放弃修补")
            break
        try:
            data, done = repair.neutralize(data, {picked})
        except (AssertionError, repair.NotGamemaker) as exc:
            notes.append(f"修补失败：{exc}")
            break
        notes.append(f"第 {rnd} 次修补：把 {', '.join(done)} 清成空列表"
                     f"（长度不变，绝对偏移不受影响）")

        rep_file = work / f"repaired_{rnd}.win"
        repair.write_repaired(data, rep_file)
        pass_dir = work / f"pass{rnd}"
        # 清理上一轮可能留下的东西，免得旧文件被当成新结果
        res, text = _run_utmt(cli, scripts, rep_file, pass_dir, env, ctx)
        n = _collect(pass_dir, ctx, origin=f"utmt(修补):{rep_file.name}")
        if n:
            notes.append(f"修补之后 UTMT 成功导出 {n} 个文件")
            return n, notes

    notes.append("修补后 UTMT 仍然读不了这个文件")
    return 0, notes


# ------------------------------------------------------------------ 实验性重建
def _try_rebuild(ctx: Context, cli: Path, scripts: List[Path],
                 data_file: Path, work: Path,
                 env: dict) -> Tuple[int, List[str]]:
    """【实验性】拿 UTMT 当预言机，反推越界的列表指针。

    见 me/formats/gamemaker_rebuild.py 开头 —— 那一层**不保证能修好**，
    修不完就老实返回 0，让流程继续退到抓取兜底。
    """
    from me.formats import gamemaker_rebuild as rb

    notes = [f"【实验性】尝试指针重建（{EXPERIMENTAL_ENV}=1 时才会跑）"]
    try:
        size = data_file.stat().st_size
    except OSError as exc:
        return 0, notes + [f"读不到文件信息：{exc}"]
    if size > REBUILD_SIZE_LIMIT:
        return 0, notes + [
            f"文件 {size / 1024 / 1024:.0f}MB 超过重建上限"
            f"（{REBUILD_SIZE_LIMIT // 1024 // 1024}MB），跳过"]

    try:
        data = data_file.read_bytes()
    except OSError as exc:
        return 0, notes + [f"读不出内容：{exc}"]

    if ctx.verbose:
        print("      【实验性】正在用 UTMT 反推越界指针，每轮都要跑一次 UTMT，"
              "可能要一会儿 ...")

    oracle = rb.utmt_oracle(cli, work / "oracle")
    res = rb.rebuild(data, oracle)
    notes += res.notes
    if not res.ok:
        return 0, notes + ["重建没有产出可用的文件"]

    rebuilt = work / "rebuilt.win"
    rb_data = res.data
    rebuilt.write_bytes(rb_data)
    notes.append(f"重建后写出 {rebuilt.name}（{len(rb_data):,} 字节，"
                 f"与原文件等长）")

    pass_dir = work / "pass_rebuilt"
    _res2, _text2 = _run_utmt(cli, scripts, rebuilt, pass_dir, env, ctx)
    n = _collect(pass_dir, ctx, origin=f"utmt(重建):{data_file.name}")
    if n:
        notes.append(f"重建之后 UTMT 导出成功，拿到 {n} 个文件")
    else:
        notes.append("重建之后 UTMT 仍然读不通（详见重建模块的说明）")
    return n, notes


# ------------------------------------------------------------------ 抓取
def _carve_fallback(ctx: Context, data_file: Path) -> Tuple[int, List[str]]:
    """第 3 层：不解析容器，直接按结构抓内嵌资源。"""
    notes: List[str] = []
    path = Path(data_file)
    try:
        size = path.stat().st_size
    except OSError as exc:
        return 0, [f"读不到文件：{exc}"]
    if size == 0:
        return 0, ["文件是空的"]

    if ctx.verbose:
        print(f"      解析不了这个文件，改为直接扫描内嵌资源"
              f"（{size / 1024 / 1024:.0f}MB，可能要一会儿）...")

    written = 0
    with open(path, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            found = carve.carve_bytes(mm, progress=ctx.progress)
            if not found:
                return 0, ["扫描后没有发现任何结构完整的 PNG / WAV / OGG"]
            unique = carve.dedupe(mm, found)
            # 按格式、再按位置排序，编号才稳定
            order = {"png": 0, "wav": 1, "ogg": 2}
            unique.sort(key=lambda it: (order.get(it.kind, 9), it.start))

            dup = len(found) - len(unique)
            if dup:
                notes.append(f"抓到 {len(found)} 个，其中 {dup} 个内容重复已去掉")
            source = f"carve:{path.name}"

            for idx, item in enumerate(unique):
                rel = _carve_name(item, idx)
                blob = mm[item.start:item.end]
                if ctx.writer.write_auto(rel, blob, origin=source) is not None:
                    written += 1

    notes.append("这个文件的容器结构 UTMT 解析不了，"
                 "资源是从字节流里按结构校验抓出来的")
    notes.append("抓到的是**整张纹理页**（图片是多张图拼在一起的图集），"
                 "不是切好的单个精灵 —— 精灵坐标在 TPAG 里，"
                 "而正是解析不了容器才走到这一步")
    return written, notes


def _carve_name(item, idx: int) -> str:
    """给抓到的资源起个带信息的名字。"""
    if item.kind == "png" and isinstance(item.meta, tuple):
        w, h, _n = item.meta
        return f"{idx:05d}_{w}x{h}.png"
    if item.kind == "wav" and isinstance(item.meta, tuple):
        fmt, ln = item.meta
        tag, ch, rate = fmt
        return f"{idx:05d}_{rate}hz_{ch}ch.wav"
    if item.kind == "ogg" and isinstance(item.meta, int):
        return f"{idx:05d}_{item.meta}pages.ogg"
    return f"{idx:05d}.{item.kind}"


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

    # ---- 第 2.5 层：实验性指针重建（默认关闭）----
    if n == 0 and os.environ.get(EXPERIMENTAL_ENV) == "1":
        n2b, notes = _try_rebuild(ctx, cli, scripts, data_file, work, env)
        out.notes += notes
        n = n2b

    # ---- 第 3 层：抓取兜底 ----
    if n == 0:
        n3, notes = _carve_fallback(ctx, data_file)
        out.notes += notes
        n = n3

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
