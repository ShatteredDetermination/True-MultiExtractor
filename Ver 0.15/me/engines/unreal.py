# -*- coding: utf-8 -*-
"""Unreal Engine 提取（借 repak 解包 .pak）。

实测得到的 repak 真实用法（tools/repak 里那份，clap 风格 CLI）：

    repak.exe [OPTIONS] <COMMAND>

    Commands: info / list / hash-list / unpack / pack / get / help

    repak.exe list   [OPTIONS] <INPUT>              # 一行一个条目路径
    repak.exe info   <INPUT>                        # 版本/挂载点/加密/条目数
    repak.exe unpack [OPTIONS] [INPUT]...           # -o/--output 指定输出目录

外部 CLI 真实行为（都是本机实测出来的）：

  * `--aes-key` / `-a` 是**顶层选项，必须写在子命令前面**：
        repak.exe --aes-key <KEY> list x.pak     ✅
        repak.exe list --aes-key <KEY> x.pak     ❌ error: unexpected argument
    值可以是 base64 或 hex（help 原文：「256 bit AES encryption key as
    base64 or hex string if the pak is encrypted」）。
  * `unpack` 默认 `--strip-prefix ../../../`，正好把 UE 里那种
    `../../../Game/Content/...` 的条目路径剥成干净相对路径；
    所以这里不传该参数，用默认值即可。
  * `unpack` 遇到已存在的目录会报错退出，需要 `-f/--force`。
  * `list` 实测输出就是裸的条目路径，一行一条，没有表头，可以直接数行数。
  * repak 只认 .pak。实测把 .utoc 喂给 `info` 会一路
    「trying version V11 failed ... version unsupported or is encrypted」，
    最后退 1，且顶层 help 里完全没有 utoc / iostore 字样 —— 即不支持 IoStore。

重要：.pak 里装的是 .uasset / .uexp / .ubulk 这类**引擎专有格式**，
不是能直接看的图片和音频。repak 只负责拆容器，转换得另找工具。
"""

import os
from pathlib import Path
from typing import List, Optional, Tuple

from me.collect import gather
from me.engines.base import Context, EngineError, Outcome
from me.tools import paths as tool_paths
from me.tools import stream

KEY = "unreal"
NAME = "Unreal Engine"

# Unreal 的容器扩展名
PAK_EXT = ".pak"
# Unreal 5 的 IoStore 格式：.utoc 是表，.ucas 是数据
IOSTORE_EXTS = (".utoc", ".ucas")

# 环境变量名。没有就不给 repak 传 --aes-key（未加密的 pak 传了也无害，
# 但明文 pak 本来就不需要，少一个出错点）
AES_ENV = "ME_AES_KEY"


def find_paks(target) -> List[Path]:
    """找出目录里所有 .pak。"""
    target = Path(target)
    if target.is_file():
        return [target] if target.suffix.lower() == PAK_EXT else []
    if not target.is_dir():
        return []
    return sorted(p for p in target.rglob("*")
                  if p.is_file() and p.suffix.lower() == PAK_EXT)


def find_iostore(target) -> List[Path]:
    """找出 .utoc / .ucas（Unreal 5 IoStore，repak 不支持）。"""
    target = Path(target)
    if target.is_file():
        return [target] if target.suffix.lower() in IOSTORE_EXTS else []
    if not target.is_dir():
        return []
    return sorted(p for p in target.rglob("*")
                  if p.is_file() and p.suffix.lower() in IOSTORE_EXTS)


def _aes_args(key: Optional[str]) -> List[str]:
    """AES key 拼到**子命令之前**（实测放后面 clap 直接报错）。"""
    return ["--aes-key", key] if key else []


def build_argv(cli: Path, sub: str, pak: Path, out_dir: Optional[Path] = None,
               aes_key: Optional[str] = None,
               force: bool = False) -> List[str]:
    """拼一次 repak 调用的命令行。

    单独抽出来是为了能自测：不真跑 repak 也能断言参数顺序对不对。
    顺序必须是 [cli] [--aes-key K] <sub> <pak> [sub 的选项]。
    """
    argv = [str(cli)] + _aes_args(aes_key) + [sub, str(pak)]
    if sub == "unpack":
        if out_dir is not None:
            argv += ["--output", str(out_dir)]
        if force:
            argv.append("--force")
    return argv


