# -*- coding: utf-8 -*-
"""交互式菜单里可选的引擎列表。

把「强制指定引擎」做成菜单项，是因为自动识别不可能覆盖所有情况：
比如某个游戏被二次打包过、或者特征被作者删掉了，用户往往自己知道是什么引擎。
"""

from me.engines import names as engine_names


def build_engine_menu():
    """返回 [(显示文字, 引擎键), ...]，第一项是「自动」。"""
    items = [("自动识别", None)]
    for key, name in engine_names():
        if key == "generic":
            continue  # 通用收集在最后单独给
        items.append((f"{name}  [{key}]", key))
    items.append(("通用收集（只按扩展名扫）", "generic"))
    return items


def describe_engines() -> str:
    """一行描述所有可用引擎，给帮助信息用。"""
    return ", ".join(key for key, _name in engine_names())
