# -*- coding: utf-8 -*-
"""引擎识别模块。

注意：这里把 detect() 函数导出成 detect_engine，
避免把子模块 me.scanner.detect 给遮蔽掉
（Python 里包属性会和同名子模块打架）。
"""

from me.scanner.detect import Detection, detect as detect_engine  # noqa: F401

__all__ = ["Detection", "detect_engine"]
