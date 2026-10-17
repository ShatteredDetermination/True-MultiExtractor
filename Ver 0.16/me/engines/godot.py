# -*- coding: utf-8 -*-
"""Godot 提取。

两种发布形态：
  * 工程形式：目录里有 project.godot，资源本来就是散文件，直接收集即可
  * 发布形式：资源在 .pck 里（可能藏在子目录），交给 me/formats/godot_pck 解开

主包之外散落的资源（外置音频、说明书之类）会再收集一遍；可执行文件和运行时
库不是资源，跳过，免得输出目录里全是垃圾。
"""

from pathlib import Path
from typing import List, Optional

from me.collect import gather
from me.engines.base import Context, EngineError, Outcome
from me.formats import godot_pck

KEY = "godot"
NAME = "Godot"

PCK_EXT = ".pck"
PROJECT_MARK = "project.godot"

# Godot 运行时 / 打包产物，不是游戏资源
SKIP_EXT = {
    ".pck", ".exe", ".dll", ".so", ".dylib", ".lib", ".exp", ".pdb",
    ".o", ".obj", ".debug", ".apk", ".aab", ".zip",
}


def find_project(target: Path) -> Optional[Path]:
    """找 project.godot（工程形式的标志）。"""
    target = Path(target)
    if not target.is_dir():
        return None
    direct = target / PROJECT_MARK
    if direct.exists():
        return direct
    for p in target.rglob(PROJECT_MARK):     # 有的工程套了一层目录
        return p
    return None


def find_pcks(target: Path) -> List[Path]:
    """找出所有 .pck，大的排前面（主包通常最大，先有大报告）。"""
    target = Path(target)
    if target.is_file():
        return [target] if _is_pck(target) else []
    found = [p for p in target.rglob("*") if p.is_file() and _is_pck(p)]
    found.sort(key=lambda p: -_size(p))
    return found


def extract(ctx: Context) -> Outcome:
    target = Path(ctx.target)
    out = Outcome()

    if target.is_file() and not _is_pck(target):
        raise EngineError(f"{target.name} 不是 .pck 文件")

    project = find_project(target)

    # 不解容器时不碰 .pck；工程形式本来就是散文件，交给通用收集即可
    if not ctx.deep:
        if project is not None:
            out.notes.append(f"工程形式（{project.name}）：资源已是散文件")
        out.notes.append("不解容器模式：未解包 .pck")
        out.fallback = True
        return out

    if project is not None:
        root = project.parent
        n = gather.gather_tree(root, ctx.writer, origin=str(root),
                               skip_exts=SKIP_EXT, progress=ctx.progress)
        out.written = n
        out.detail = f"工程形式（{project.name}），收集 {n} 个文件"
        out.notes.append(f"工程目录: {root}")
        if n == 0:
            out.notes.append("工程目录内无可收集的资源")
            out.fallback = True
        return out

    pcks = find_pcks(target)
    if not pcks:
        out.detail = "未找到 .pck，也无 project.godot"
        out.notes.append("Godot 单文件游戏的输入: .pck 或含 .pck 的目录")
        out.fallback = True
        return out

    from_pck = 0
    for pck in pcks:
        try:
            idx = godot_pck.read_index(pck)
        except (godot_pck.PckError, OSError) as exc:
            out.notes.append(_with_name(pck, exc))
            continue

        out.notes.append(f"{pck.name}: PCK v{idx.version}, "
                         f"{len(idx.entries)} 个条目 / {_mb(idx.total_bytes)}")
        try:
            rep = godot_pck.extract(pck, ctx.writer, origin=str(pck),
                                    progress=ctx.progress)
        except (godot_pck.PckError, OSError) as exc:
            out.notes.append(_with_name(pck, f"解包失败: {exc}"))
            continue

        from_pck += rep.written
        out.notes.append(f"  写出 {rep.written}/{rep.total} 个文件，"
                         f"offset 基准: {idx.base_kind}")
        if idx.version >= 2:
            out.notes.append(f"  目录起点 0x{idx.dir_pos:X}，"
                             f"file_base 0x{idx.file_base:X}，"
                             f"目录哈希 {idx.hash_len} 字节")
        if rep.skipped_encrypted:
            out.notes.append(f"  {rep.skipped_encrypted} 个文件被加密，跳过")
        if rep.skipped_removed:
            out.notes.append(f"  {rep.skipped_removed} 个文件被标记删除，跳过")
        if rep.skipped_broken:
            out.notes.append(f"  {rep.skipped_broken} 个条目越界或读不全，跳过")
        if rep.escaped:
            out.notes.append(f"  {len(rep.escaped)} 个路径试图跳出输出目录，已清洗: "
                             + ", ".join(rep.escaped[:3]))
        out.notes += rep.warnings

    # pck 之外散落的资源（外置音频、说明文件等）
    extra = 0
    if target.is_dir():
        extra = gather.gather_tree(target, ctx.writer, skip_exts=SKIP_EXT,
                                   origin=str(target), progress=ctx.progress)
        if extra:
            out.notes.append(f"pck 之外另收集 {extra} 个文件")

    out.written = from_pck + extra
    out.detail = (f"从 {len(pcks)} 个 .pck 解出 {from_pck} 个文件"
                  + (f"，另收集散落文件 {extra} 个" if extra else ""))
    if out.written == 0:
        out.fallback = True
    return out


def _is_pck(path: Path) -> bool:
    return path.name.lower().endswith(PCK_EXT)


def _with_name(pck: Path, text) -> str:
    """解析器的报错里通常已经带了文件名，别写成「a.pck: a.pck ...」。"""
    text = str(text)
    return text if pck.name in text else f"{pck.name}: {text}"


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"
