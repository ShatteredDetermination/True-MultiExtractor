# -*- coding: utf-8 -*-
"""在 tools/ 下定位外部 CLI。

所有外部工具都放在 tools/ 里随包发布，用户端不需要再下载。
这里只管「找得到 / 找不到」，找不到时给出清晰的提示。
"""

import os
from pathlib import Path
from typing import List, Optional

from me.config import TOOLS_DIR

# 工具键 -> (相对 tools/ 的候选路径列表, 说明)
REGISTRY = {
    "utmt": (
        ["utmt/UndertaleModCli.exe", "utmt/UndertaleModTool.exe",
         "utmt/UndertaleModCli"],
        "UndertaleModTool CLI —— 提取 GameMaker (data.win) 资源",
    ),
    "assetstudio": (
        ["assetstudio/AssetStudio.CLI.exe", "assetstudio/AssetStudio.CLI"],
        "AssetStudio CLI —— 提取 Unity 资源",
    ),
    "evbunpack": (
        ["evbunpack/evbunpack.exe", "evbunpack/evbunpack"],
        "evbunpack —— Enigma Virtual Box 脱壳",
    ),
    "repak": (
        ["repak/repak.exe", "repak/repak"],
        "repak —— 解包 Unreal Engine .pak",
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


def require(key: str) -> Path:
    """找一个工具，找不到就抛异常并给出修复提示。"""
    p = find(key)
    if p is None:
        _, desc = REGISTRY.get(key, ("", key))
        raise ToolMissing(
            f"缺少外部工具 [{key}]：{desc}\n"
            f"  期望位置：{TOOLS_DIR / REGISTRY[key][0][0]}\n"
            f"  修复：python tools/setup_tools.py {key}"
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
