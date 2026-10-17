# -*- coding: utf-8 -*-
"""Enigma Virtual Box 脱壳。

Enigma VB 会把游戏运行时和资源打包进一个 exe（虚拟文件系统 + 还原原始 PE）。
脱壳用 evbunpack，脱完之后再重新识别内层引擎，交给对应的引擎模块继续提取。

实测样本：cage-of-abyss-full-game-v0.2.2.exe
  Enigma VB 壳 -> 内层 GameMaker -> data.win
"""

from pathlib import Path

from me.collect import gather
from me.engines.base import Context, EngineError, Outcome
from me.tools import paths as tool_paths
from me.tools import stream

KEY = "enigma"
NAME = "Enigma Virtual Box"

# 防御性上限：壳里套壳虽然少见，但不能无限递归
MAX_SHELL_DEPTH = 2


def extract(ctx: Context) -> Outcome:
    """两条通道（V0.17）：默认第三方 evbunpack，勾了自研才走 me/formats/evb.py。

    两条通道做的事一样：把虚拟文件系统铺到 work 目录，然后重新识别内层引擎
    递归提取。区别只在于「谁来铺」—— 外部的 evbunpack，还是自己写的 evb。
    """
    target = Path(ctx.target)
    out = Outcome()

    if not ctx.deep:
        out.notes.append("不解容器模式：未执行脱壳")
        out.fallback = True
        return out

    depth = int(getattr(ctx, "shell_depth", 0) or 0)
    if depth >= MAX_SHELL_DEPTH:
        out.notes.append(f"递归脱壳 {depth} 层，不再继续")
        out.fallback = True
        return out

    work = ctx.sub_work(f"enigma_lv{depth + 1}")
    out.notes.append(f"脱壳: {target.name} -> {work}")

    if ctx.use_self:
        out.notes.append("拆包: 自研（me/formats/evb.py）")
        if not _unpack_self(ctx, target, work, out):
            out.notes.append("自研通道没铺出文件，退回 evbunpack")
            _unpack_third(ctx, target, work)
    else:
        out.notes.append("拆包: 第三方 evbunpack")
        _unpack_third(ctx, target, work)

    # ---- 看看脱出来了什么 ----
    files = list(gather.list_files(work))
    if not files:
        out.notes.append("脱壳目录为空")
        out.fallback = True
        return out

    exes = [p for p in files if p.suffix.lower() == ".exe"]
    out.notes.append(f"脱出 {len(files)} 个文件，其中 exe {len(exes)} 个")

    # ---- 重新识别内层引擎并递归提取 ----
    inner_key, inner_name, inner_note = _pick_inner(ctx, work, exes)
    if inner_note:
        out.notes.append(inner_note)

    from me import channel as chan
    from me.engines import get as get_engine, fallback as get_fallback
    mod = get_engine(inner_key) or get_fallback()
    if inner_key == KEY:
        # 内层还是壳，避免死循环
        mod = get_fallback()

    # 内层按自己的引擎重新判一次通道：外层是壳（evbunpack），内层可能是
    # GameMaker（UTMT / 自研）。用户的开关要跟着传下去，不然「勾了自研」
    # 到了内层会悄悄变回第三方。
    dec = chan.decide(inner_key, experimental=ctx.experimental,
                      want_self=ctx.want_self)
    out.notes.append(f"内层通道: {dec.label}"
                     + (f"（{dec.note}）" if dec.note else ""))

    sub_ctx = Context(
        target=work,
        detection=_fake_detection(inner_key, inner_name),
        writer=ctx.writer,
        work_dir=Path(ctx.work_dir) / f"enigma_inner{depth + 1}",
        deep=ctx.deep,
        categories=ctx.categories,
        progress=ctx.progress,
        experimental=ctx.experimental,
        want_self=ctx.want_self,
        channel=dec.channel,
        shell_depth=depth + 1,
    )

    try:
        inner = mod.extract(sub_ctx)
    except Exception as exc:  # noqa: BLE001 - 内层失败也要给出结果
        out.notes.append(f"内层引擎（{inner_name}）提取出错: {exc}")
        inner = Outcome(fallback=True)

    out.written = inner.written
    out.notes += inner.notes
    out.detail = inner.detail or f"脱壳后由 {inner_name} 提取"
    if inner.written == 0:
        # 内层什么都没提取到，至少把脱出来的文件收一遍
        n = gather.gather_tree(work, ctx.writer, origin="enigma:unpacked",
                               progress=ctx.progress)
        out.written += n
        if n:
            out.notes.append(f"退回通用收集 {n} 个文件")
        else:
            out.fallback = True
    return out


def _unpack_self(ctx: Context, target: Path, work: Path, out: Outcome) -> bool:
    """自研通道：把 .enigma1 里的虚拟文件系统铺到 work。返回是否铺出文件。"""
    from me.formats import evb

    try:
        idx = evb.read_index(target)
    except evb.EvbError as exc:
        out.notes.append(f"自研读不出壳内文件表: {exc}")
        return False
    out.notes.append(evb.describe(idx))

    try:
        rep = evb.extract_to(target, work, progress=ctx.progress)
    except evb.EvbError as exc:
        out.notes.append(f"自研铺文件失败: {exc}")
        return False
    out.notes.append(f"自研铺出 {rep.written}/{rep.total} 个文件"
                     f"（{rep.bytes / 1024 / 1024:.1f} MB）")
    if rep.warnings:
        out.notes += rep.warnings[:5]
    return rep.written > 0


def _unpack_third(ctx: Context, target: Path, work: Path) -> None:
    """第三方通道：evbunpack。"""
    tool = tool_paths.find("evbunpack")
    if tool is None:
        raise EngineError(
            "缺少 evbunpack，无法脱 Enigma Virtual Box 壳。\n"
            "      修复：python tools/setup_tools.py evbunpack")

    if ctx.verbose:
        # 这一步实测要 30 秒上下，期间不给点动静用户会以为卡死然后按 Ctrl+C
        print("      脱壳中")

    # evbunpack <file> <output>
    res = stream.run_live(
        [str(tool), str(target), str(work)],
        timeout=_timeout(),
        echo=ctx.verbose,
        banner="evbunpack 输出：",
    )
    if not res.ok:
        tail = res.tail(10)
        raise EngineError(
            f"evbunpack 失败（退出码 {res.code}）\n"
            + ("      " + tail.replace("\n", "\n      ") if tail else ""))


def _pick_inner(ctx: Context, work: Path, exes) -> tuple:
    """判断壳里是什么引擎。"""
    from me.scanner.detect import scan_dir

    det = scan_dir(work)
    if det.is_known and det.engine != KEY:
        return det.engine, det.name, f"内层识别为 {det.name}"

    # 目录判不出来就挨个看 exe
    for exe in exes[:5]:
        from me.scanner.detect import scan_file
        sub = scan_file(exe)
        if sub.is_known and sub.engine != KEY:
            return sub.engine, sub.name, f"内层 exe（{exe.name}）识别为 {sub.name}"

    hinted = getattr(ctx.detection, "inner", "") or ""
    if hinted and hinted != KEY:
        return hinted, getattr(ctx.detection, "inner_name", hinted), \
            f"内层引擎: {hinted}"
    return "generic", "通用收集", "内层引擎未能识别，改用通用收集"


def _fake_detection(key: str, name: str):
    from me.scanner.detect import Detection
    det = Detection()
    det.engine = key
    det.name = name
    det.evidence = ["由 Enigma 脱壳后重新识别"]
    return det


def _timeout() -> int:
    from me.config import TOOL_TIMEOUT
    return TOOL_TIMEOUT
