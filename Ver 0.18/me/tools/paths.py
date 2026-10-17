# -*- coding: utf-8 -*-
"""外部工具定位：清单（tools.ini）+ 本机落点。

V0.18E 起发布包是 Slim，不再随包带 tools/，所以这里的职责拆成两半：

* 「应该有哪些工具、去哪下」→ me/tools/manifest.py（远端 / 缓存 / 内置）
* 「本机有没有、在哪个目录」→ 本文件

查找顺序（先精确后宽松）：

    1. toolsdir.ini 里用户手动指定的目录（安装式布局）
    2. <程序根>/tools/                  （旧的全量包布局，仍然认）
    3. 程序根平铺                       （CLI 与壳同级时工具也在同级）

任何一步失败都只返回 None，不抛异常 —— 缺工具是常态，不是错误。
"""

import configparser
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from me.config import IS_FROZEN, ROOT, TOOLS_DIR
from me.tools import manifest as mf

OVERRIDE_SUBDIR = "MultiExtractor"
OVERRIDE_FILE = "toolsdir.ini"
OVERRIDE_SECTION = "ToolsDir"
OVERRIDE_KEY = "Dir"


# ---------------------------------------------------------------- 用户指定目录
def override_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / OVERRIDE_SUBDIR / OVERRIDE_FILE


def override_dir() -> Optional[Path]:
    """读 toolsdir.ini。没有 / 坏了 / 目录不在，一律 None。"""
    p = override_path()
    if not p.exists():
        return None
    cp = configparser.ConfigParser()
    try:
        cp.read(p, encoding="utf-8")
        raw = cp.get(OVERRIDE_SECTION, OVERRIDE_KEY, fallback="").strip().strip('"')
    except (configparser.Error, OSError, UnicodeDecodeError):
        return None
    if not raw:
        return None
    d = Path(raw)
    if not d.is_absolute():
        d = p.parent / d
    return d if d.is_dir() else None


def set_override_dir(directory) -> Path:
    """把用户选的目录写进 toolsdir.ini，返回文件路径。"""
    d = Path(directory).resolve()
    p = override_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    cp = configparser.ConfigParser()
    cp[OVERRIDE_SECTION] = {OVERRIDE_KEY: str(d)}
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("; 外部工具根目录。改这里等于换 tools/ 的位置。\n")
        cp.write(fh)
    return p


def clear_override_dir() -> None:
    try:
        override_path().unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- 清单
def specs() -> Dict[str, mf.ToolSpec]:
    return dict(mf.snapshot().tools)


def source_label() -> str:
    m = mf.snapshot()
    return {"remote": "远端", "cache": "本地缓存", "builtin": "内置兜底"}.get(
        m.source, m.source)


def _roots() -> List[Path]:
    """候选根目录：手动指定 > 程序根/tools > 程序根（平铺）。"""
    out: List[Path] = []
    od = override_dir()
    if od is not None:
        out.append(od)
    out.append(TOOLS_DIR)
    out.append(ROOT)
    return out


def find(key: str) -> Optional[Path]:
    """按清单里的候选相对路径找工作。找不到返回 None。"""
    spec = specs().get(key)
    if spec is None or not spec.paths:
        return None
    for root in _roots():
        for rel in spec.paths:
            p = root / rel
            if p.is_file():
                return p
    return None


def find_in(directory, names: List[str]) -> Optional[Path]:
    """在指定目录（含子目录）里找任一文件名。"""
    directory = Path(directory)
    if not directory.is_dir():
        return None
    lowered = {n.lower() for n in names}
    for root, _dirs, files in os.walk(directory):
        for fn in files:
            if fn.lower() in lowered:
                return Path(root) / fn
    return None


def available() -> Dict[str, bool]:
    return {key: find(key) is not None for key in specs()}


def installed() -> List[str]:
    return [k for k, ok in available().items() if ok]


def any_installed() -> bool:
    return bool(installed())


def missing() -> List[str]:
    return [k for k, ok in available().items() if not ok]


# ---------------------------------------------------------------- 提示文案
def repair_hint(key: str = "") -> str:
    """缺工具时给用户的修复办法（按是否冻结成 exe 分两种口径）。

    V0.18E 起第一选择是**让程序自己下**（`--tools-get`），而不是让用户
    去浏览器里点：软键 1 与这个参数是同一条路（me/tools/fetch.py）。
    """
    if IS_FROZEN:
        exe = "MultiExtractorCLI.exe"
        return (f"修复：让程序自己下 → {exe} --tools-get"
                "（只有 .7z 的那个可加 --tools-get-full）")
    cmd = f"python main.py --tools-get {key}".strip()
    return f"修复：{cmd}（或 python tools/setup_tools.py {key}）"


def download_hint() -> str:
    """全量包地址（来自清单，缺失时用内置兜底）。"""
    return mf.snapshot().full_package or mf.BUILTIN.full_package


def status() -> List[Tuple[str, Optional[Path], str, str]]:
    """`--tools` 用：(键, 实际路径, 说明, 期望相对路径)。"""
    out = []
    for key, spec in specs().items():
        out.append((key, find(key), spec.note or spec.name, spec.primary))
    return out
