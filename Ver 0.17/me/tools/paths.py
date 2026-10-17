# -*- coding: utf-8 -*-
"""在 tools/ 下定位外部 CLI。

所有外部工具都放在 tools/ 里随包发布，用户端不需要再下载。
这里只管「找得到 / 找不到」，找不到时给出清晰的提示。
"""

import os
from pathlib import Path
from typing import List, Optional

from me.config import IS_FROZEN, TOOLS_DIR

# 工具键 -> (相对 tools/ 的候选路径列表, 说明)
REGISTRY = {
    # V0.16 起第三方通道要用它（自研通道见 me/formats/gamemaker_*.py）
    "utmt": (
        ["utmt/UndertaleModCli.exe", "utmt/UndertaleModTool.exe",
         "utmt/UndertaleModCli"],
        "UndertaleModTool CLI: GameMaker (data.win)",
    ),
    "assetstudio": (
        ["assetstudio/AssetStudio.CLI.exe", "assetstudio/AssetStudio.CLI"],
        "AssetStudio CLI: Unity",
    ),
    "evbunpack": (
        ["evbunpack/evbunpack.exe", "evbunpack/evbunpack"],
        "evbunpack: Enigma Virtual Box",
    ),
    "repak": (
        ["repak/repak.exe", "repak/repak"],
        "repak: Unreal Engine .pak",
    ),
}


def find(key: str) -> Optional[Path]:
    """按注册表找一个工具，找到返回路径。"""
    if key not in REGISTRY:
        return None
    candidates, _ = REGISTRY[key]
    for rel in candidates:
        p = TOOLS_DIR / rel
        if p.exists():
            return p
    return None


def find_in(directory, names: List[str]) -> Optional[Path]:
    """在指定目录（含子目录）里找任一文件名。"""
    directory = Path(directory)
    if not directory.exists():
        return None
    lowered = {n.lower() for n in names}
    for root, _dirs, files in os.walk(directory):
        for fn in files:
            if fn.lower() in lowered:
                return Path(root) / fn
    return None


def repair_hint(key: str = "") -> str:
    """缺工具时给用户的修复办法。

    打包成 exe 之后用户机器上没有 Python，让他去跑 setup_tools.py
    是条死路，所以换成「重新解压发布包」。
    """
    if IS_FROZEN:
        return "修复：tools/ 需与 MultiExtractorCLI.exe 同层。"
    cmd = f"python tools/setup_tools.py {key}".strip()
    return f"修复：{cmd}"


def require(key: str) -> Path:
    """找一个工具，找不到就抛异常并给出修复提示。"""
    p = find(key)
    if p is None:
        _, desc = REGISTRY.get(key, ("", key))
        raise ToolMissing(
            f"缺少外部工具 [{key}]: {desc}\n"
            f"  期望位置: {TOOLS_DIR / REGISTRY[key][0][0]}\n"
            f"  {repair_hint(key)}"
        )
    return p


def status() -> List[tuple]:
    """列出所有工具的状态，用于 `--tools` 自检。"""
    out = []
    for key, (candidates, desc) in REGISTRY.items():
        p = find(key)
        out.append((key, p, desc, candidates[0]))
    return out


class ToolMissing(Exception):
    """外部工具缺失。"""
