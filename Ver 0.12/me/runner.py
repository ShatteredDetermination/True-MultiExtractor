# -*- coding: utf-8 -*-
"""提取流程编排。

main.py 只负责收集用户意图，真正的「识别 -> 选引擎 -> 提取 -> 出报表」
在这里。这样命令行和交互菜单可以共用同一套逻辑。
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from me import setup_ini, ui
from me.collect import manifest
from me.collect.writer import OutputWriter
from me.config import WORK_DIR, ensure_dirs
from me.engines import base, fallback as _fallback, get as get_engine
from me.scanner.detect import detect as detect_engine


@dataclass
class Result:
    """一次提取的最终结果。"""
    detection: object = None
    engine_key: str = ""
    engine_name: str = ""
    written: int = 0
    detail: str = ""
    notes: List[str] = field(default_factory=list)
    out_dir: Optional[Path] = None
    elapsed: float = 0.0
    error: str = ""


def run(target, out_dir=None, categories=None, deep: bool = True,
        engine_key: Optional[str] = None, quiet: bool = False,
        progress: Optional[Callable] = None, keep_work: bool = False) -> Result:
    """对 target 跑一遍完整流程。"""
    ensure_dirs()
    t0 = time.time()
    target = Path(target).expanduser()
    res = Result()

    # ---- 1. 识别 ----
    if not quiet:
        ui.step(f"识别中: {target}")
    det = detect_engine(target)
    res.detection = det
    if not quiet:
        ui.ok(f"引擎: {det.describe()}")
        for ev in det.evidence[:5]:
            ui.detail(ev)
        if det.truncated:
            ui.warn("扫描条目超上限，已截断；特征可能漏检")

    # ---- 2. 选引擎 ----
    key = engine_key or det.engine
    mod = get_engine(key)
    if mod is None:
        if key not in ("unknown", ""):
            if not quiet:
                ui.warn(f"引擎 [{key}] 模块未加载，改用通用收集")
        mod = _fallback()
        key = "generic"
    res.engine_key = key
    res.engine_name = getattr(mod, "NAME", key)

    # ---- 3. 写出器 ----
    # 没指定 -o 就用 setup.ini 里的保存路径，下面再挂一层「游戏名」，
    # 这样多次提取不会互相覆盖。
    out_root = (Path(out_dir) if out_dir
                else setup_ini.output_root() / _safe_name(target))
    writer = OutputWriter(out_root, categories=categories,
                          engine=res.engine_name, source=str(target))

    ctx = base.Context(
        target=target,
        detection=det,
        writer=writer,
        work_dir=WORK_DIR / _safe_name(target),
        deep=deep,
        categories=set(categories) if categories else None,
        progress=progress,
        # 静默模式（自测/脚本）下不要把外部工具的输出刷到屏幕上
        verbose=not quiet,
    )

    # ---- 4. 提取 ----
    if not quiet:
        ui.step(f"使用引擎模块: {res.engine_name}")
    try:
        outcome = mod.extract(ctx)
        if not isinstance(outcome, base.Outcome):
            outcome = base.Outcome(written=writer.total())
    except base.EngineError as exc:
        if not quiet:
            ui.error(str(exc))
        res.error = str(exc)
        outcome = base.Outcome(fallback=True)
    except Exception as exc:  # noqa: BLE001 - 任何异常都要给用户看得懂的结果
        if not quiet:
            ui.error(f"提取过程异常: {type(exc).__name__}: {exc}")
        res.error = f"{type(exc).__name__}: {exc}"
        outcome = base.Outcome(fallback=True)

    # ---- 5. 兜底 ----
    if outcome.fallback and key != "generic":
        if not quiet:
            ui.warn("该引擎未提取到内容，改用通用收集")
        ctx.progress = progress
        try:
            fb = _fallback().extract(ctx)
            outcome.written += fb.written
            outcome.detail = (outcome.detail + " / " + fb.detail).strip(" /")
            outcome.notes += fb.notes
        except Exception as exc:  # noqa: BLE001
            res.error = res.error or str(exc)

    # ---- 6. 报表 ----
    res.written = writer.total()
    res.detail = outcome.detail
    res.notes = outcome.notes
    res.out_dir = out_root
    res.elapsed = time.time() - t0

    # ---- 7. 清理中间产物 ----
    # 成功提取时把 work 目录删掉（脱壳/解包出来的东西动辄几个 GB）。
    # 一个文件都没提取到的话保留，用户可以直接进去看看到底解出了什么。
    if not keep_work:
        if res.written > 0:
            _cleanup_work(ctx.work_dir)
        elif Path(ctx.work_dir).exists():
            res.notes.append(f"中间产物: {ctx.work_dir}")

    if res.written:
        manifest.write_manifest(out_root, writer,
                                extra={"detection": _det_dict(det)})
        manifest.write_report(out_root, writer)
    if not quiet:
        ui.clear_progress()   # 进度条可能没跑到 100%，先擦掉再打报表
        _report(res, writer)
    return res


def _cleanup_work(work_dir) -> None:
    """删掉本次运行的中间产物目录。"""
    import shutil
    work_dir = Path(work_dir)
    if not work_dir.exists():
        return
    try:
        shutil.rmtree(work_dir)
    except OSError:
        # 删不掉不影响结果（比如文件被占用），静默忽略
        pass


def _report(res: Result, writer) -> None:
    from me.config import CATEGORY_LABEL
    ui.sep()
    if res.written:
        ui.ok(f"导出 {res.written} 个文件  ->  {res.out_dir}")
        for cat, n in sorted(writer.counts.items(), key=lambda kv: -kv[1]):
            ui.detail(f"{CATEGORY_LABEL.get(cat, cat)}: {n}")
    else:
        ui.warn("未提取到任何文件")
    for note in res.notes:
        ui.detail(note)
    ui.detail(f"耗时 {res.elapsed:.1f}s")


# 这些后缀说明用户传的是单个数据文件，而不是一个游戏目录
_FILE_SUFFIXES = {".exe", ".dat", ".ccn", ".cct", ".win", ".droid", ".ios",
                  ".unx", ".pck", ".pak", ".rgssad", ".rgss2a", ".rgss3a",
                  ".asar", ".apk"}


def _safe_name(target: Path) -> str:
    """由目标路径推一个能当目录名的名字。"""
    # 传单个 exe 时用文件名主干，免得出现 output/xxx.exe/ 这种怪目录。
    # 目录名里本身可能带点（"Game v1.2"），所以不能只看有没有后缀。
    if target.is_file() or target.suffix.lower() in _FILE_SUFFIXES:
        name = target.stem
    else:
        name = target.name
    name = name or "output"
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name.strip() or "output"


def _det_dict(det) -> dict:
    return {
        "engine": det.engine,
        "name": det.name,
        "shell": det.shell,
        "inner": det.inner,
        "entry": det.entry,
        "evidence": det.evidence,
    }
