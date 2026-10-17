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
