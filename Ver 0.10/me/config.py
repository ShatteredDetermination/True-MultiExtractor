# -*- coding: utf-8 -*-
"""全局常量与路径。

需要改配置（输出目录、分类名称等）时只动这一个文件。
"""

from pathlib import Path

# ---------------------------------------------------------------- 程序信息
APP_NAME = "True MultiExtractTool"
VERSION = "0.10 Beta"
AUTHOR = "Resolved-Patient"

# ---------------------------------------------------------------- 目录布局
# config.py 在 MultiExtractor/me/ 下，所以上一级就是项目根目录
ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"        # 随包发布的外部 CLI
WORK_DIR = ROOT / "work"          # 中间产物（脱壳、临时解包）
OUTPUT_DIR = ROOT / "output"      # 最终提取结果

# ---------------------------------------------------------------- 分类
# 内部键名 -> 中文显示名。顺序即菜单顺序。
CATEGORY_LABEL = {
    "image": "图片",
    "audio": "音频",
    "text": "文本",
    "font": "字体",
    "video": "视频",
    "other": "其他",
}
CATEGORIES = tuple(CATEGORY_LABEL.keys())

# 子进程超时（秒）。大游戏解包可能很久，所以给得比较宽松。
TOOL_TIMEOUT = 3600

# 单个文件读取上限，用于二进制特征扫描（默认 64MB，避免把大 exe 全读进内存）
SCAN_LIMIT = 64 * 1024 * 1024


def ensure_dirs() -> None:
    """确保工作目录和输出目录存在。"""
    for d in (TOOLS_DIR, WORK_DIR, OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)
