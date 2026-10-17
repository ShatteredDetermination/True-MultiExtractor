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
    target = Path(ctx.target)
    out = Outcome()

    tool = tool_paths.find("evbunpack")
    if tool is None:
        raise EngineError(
            "缺少 evbunpack，无法脱 Enigma Virtual Box 壳。\n"
            "      修复：python tools/setup_tools.py evbunpack")

    if not ctx.deep:
        out.notes.append("当前为「不解容器」模式，未执行脱壳")
        out.fallback = True
        return out

    depth = int(getattr(ctx, "shell_depth", 0) or 0)
    if depth >= MAX_SHELL_DEPTH:
        out.notes.append(f"已递归脱壳 {depth} 层，不再继续")
        out.fallback = True
        return out

    work = ctx.sub_work(f"enigma_lv{depth + 1}")
    out.notes.append(f"脱壳: {target.name} -> {work}")

    if ctx.verbose:
        # 这一步实测要 30 秒上下，期间不给点动静用户会以为卡死然后按 Ctrl+C
        print("      正在脱壳（解开虚拟文件系统 + 还原原始 PE）。")
        print("      文件多时可能要几十秒，期间没有输出是正常的，请不要中断。")

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

    # ---- 看看脱出来了什么 ----
    files = list(gather.list_files(work))
    if not files:
        out.notes.append("脱壳后目录是空的，可能不是 Enigma VB 打包的文件")
        out.fallback = True
        return out

    exes = [p for p in files if p.suffix.lower() == ".exe"]
    out.notes.append(f"脱出 {len(files)} 个文件，其中 exe {len(exes)} 个")

    # ---- 重新识别内层引擎并递归提取 ----
    inner_key, inner_name, inner_note = _pick_inner(ctx, work, exes)
    if inner_note:
        out.notes.append(inner_note)

    from me.engines import get as get_engine, fallback as get_fallback
    mod = get_engine(inner_key) or get_fallback()
    if inner_key == KEY:
        # 内层还是壳，避免死循环
        mod = get_fallback()

    sub_ctx = Context(
        target=work,
        detection=_fake_detection(inner_key, inner_name),
        writer=ctx.writer,
        work_dir=Path(ctx.work_dir) / f"enigma_inner{depth + 1}",
        deep=ctx.deep,
        categories=ctx.categories,
        progress=ctx.progress,
    )
    setattr(sub_ctx, "shell_depth", depth + 1)

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
            out.notes.append(f"退回通用收集，收了 {n} 个文件")
        else:
            out.fallback = True
    return out


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
            f"按外层识别结果，内层应为 {hinted}"
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
