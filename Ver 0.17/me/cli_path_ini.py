# -*- coding: utf-8 -*-
"""写两份 ini 给 GUI 壳读：一份只放路径，一份只放版本。

为什么要拆成两份
----------------
``CLIPath.ini`` 在用户目录里，是「壳去哪儿找 CLI」的唯一线索；它的内容必须
**只有路径** —— 用户手改这个文件的时候只该看见一件事，混进去的版本行会让人
以为那也要改。版本和署名是 CLI 自己的属性，跟着 CLI 目录走：整包挪到别的盘，
属性自然跟过去，不会出现「路径已经指向新目录、版本还写着旧的」。

    1. ``%LOCALAPPDATA%\\MultiExtractor\\CLIPath.ini``   只有路径
       [CLIDirPath]
       Dir = "C:\\MultiExtractor\\Compiled\\Ver 0.15E"

    2. ``<CLI 目录>\\version.ini``                       只有版本与署名
       [CLIVersion]
       Version = "0.15E Beta 00066"
       By = "Resolved-Patient"

两份都**每次运行都写**，不能只写一次：用户把整包挪走之后，壳必须立刻拿到新
位置和新版本。写不了（只读目录之类）就静默跳过 —— 这不影响提取本身。

段名/键名保持原样（``CLIDirPath`` / ``CLIVersion``），壳那边的解析代码不用改，
只是第二个文件换了个位置读。
"""

import os
from pathlib import Path

from me import config

DIR_NAME = "MultiExtractor"
INI_NAME = "CLIPath.ini"          # 只放路径，在 %LOCALAPPDATA%
VER_NAME = "version.ini"          # 只放版本与署名，在 CLI 目录
SECTION_DIR = "CLIDirPath"
SECTION_VER = "CLIVersion"


def ini_path() -> Path:
    """路径 ini 的完整位置（%LOCALAPPDATA%\\MultiExtractor\\CLIPath.ini）。"""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / DIR_NAME / INI_NAME


def version_ini_path() -> Path:
    """版本 ini 的完整位置（CLI 目录下的 version.ini）。"""
    return Path(config.ROOT) / VER_NAME


def render() -> str:
    """路径 ini 的文本内容：**只有路径**。"""
    return (
        f"; 只存路径。版本与署名在 CLI 目录下的 {VER_NAME} 里。\n"
        f"[{SECTION_DIR}]\n"
        f'Dir = "{config.ROOT}"\n'
    )


def render_version() -> str:
    """版本 ini 的文本内容：版本与署名。"""
    return (
        f"[{SECTION_VER}]\n"
        f'Version = "{config.VERSION}"\n'
        f'By = "{config.AUTHOR}"\n'
    )


def _write_text(p: Path, text: str) -> None:
    # 带 BOM 的 UTF-8：文件里有中文注释，记事本/多数编辑器靠 BOM 才认得出
    # 是 UTF-8（否则中文 Windows 上按 GBK 打开就是乱码）。GUI 的读法
    # （ReadTextAuto）会跳过 BOM，不受影响。
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8-sig")
    except OSError:
        pass


def write() -> Path:
    """两份都写出去，返回路径 ini 的位置。"""
    p = ini_path()
    _write_text(p, render())
    _write_text(version_ini_path(), render_version())
    return p
