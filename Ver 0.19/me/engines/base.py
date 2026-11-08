# -*- coding: utf-8 -*-
"""引擎提取流程的公共接口。

每个引擎模块（me/engines/xxx.py）都要提供：

    KEY   = "xxx"                # 引擎键，和 scanner 里的一致
    NAME  = "显示名"
    def extract(ctx) -> str      # 干活，返回一句结果描述

ctx 是下面的 Context。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from me import channel
from me.collect.writer import OutputWriter
from me.scanner.detect import Detection


class EngineError(Exception):
    """提取过程中的可预期错误（缺工具、格式不符等）。"""


@dataclass
class Context:
    """一次提取任务的上下文。"""
    target: Path                                  # 用户给的文件或目录
    detection: Detection                          # 识别结果
    writer: OutputWriter                          # 结果写出器
    work_dir: Path                                # 中间产物目录
    deep: bool = True                             # 是否解开容器
    categories: Optional[set] = None              # 只要这些分类
    progress: Optional[Callable] = None           # 进度回调
    verbose: bool = True                          # 是否把外部工具的输出实时打给用户
    fix: bool = False                             # 先重建容器结构再提取（V0.15）
    # ---- V0.17：两条拆包通道（判定见 me/channel.py）
    experimental: bool = False                    # 实验性功能总开关
    want_self: bool = False                       # 使用自研拆包核心
    channel: str = ""                             # 判定结果：self / third
    shell_depth: int = 0                          # 套壳递归层数（Enigma）

    @property
    def use_self(self) -> bool:
        """本次是否走自研通道。

        引擎模块只读这个属性，不自己判断开关 —— 判定逻辑只有 channel.decide 一处。
        channel 为空（直接构造 Context 的老代码 / 自测）时按第三方处理。
        """
        return self.channel == channel.SELF

    def sub_work(self, name: str) -> Path:
        """在 work 目录下开一个子目录。"""
        p = Path(self.work_dir) / name
        p.mkdir(parents=True, exist_ok=True)
        return p


@dataclass
class Outcome:
    """引擎返回的结果。"""
    written: int = 0
    detail: str = ""
    notes: list = field(default_factory=list)
    fallback: bool = False       # 是否需要退回到通用收集
