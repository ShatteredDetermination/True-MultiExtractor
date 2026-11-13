# -*- coding: utf-8 -*-
"""骨架 mfa 的定位与记忆。

为什么要骨架
------------
自研转换把成品里的应用信息 / 资源库 / 帧写进一份**现成的 mfa 工程**里，
所以需要一份「壳工程」。任何一份 Fusion 2.5 存的 mfa 都能当骨架 ——
在 Fusion 里新建一个空白工程、存盘，就得到一份几 KB 的骨架。

这里的职责：找到它、记住它，别每次都问。
来源顺序：环境变量 `ME_MFA_TEMPLATE` → 上次记住的路径 → 程序目录 / 桌面 / 文档
里顺手能找到的 `*.mfa`。
"""

from pathlib import Path
from typing import List, Optional

from me.config import ROOT

CACHE_NAME = "mfa.ini"
SECTION = "MFA"
KEY = "template"

#: 只想找「壳工程」，别把几百 MB 的成品产物当成骨架
MAX_SIZE = 50 * 1024 * 1024


def cache_path() -> Path:
    return ROOT / CACHE_NAME


def _valid(p: Path) -> bool:
    try:
        if not p.is_file() or p.stat().st_size > MAX_SIZE:
            return False
        with p.open("rb") as f:
            return f.read(4) == b"MFU2"
    except OSError:
        return False


def remembered() -> Optional[Path]:
    """上次记住的骨架路径（文件还在才算数）。"""
    p = cache_path()
    if not p.exists():
        return None
    try:
        import configparser

        cfg = configparser.ConfigParser()
        cfg.read(p, encoding="utf-8")
        raw = cfg.get(SECTION, KEY, fallback="").strip()
    except Exception:
        return None
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if _valid(path) else None


def remember(path) -> None:
    """把用过的骨架记下来；文件不存在或不是 MFU2 就不记。"""
    p = Path(str(path)).expanduser()
    if not _valid(p):
        return
    try:
        import configparser

        cfg = configparser.ConfigParser()
        target = cache_path()
        if target.exists():
            cfg.read(target, encoding="utf-8")
        if not cfg.has_section(SECTION):
            cfg.add_section(SECTION)
        cfg.set(SECTION, KEY, str(p))
        with target.open("w", encoding="utf-8") as f:
            cfg.write(f)
    except OSError:
        pass


def candidates() -> List[Path]:
    """按优先级列出可能当骨架的 mfa（存在且是 MFU2 的才留下）。"""
    import os

    out: List[Path] = []

    env = os.environ.get("ME_MFA_TEMPLATE", "").strip()
    if env:
        out.append(Path(env).expanduser())
    got = remembered()
    if got:
        out.append(got)

    # 程序目录 / 桌面的浅层文件：够用，不扫全盘
    for folder in (ROOT, ROOT / "resources", Path.home() / "Desktop",
                   Path.home() / "Documents"):
        try:
            if folder.is_dir():
                out.extend(sorted(folder.glob("*.mfa")))
        except OSError:
            pass

    seen, uniq = set(), []
    for p in out:
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        if _valid(p):
            uniq.append(p)
    return uniq


def find() -> Optional[Path]:
    """找到第一份能用的骨架；找不到返回 None。"""
    got = candidates()
    return got[0] if got else None
