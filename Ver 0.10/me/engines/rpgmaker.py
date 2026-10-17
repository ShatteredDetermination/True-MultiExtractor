# -*- coding: utf-8 -*-
"""RPG Maker（XP / VX / VX Ace）提取。

两种形态都要照顾：
  * 加密归档：Game.rgssad（XP）/ Game.rgss2a（VX）/ Game.rgss3a（VX Ace），
    交给 me/formats/rgssad.py 解密；
  * 明文工程：开了「不加密」发布的游戏，目录里直接躺着 Graphics/、Audio/，走通用收集。

另外会在 text/ 下写一份归档清单（``相对路径<TAB>大小``），
用户核对「归档里原本有哪些文件」时不用再开一次解包器。
"""

import os
from pathlib import Path
from typing import List

from me.collect import gather
from me.engines.base import Context, EngineError, Outcome
from me.formats import rgssad

KEY = "rpgmaker"
NAME = "RPG Maker"

# 归档后缀（scanner 的 DIR_EXT_MARKERS 里也是这三个）
ARCHIVE_EXTS = (".rgssad", ".rgss2a", ".rgss3a")
# 这些是 RGSS 运行库/游戏本体，不是资源
SKIP_EXT = {".dll", ".exe", ".so", ".dylib", ".ini", ".rxdata", ".rvdata",
            ".rvdata2", ".rxproj", ".rvproj", ".rvproj2"}
# 目录遍历时不要往里钻（RTP 缓存之类）
SKIP_DIRS = {".git", "__macosx", "save", "saves"}


def find_archives(target: Path) -> List[Path]:
    """找出目标里的所有 RGSS 归档。

    优先按扩展名找（快）；找不到再按魔数扫一遍，兜住「被改名的归档」。
    """
    target = Path(target)
    if target.is_file():
        return [target] if rgssad.guess_archive_type(target) else []

    found: List[Path] = []
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
        for fn in files:
            if fn.lower().endswith(ARCHIVE_EXTS):
                found.append(Path(root) / fn)
    if found:
        return sorted(found)

    # 没有任何归档后缀：扫一遍小文件，看有没有魔数对得上的
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
        for fn in files:
            p = Path(root) / fn
            try:
                if p.stat().st_size < 8:
                    continue
                if rgssad.guess_archive_type(p):
                    found.append(p)
            except OSError:
                continue
    return sorted(found)


def extract(ctx: Context) -> Outcome:
    target = Path(ctx.target)
    out = Outcome()
    archives = find_archives(target)
    manifest: List[str] = []          # 所有归档的清单行，最后合成一个文件
    archive_written = 0

    for path in archives:
        kind = rgssad.guess_archive_type(path)
        if not ctx.deep:
            out.notes.append(
                f"{path.name}: {rgssad.format_label(kind)}，"
                f"当前为「不解容器」模式，未解密")
            continue
        try:
            entries = rgssad.read_entries(path)
            n = rgssad.extract(path, ctx.writer,
                               origin=str(path), progress=ctx.progress)
        except (rgssad.RgssadError, OSError) as exc:
            out.notes.append(f"{path.name}: 解析失败（{exc}）")
            continue

        total_bytes = sum(e.size for e in entries)
        archive_written += n
        for e in entries:
            manifest.append(f"{e.name}\t{e.size}")
        out.notes.append(
            f"{path.name}: {rgssad.format_label(kind)}，"
            f"{len(entries)} 个条目 / {_mb(total_bytes)}，导出 {n} 个")
        if not entries:
            out.notes.append(f"{path.name}: 目录表是空的，可能不是标准归档")

    # 散落资源：明文发布的游戏目录，或者归档之外的素材
    scattered = 0
    if target.is_dir():
        scattered = gather.gather_tree(
            target, ctx.writer,
            skip_exts=SKIP_EXT | set(ARCHIVE_EXTS),
            origin=str(target),
            progress=ctx.progress,
        )
        if scattered:
            out.notes.append(f"归档之外另收集 {scattered} 个散落文件")

    # 清单放在 text/ 下
    if manifest and ctx.writer.wants("text"):
        rel = "rgssad_manifest.txt"
        body = _MANIFEST_HEAD + "\n".join(manifest) + "\n"
        if ctx.writer.write_bytes("text", rel, body.encode("utf-8"),
                                  origin="rpgmaker:manifest"):
            out.notes.append(f"归档清单已写入 text/{rel}（{len(manifest)} 行）")

    out.written = archive_written + scattered
    out.detail = _describe(archives, archive_written, scattered)

    if not archives:
        if not ctx.deep:
            out.notes.append("没有找到 .rgssad/.rgss2a/.rgss3a 归档")
        if scattered == 0:
            raise EngineError(
                "没有找到 RPG Maker 加密归档（.rgssad / .rgss2a / .rgss3a），"
                "目录里也没有可收集的资源。\n"
                "      如果确认是 RPG Maker 游戏，可能是 MV/MZ（www/ 目录，"
                "资源在 data/ 与 img/ 里）。")
    elif out.written == 0:
        # 归档在但一个文件都没解出来：让 runner 走通用兜底再试一次
        out.fallback = True
    return out


def _describe(archives: List[Path], from_archives: int, scattered: int) -> str:
    """一句话总结。"""
    if not archives:
        return f"明文目录，收集了 {scattered} 个文件"
    parts = []
    for path in archives:
        kind = rgssad.guess_archive_type(path)
        parts.append(f"{path.name}（{rgssad.format_label(kind)}）")
    text = "、".join(parts)
    if scattered:
        return f"从 {text} 解出 {from_archives} 个文件，另有 {scattered} 个散落文件"
    return f"从 {text} 解出 {from_archives} 个文件"


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


_MANIFEST_HEAD = (
    "# RPG Maker 归档清单：相对路径<TAB>大小(字节)\n"
    "# 由 MultiExtractor 的 rpgmaker 引擎生成，仅记录归档内的条目\n"
)
