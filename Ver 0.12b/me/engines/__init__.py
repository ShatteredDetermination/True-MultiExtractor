# -*- coding: utf-8 -*-
"""引擎注册表。

用 importlib 挨个加载，某个引擎模块出问题不会影响其他引擎
（方便增量开发，也方便用户自己删掉不用的模块）。
"""

import importlib
from typing import Dict, Optional

# 注册顺序 = 菜单里的显示顺序
MODULE_ORDER = (
    "electron",
    "gamemaker",
    "clickteam",
    "enigma",
    "godot",
    "rpgmaker",
    "unity",
    "unreal",
    "generic",
)

ENGINES: Dict[str, object] = {}
LOAD_ERRORS: Dict[str, str] = {}

for _name in MODULE_ORDER:
    try:
        _mod = importlib.import_module(f"me.engines.{_name}")
        ENGINES[getattr(_mod, "KEY", _name)] = _mod
    except Exception as _exc:  # noqa: BLE001 - 故意吞掉，只记录
        LOAD_ERRORS[_name] = f"{type(_exc).__name__}: {_exc}"


def get(key: str) -> Optional[object]:
    """按键取引擎模块。"""
    return ENGINES.get(key)


def names() -> list:
    """返回 [(key, 显示名), ...]。"""
    out = []
    for key, mod in ENGINES.items():
        out.append((key, getattr(mod, "NAME", key)))
    return out


def fallback() -> object:
    """通用引擎（一定存在）。"""
    mod = ENGINES.get("generic")
    if mod is None:
        raise RuntimeError("通用引擎模块加载失败: "
                           + LOAD_ERRORS.get("generic", "未知原因"))
    return mod
