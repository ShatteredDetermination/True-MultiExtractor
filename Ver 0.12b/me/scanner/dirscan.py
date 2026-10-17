# -*- coding: utf-8 -*-
"""扫描目录，建立文件索引。

替代原脚本里的 `cmd /c dir /b /s`：
  - 不依赖 cmd 和 GBK 编码，中文/超长路径都不会炸
  - 遇到超大目录会截断，不会卡死
"""

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

# 目录里文件太多时最多统计这么多个
MAX_ENTRIES = 60000
# 目录嵌套太深就放弃（游戏一般不会超过这个）
MAX_DEPTH = 12

# 这些目录没必要进去找主程序
SKIP_DIRS = {
    "$recycle.bin", "system volume information", "node_modules",
    "__pycache__", ".git", ".svn", "windows", "temp",
}


@dataclass
class FileIndex:
    """一个目录的文件索引。"""
    root: Path
    names: List[str] = field(default_factory=list)          # 全部文件名（小写）
    dirnames: List[str] = field(default_factory=list)       # 全部目录名（小写）
    exts: Counter = field(default_factory=Counter)          # 扩展名计数
    top: List[str] = field(default_factory=list)            # 顶层条目名（小写）
    exes: List[Path] = field(default_factory=list)          # 找到的 exe
    truncated: bool = False

    def has(self, *names: str) -> bool:
        """所有名字都出现过（文件或目录都算）。"""
        return all(self._has_one(n) for n in names)

    def has_any(self, *names: str) -> bool:
        return any(self._has_one(n) for n in names)

    def _has_one(self, name: str) -> bool:
        low = name.lower()
        return (low in self.names or low in self.dirnames or low in self.top)

    def has_file(self, *names: str) -> bool:
        """只认文件，不认目录。"""
        return all(n.lower() in self.names for n in names)

    def has_dir(self, *names: str) -> bool:
        """只认目录。"""
        lows = {n.lower() for n in names}
        return any(low in self.dirnames or low in self.top for low in lows)

    def has_ext(self, ext: str) -> bool:
        return self.exts.get(ext.lower(), 0) > 0

    def count_ext(self, ext: str) -> int:
        return self.exts.get(ext.lower(), 0)


def scan(root, max_entries: int = MAX_ENTRIES) -> FileIndex:
    """遍历目录建立索引。"""
    root = Path(root)
    idx = FileIndex(root=root)

    try:
        for entry in os.scandir(root):
            idx.top.append(entry.name.lower())
    except OSError:
        return idx

    seen = 0
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        # 剪枝：跳过无关目录、限制深度
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth >= MAX_DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = [
            d for d in dirnames
            if d.lower() not in SKIP_DIRS and not d.startswith("$")
        ]
        for d in dirnames:
            idx.dirnames.append(d.lower())

        for fn in filenames:
            lower = fn.lower()
            idx.names.append(lower)
            idx.exts[os.path.splitext(lower)[1]] += 1
            if lower.endswith(".exe"):
                idx.exes.append(Path(dirpath) / fn)
            seen += 1
            if seen >= max_entries:
                idx.truncated = True
                return idx
    return idx


def read_head(path, limit: int) -> bytes:
    """读文件头部若干字节，读不动就返回已读到的部分。"""
    try:
        with open(path, "rb") as f:
            return f.read(limit)
    except OSError:
        return b""
