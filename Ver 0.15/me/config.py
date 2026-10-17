# -*- coding: utf-8 -*-
"""全局常量与路径。

要改「提取结果存到哪」，改 setup.ini（见 me/setup_ini.py）；
要改分类名称、超时之类的，才动这个文件。
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------- 程序信息
APP_NAME = "True MultiExtractTool"
# 约定：Beta 后面那五位是**累加编号** —— 每对话一轮就加一，单调递增，
# 开新版本线（比如 0.14 -> 0.15）也不归零。动代码要加，不动代码对话一轮也加。
# （跑 python bump.py 加一）
# 这里是唯一来源 —— CLI 横幅、--version、事件流的 version 字段，
# 以及写给 GUI 的 %LOCALAPPDATA%\MultiExtractor\CLIPath.ini 全从它取。
# 0.15 开线：新增结构重建模块 me/formats/gamemaker_rebuild.py。
VERSION = "0.15 Beta 00053"
AUTHOR = "Resolved-Patient"

# 是否被打包成 exe（PyInstaller 会设置 sys.frozen）。
# 打包后 __file__ 指向临时解包目录，不能再拿它推项目根目录，
# 详见下面 _resolve_root() 的注释。
IS_FROZEN = bool(getattr(sys, "frozen", False))


def _resolve_root() -> Path:
    """定位「程序根目录」——tools/ 、work/ 、setup.ini 都挂在它下面。

    源码运行：config.py 在 <root>/me/ 下，往上两级就是 root。
    冻结成 exe：__file__ 指向 PyInstaller 的临时解包目录（_MEIPASS），
    那里面只有被打进 exe 的 Python 代码，**没有** tools/ ——外部 CLI
    是几百 MB 的独立程序，只能留在 exe 旁边。所以冻结后一律以 exe
    所在目录为根，这样双击 exe 时 work/ 和 setup.ini 也落在 exe 旁边，
    天然就是「绿色便携」的布局。
    """
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = _resolve_root()
TOOLS_DIR = ROOT / "tools"        # 随包发布的外部 CLI
WORK_DIR = ROOT / "work"          # 中间产物（脱壳、临时解包）

# 提取结果的默认位置不再写死在这里，而是由 exe/项目根目录下的 setup.ini
# 决定（见 me/setup_ini.py）。setup.ini 不存在时会自动生成一份，
# 默认值是 "Extract Resources"。
SETUP_INI = ROOT / "setup.ini"

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

# 进程退出码。GUI 壳按这个判成败，数值定死不要再改。
EXIT_OK = 0            # 成功
EXIT_TARGET = 1        # 目标不存在或不可读
EXIT_ARGS = 2          # 参数错误
EXIT_EMPTY = 3         # 跑完了，但一个文件都没提取到
EXIT_TOOL = 4          # 缺少外部 CLI
EXIT_CANCELLED = 5     # 被 --cancel-file 取消
EXIT_INTERNAL = 70     # 未预期的内部异常
EXIT_INTERRUPT = 130   # Ctrl+C

# 子进程超时（秒）。大游戏解包可能很久，所以给得比较宽松。
TOOL_TIMEOUT = 3600

# 单个文件读取上限，用于二进制特征扫描（默认 64MB，避免把大 exe 全读进内存）
SCAN_LIMIT = 64 * 1024 * 1024


def ensure_dirs() -> None:
    """确保外部工具目录和工作目录存在。

    结果目录不在这里创建 —— 它由 setup.ini 指定，可能是任意路径
    （甚至别的盘），等真正要写文件时再按需建，免得平白无故
    在用户指定的位置留一个空目录。
    """
    for d in (TOOLS_DIR, WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)
