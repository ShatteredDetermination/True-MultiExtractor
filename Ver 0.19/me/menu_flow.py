# -*- coding: utf-8 -*-
"""交互菜单的状态机与参数拼装（纯逻辑，不做 I/O）。

为什么单独一层
--------------
菜单如果自己拼参数、自己调函数，就会出现「按了 2 却执行了 1」这类错乱。
这里的做法是：**菜单只产出命令行参数**，执行时再用同一套 argparse 解析一遍。
这样菜单和命令行永远走同一条路，也能被自测直接断言。

流程（渐进式，不一次糊满屏）：

    输入目标
      └─ [1] 分析目标（--detect）   ← 没分析之前不显示提取/重建
           └─ 分析完成
                ├─ [1] 提取资源（--extract）
                ├─ [2] 修复/重建 data.win（--rebuild / --repair / --fix）  ← 仅 GameMaker
                ├─ [3] 切换拆包通道（--experimental / --self）             ← 仅双通道引擎
                ├─ [4] 识别详情
                ├─ [5] 更换路径
                ├─ [6] 更多选项（不解容器 / 保留中间产物 / 自检 / 配置 / 版本）
                └─ [7] 退出
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from me import channel

# ---- 动作常量（菜单只产出这些，不直接调函数）
ANALYZE = "analyze"
EXTRACT = "extract"
REPAIR_MENU = "repair_menu"
CHANNEL_MENU = "channel_menu"
DETAIL = "detail"
PATH = "path"
MORE = "more"
EXIT = "exit"

# 修复模式
PATCH = "patch"          # --repair   原位补丁，长度不变
REBUILD = "rebuild"      # --rebuild  全量重建，长度会变
FIX = "fix"              # --fix      先重建再提取

# 提取分类（菜单顺序）
EXTRACT_ITEMS: Tuple[Tuple[str, Optional[set]], ...] = (
    ("提取图片", {"image"}),
    ("提取音频", {"audio"}),
    ("提取文本", {"text"}),
    ("提取字体", {"font"}),
    ("提取全部", None),
)

REPAIR_ENGINES = ("gamemaker",)


@dataclass
class Session:
    """一次交互会话的状态。"""

    target: str
    forced: str = ""                 # -e 指定的引擎键
    engine_key: str = ""             # 分析得到的引擎键
    engine_name: str = ""
    analyzed: bool = False
    experimental: bool = False
    want_self: bool = False
    no_deep: bool = False
    keep_work: bool = False
    out: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def effective_engine(self) -> str:
        return self.forced or self.engine_key


# ---------------------------------------------------------------- 能力判断
def repair_supported(engine_key: str) -> bool:
    """修复/重建只对 GameMaker 有意义。"""
    return (engine_key or "") in REPAIR_ENGINES


def channel_switchable(engine_key: str) -> bool:
    """有第三方且有自研，才谈得上「切换」。CTF / Godot 这类只有一个通道的不显示。"""
    cap = channel.capability(engine_key or "")
    return cap.third is not None and cap.self_ready


def engine_label(session: Session) -> str:
    """当前是自动识别还是被 -e 固定了。"""
    return session.forced or "自动"


def channel_label(session: Session) -> str:
    """当前通道的一句话描述（引擎相关）。"""
    key = session.effective_engine
    cap = channel.capability(key or "")
    if cap.third is None:
        return "自研（该引擎无第三方）"
    dec = channel.decide(key, experimental=session.experimental,
                         want_self=session.want_self)
    return dec.label


def channel_detail(session: Session) -> str:
    """通道那一行括号里的补充说明。"""
    key = session.effective_engine
    cap = channel.capability(key or "")
    if cap.third is None:
        return "不接第三方"
    if cap.self_ready:
        if session.experimental and session.want_self:
            return f"自研核心（{cap.self_name}）"
        name = cap.third_name or cap.third
        return f"第三方工具（依赖 {name}）"
    return "自研通道待补，只能走第三方"


def result_banner(session: Session, det=None) -> str:
    """分析完成后那一行「[检测结果]」。"""
    if det is None:
        name = session.engine_name or session.engine_key or "未识别"
    else:
        name = getattr(det, "name", "") or session.engine_name or "未识别"
        inner = getattr(det, "inner_name", "")
        shell = getattr(det, "shell", "")
        if shell and inner:
            name = f"{shell} 壳（内层: {inner}）"
    entry = getattr(det, "entry", "") if det is not None else ""
    return f"[检测结果]: {name}" + (f"（主数据 {entry}）" if entry else "")


# ---------------------------------------------------------------- 菜单
def pre_analyze_options() -> List[Tuple[str, str]]:
    return [
        ("分析目标（--detect）", ANALYZE),
        ("更换路径", PATH),
        ("退出", EXIT),
    ]


def main_options(session: Session) -> List[Tuple[str, str]]:
    """分析完成后的主菜单：按引擎能力动态生成。"""
    items: List[Tuple[str, str]] = [("提取资源（--extract）", EXTRACT)]
    key = session.effective_engine
    if repair_supported(key):
        items.append(("修复/重建 data.win（--rebuild / --repair）", REPAIR_MENU))
    if channel_switchable(key):
        items.append((f"切换拆包通道（当前: {channel_label(session)}）", CHANNEL_MENU))
    items += [
        ("识别详情", DETAIL),
        ("更换路径", PATH),
        ("更多选项（不解容器 / 中间产物 / 自检 / 配置 / 版本）", MORE),
        ("退出", EXIT),
    ]
    return items


def extract_options(session: Session) -> List[Tuple[str, str]]:
    """提取子菜单：分类 + 返回。"""
    items = [(label, f"cat:{'all' if cats is None else ','.join(sorted(cats))}")
             for label, cats in EXTRACT_ITEMS]
    items.append(("返回", "back"))
    return items


def repair_options() -> List[Tuple[str, str]]:
    return [
        ("全量重建（推荐：重建 AST 结构，彻底洗白混淆，文件长度会变）", REBUILD),
        ("原位补丁（保守：仅修补越界指针，文件长度不变）", PATCH),
        ("先重建再提取（--fix：重建后直接跑一遍提取）", FIX),
        ("取消返回", "back"),
    ]


REPAIR_WARNING = (
    "修复/重建功能仅适用于**损坏的 GameMaker data.win** 文件。\n"
    "普通游戏不需要它；重建会改变文件长度，请先用备份试。"
)


def channel_options(session: Session) -> List[Tuple[str, str]]:
    key = session.effective_engine
    cap = channel.capability(key or "")
    now = channel_label(session)
    third = f"第三方工具（{cap.third_name or cap.third}）"
    self_ = f"自研核心（{cap.self_name}）"
    to_self = "switch_self"
    to_third = "switch_third"
    items: List[Tuple[str, str]] = []
    if now != "自研":
        items.append((f"切换到 {self_} —— 不依赖外部 CLI", to_self))
    if now != "第三方":
        items.append((f"切换到 {third}", to_third))
    items.append(("返回", "back"))
    return items


def more_options(session: Session) -> List[Tuple[str, str]]:
    return [
        (f"不解容器 [{'开' if session.no_deep else '关'}]", "toggle_deep"),
        (f"保留中间产物 [{'开' if session.keep_work else '关'}]", "toggle_work"),
        (f"指定引擎（-e，当前: {engine_label(session)}）", "engine"),
        ("工具自检（--tools）", "tools"),
        ("引擎列表（--engines）", "engines"),
        ("配置（--setup）", "setup"),
        ("版本（--version）", "version"),
        ("返回", "back"),
    ]


# ---------------------------------------------------------------- 参数拼装
def channel_flags(session: Session) -> List[str]:
    """只有实验性功能开着才带通道参数；自研再叠 --self。"""
    if not session.experimental:
        return []
    return ["--experimental", "--self"] if session.want_self else ["--experimental"]


def build_extract_argv(session: Session, categories: Optional[set] = None) -> List[str]:
    argv = [session.target]
    if categories:
        argv += ["--only", ",".join(sorted(categories))]
    if session.forced:
        argv += ["-e", session.forced]
    if session.out:
        argv += ["-o", session.out]
    if session.no_deep:
        argv.append("--no-deep")
    if session.keep_work:
        argv.append("--keep-work")
    argv += channel_flags(session)
    return argv


def build_repair_argv(session: Session, mode: str) -> List[str]:
    """mode 决定发哪个参数：rebuild→--rebuild，patch→--repair，fix→--fix。"""
    flag = {REBUILD: "--rebuild", PATCH: "--repair", FIX: "--fix"}.get(mode)
    if flag is None:
        raise ValueError(f"未知修复模式: {mode}")
    argv = [session.target, flag]
    if session.out:
        argv += ["-o", session.out]
    argv += channel_flags(session)
    return argv


def build_detect_argv(session: Session) -> List[str]:
    argv = [session.target, "--detect"]
    if session.forced:
        argv += ["-e", session.forced]
    return argv