def count_entries(cli: Path, pak: Path, aes_key: Optional[str],
                  verbose: bool = False) -> Tuple[int, str]:
    """用 repak list 数条目。返回 (条目数, 说明文字)。

    数不出来时返回 -1，让调用方照常解包（条目数只是给用户看的参考）。
    """
    argv = build_argv(cli, "list", pak, aes_key=aes_key)
    res = stream.run_live(argv, timeout=_timeout(), echo=verbose,
                          banner=f"repak list {pak.name}：")
    if not res.ok:
        tail = res.lines
        hint = tail[-1].strip()[:120] if tail else "无输出"
        return -1, f"list 失败（退出码 {res.code}）: {hint}"
    # list 输出就是裸路径，一行一条；空行不算
    lines = [ln for ln in res.lines if ln.strip()]
    return len(lines), ""


def extract(ctx: Context) -> Outcome:
    target = Path(ctx.target)
    out = Outcome()

    cli = tool_paths.find("repak")
    if cli is None:
        raise EngineError(
            "缺少 repak，无法解包 Unreal .pak。\n"
            "      修复：python tools/setup_tools.py repak")

    paks = find_paks(target)
    iostore = find_iostore(target)

    # IoStore 先说清楚，因为 UE5 游戏经常只有 .utoc/.ucas 没有 .pak
    if iostore:
        names = ", ".join(p.name for p in iostore[:4])
        more = f" 等 {len(iostore)} 个" if len(iostore) > 4 else ""
        out.notes.append(
            f"Unreal 5 IoStore 文件：{names}{more}\n"
            "        repak 只认 .pak：.utoc 得到 version unsupported or is encrypted，"
            "退出码 1\n"
            "        IoStore 转换: FModel 或 UE Viewer（umodel）\n"
            "        同时存在 .pak 时照常解 .pak")

    if not paks:
        if iostore:
            # 有 utoc 没 pak 是 UE5 的常态，不算错误，别报成「没找到 pak」
            out.notes.append("目录内无 .pak，仅有 IoStore 文件，本次无法解包")
            out.detail = "只发现 IoStore（.utoc/.ucas），repak 不支持"
        else:
            raise EngineError(
                "未找到 Unreal 的 .pak 文件（<游戏>\\Content\\Paks\\）")
        out.fallback = True
        return out

    if not ctx.deep:
        out.notes.append(f"发现 {len(paks)} 个 .pak；不解容器模式，未解包")
        out.fallback = True
        return out

    aes_key = os.environ.get(AES_ENV, "").strip() or None
    if aes_key:
        out.notes.append(f"使用 {AES_ENV} 里的 AES key（长度 {len(aes_key)}）")
    else:
        out.notes.append(f"未设置 {AES_ENV}，按未加密处理")

    work = ctx.sub_work("unreal")
    out.notes.append(f"发现 {len(paks)} 个 .pak")

    total = 0
    for i, pak in enumerate(paks):
        # 每个 pak 解到自己的子目录，避免多个 pak 里同名条目互相覆盖
        dest = work / f"{i:02d}_{pak.stem}"

        n_entries, err = count_entries(cli, pak, aes_key, verbose=ctx.verbose)
        if n_entries >= 0:
            out.notes.append(f"{pak.name}: {n_entries} 个条目")
        else:
            out.notes.append(f"{pak.name}: {err}")

        argv = build_argv(cli, "unpack", pak, out_dir=dest,
                          aes_key=aes_key, force=True)
        # 大 pak 解包可能跑很久，输出实时透传，用户才知道没卡死
        res = stream.run_live(argv, timeout=_timeout(), echo=ctx.verbose,
                              banner=f"repak unpack {pak.name}：")
        got = gather.gather_tree(dest, ctx.writer,
                                 rel_prefix=pak.stem, origin=str(pak))
        total += got
        out.notes.append(f"{pak.name}: 解出 {got} 个文件（退出码 {res.code}）")

        if got == 0:
            tail = res.lines
            hint = tail[-1].strip()[:160] if tail else "无输出"
            out.notes.append(f"{pak.name} 未解出内容: {hint}")

    out.written = total
    out.detail = f"从 {len(paks)} 个 .pak 解出 {total} 个文件"

    # 无论解出多少，这条提示都必须给：解出来的是引擎专有格式，不能直接用
    out.notes.append(
        ".pak 内是 .uasset / .uexp / .ubulk，归入「其他」分类\n"
        "        转换: FModel 或 UE Viewer（umodel）")

    if total == 0:
        out.fallback = True
    return out


def _timeout() -> int:
    from me.config import TOOL_TIMEOUT
    return TOOL_TIMEOUT
