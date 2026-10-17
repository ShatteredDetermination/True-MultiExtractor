# -*- coding: utf-8 -*-
"""True MultiExtractTool —— 多引擎游戏资源提取工具。

包结构（每个文件只负责一件事，方便单独维护）：

    me/config.py            全局常量、路径
    me/ui.py                控制台输出（横幅/进度/日志）
    me/scanner/             引擎识别
    me/collect/             资源收集与分类、写出、清单
    me/formats/             各容器格式的纯 Python 解析器
    me/engines/             各引擎的提取流程（可能调用 tools/ 下的外部 CLI）
    me/tools/               外部 CLI 的定位与调用封装
"""

from me.config import APP_NAME, VERSION  # noqa: F401

__all__ = ["APP_NAME", "VERSION"]
