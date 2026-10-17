# -*- coding: utf-8 -*-
"""自研 / 第三方两条拆包通道的选择（V0.17）。

规则来自用户指示，逐条对应：

  1. 每种引擎都提供**自研工具**（自研 = ``me/formats/`` 下自己写的解析器）。
  2. **默认走第三方工具**（``tools/`` 下随包发布的外部 CLI）。
  3. GUI 上「启用实验性功能」与「使用自研拆包核心」**同时**勾选才换自研；
     CLI 侧是 ``--experimental`` + ``--self``，语义完全一致。
  4. **CTF（Clickteam Fusion）除外**：只有自研，不接第三方
     （CTFAK 是 AGPL-3.0，不引入）。
  5. 自研还没覆盖的引擎不假装有：明确退回第三方，并留一条能看懂的 note。

这个模块只做判定，不碰文件、不 import 引擎模块 —— 这样自测可以纯逻辑地
把整张矩阵跑一遍。
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

SELF = "self"
THIRD = "third"

LABEL = {SELF: "自研", THIRD: "第三方"}


@dataclass(frozen=True)
class Capability:
    """一个引擎的两条通道各是什么状态。"""

    key: str
    third: Optional[str]      # tools/ 注册表键；None = 没有第三方通道
    third_name: str           # 第三方工具显示名
    self_ready: bool          # 自研通道是否已经实现
    self_name: str            # 自研实现落在哪


# 顺序与 me/engines/__init__.py 的 MODULE_ORDER 一致，方便对照阅读。
CAPS: Dict[str, Capability] = {
    "electron": Capability(
        "electron", None, "",
        True, "me/formats/asar.py"),
    "gamemaker": Capability(
        "gamemaker", "utmt", "UndertaleModTool CLI",
        True, "me/formats/gamemaker_win.py"),
    "clickteam": Capability(
        "clickteam", None, "",
        True, "me/formats/ctf25/"),
    "enigma": Capability(
        "enigma", "evbunpack", "evbunpack",
        True, "me/formats/evb.py（只读文件系统，不做 PE 还原）"),
    "godot": Capability(
        "godot", None, "",
        True, "me/formats/godot_pck.py"),
    "rpgmaker": Capability(
        "rpgmaker", None, "",
        True, "me/formats/rgssad.py"),
    "unity": Capability(
        "unity", "assetstudio", "AssetStudio CLI",
        True, "me/formats/unityfs.py（只覆盖未压缩包）"),
    "unreal": Capability(
        "unreal", "repak", "repak",
        True, "me/formats/ue_pak.py（pak v0–v9）"),
    "generic": Capability(
        "generic", None, "",
        True, "me/collect/gather.py"),
}

# 只有自研、没有第三方的引擎。CTF 在这里是「除外」规则的主体。
SELF_ONLY = tuple(k for k, c in CAPS.items() if c.third is None)


def capability(engine: str) -> Capability:
    """取能力表；未知引擎按「只有自研」处理（未知引擎最后会落到通用收集）。"""
    cap = CAPS.get(engine)
    if cap is not None:
        return cap
    return Capability(engine, None, "", True, "通用收集")


@dataclass
class Decision:
    """一次判定的结果。"""

    channel: str
    note: str = ""

    @property
    def label(self) -> str:
        return LABEL.get(self.channel, self.channel)

    @property
    def is_self(self) -> bool:
        return self.channel == SELF


def decide(engine: str, experimental: bool = False,
           want_self: bool = False,
           third_available: Optional[bool] = None) -> Decision:
    """判定这次该走哪条通道。

    ``experimental``  —— 实验性功能总开关（GUI 的 CheckBox1 / --experimental）
    ``want_self``     —— 使用自研拆包核心（GUI 的 CheckBox7 / --self）
    ``third_available`` —— 第三方工具在不在；None 表示不检查（自测用）

    两者必须**同时**成立才走自研 —— 只勾自研不勾实验性，按第三方。
    """
    cap = capability(engine)

    # ---- 没有第三方通道：只能自研（CTF 属于这一类，不受开关影响）
    if cap.third is None:
        if engine == "clickteam":
            return Decision(SELF, "CTF 只有自研通道，不接第三方，不受实验性开关影响")
        return Decision(SELF, f"{engine} 没有第三方工具，只能走自研")

    # ---- 有第三方：默认就是它
    if third_available is False:
        if cap.self_ready:
            return Decision(SELF, "第三方工具缺失，改用自研")
        return Decision(THIRD, "第三方工具缺失，自研通道也没覆盖，本引擎无法解包")

    if not experimental:
        if want_self:
            return Decision(
                THIRD, "已要求自研但未启用实验性功能，按默认走第三方")
        return Decision(THIRD, "")

    if not want_self:
        return Decision(THIRD, "实验性功能已启用但未勾选自研，走第三方")

    if cap.self_ready:
        return Decision(SELF, "实验性功能 + 自研开关同时启用")

    return Decision(
        THIRD, f"自研通道还没覆盖 {engine}（V0.17 待补），退回第三方")


def matrix() -> List[Capability]:
    """按注册顺序返回能力表，给 --channels 和文档用。"""
    return [CAPS[k] for k in CAPS]


def status_line(cap: Capability) -> str:
    """一行人类可读的状态描述。"""
    third = cap.third_name if cap.third else "无（不接第三方）"
    self_ = cap.self_name if cap.self_ready else "待补（V0.17）"
    return f"{cap.key:<10} 第三方 {third} / 自研 {self_}"
