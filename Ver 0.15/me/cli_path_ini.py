# -*- coding: utf-8 -*-
"""把 CLI 自身的位置与版本写成一个 ini，给 GUI 壳读。

路径固定：``%LOCALAPPDATA%\\MultiExtractor\\CLIPath.ini``

GUI 壳只认这一个文件，扫不到就直接提示、不做任何后备 —— 所以这里**每次运行
都写**，不能只在首次生成：用户把整包挪到别的盘之后，GUI 必须立刻拿到新位置。

内容：

    [CLIDirPath]
    Dir = "<MultiExtractor.exe 所在目录>"

    [CLIVersion]
    Version = "0.14 Beta 00032"
    By = "Resolved-Patient"

``Dir`` 是目录不是 exe 全路径；``Version``/``By`` 就是壳右上角那个版本标签的
两行文字，改版本只改 me/config.py，壳不用动。
"""

import os
from pathlib import Path

from me import config

DIR_NAME = "MultiExtractor"
INI_NAME = "CLIPath.ini"
SECTION_DIR = "CLIDirPath"
SECTION_VER = "CLIVersion"


def ini_path() -> Path:
    """ini 的完整路径。"""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / DIR_NAME / INI_NAME


def render() -> str:
    """ini 的文本内容。"""
    return (
        f"[{SECTION_DIR}]\n"
        f'Dir = "{config.ROOT}"\n'
        f"\n"
        f"[{SECTION_VER}]\n"
        f'Version = "{config.VERSION}"\n'
        f'By = "{config.AUTHOR}"\n'
    )


def write() -> Path:
    """写出去。写不了就静默跳过 —— 这不影响提取本身。"""
    p = ini_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render(), encoding="utf-8")
    except OSError:
        pass
    return p
