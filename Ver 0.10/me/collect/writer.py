# -*- coding: utf-8 -*-
"""输出写出器。

负责三件事：
  1. 把文件按分类放进 output/<分类>/... 下面
  2. 清洗路径（防目录穿越、非法字符、重名冲突）
  3. 记账，供最后生成清单/报表
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from me.collect import kinds

# Windows 文件名非法字符
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
# 保留设备名
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_rel(rel: str) -> str:
    """把一个相对路径清洗成安全的相对路径（用 / 分隔）。"""
    rel = str(rel).replace("\\", "/")
    parts: List[str] = []
    for raw in rel.split("/"):
        if not raw or raw == ".":
            continue
        if raw == "..":
            continue
        # 去掉盘符和非法字符
        raw = raw.split(":")[-1]
        raw = _BAD_CHARS.sub("_", raw).strip().rstrip(".")
        if not raw:
            continue
        stem, ext = os.path.splitext(raw)
        if stem.upper() in _RESERVED:
            raw = f"{stem}_{ext}"
        # 单段太长会超出文件系统限制
        if len(raw) > 120:
            stem, ext = os.path.splitext(raw)
            raw = stem[:110] + ext[:9]
        parts.append(raw)
    return "/".join(parts) if parts else "unnamed"


class OutputWriter:
    """把提取出来的资源写进 output 目录，并记录清单。"""

    def __init__(self, out_root, categories=None, engine: str = "", source: str = ""):
        self.root = Path(out_root)
        self.categories = set(categories) if categories else None
        self.engine = engine
        self.source = source
        self.records: List[Dict] = []
        self.counts: Dict[str, int] = {}
        self.bytes: Dict[str, int] = {}
        self._taken: set = set()

    # ------------------------------------------------------------ 内部
    def _wanted(self, category: str) -> bool:
        return self.categories is None or category in self.categories

    def _unique(self, path: Path) -> Path:
        """同一路径被占用时加 _1 _2 后缀。"""
        key = str(path).lower()
        if key not in self._taken and not path.exists():
            self._taken.add(key)
            return path
        stem, ext = os.path.splitext(path.name)
        for i in range(1, 10000):
            cand = path.with_name(f"{stem}_{i}{ext}")
            ckey = str(cand).lower()
            if ckey not in self._taken and not cand.exists():
                self._taken.add(ckey)
                return cand
        raise RuntimeError(f"无法为 {path} 找到可用文件名")

    def _record(self, category: str, path: Path, size: int, origin: str) -> None:
        self.counts[category] = self.counts.get(category, 0) + 1
        self.bytes[category] = self.bytes.get(category, 0) + size
        try:
            rel = path.relative_to(self.root).as_posix()
        except ValueError:
            rel = str(path)
        self.records.append({
            "category": category,
            "path": rel,
            "size": size,
            "engine": self.engine,
            "origin": origin,
        })

    def _prepare(self, category: str, rel: str) -> Optional[Path]:
        if not self._wanted(category):
            return None
        rel = sanitize_rel(rel)
        path = self.root / category / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        return self._unique(path)

    # ------------------------------------------------------------ 对外
    def wants(self, category: str) -> bool:
        """这个分类要不要（供引擎提前跳过无用功）。"""
        return self._wanted(category)

    def write_bytes(self, category: str, rel: str, data: bytes,
                    origin: str = "") -> Optional[Path]:
        path = self._prepare(category, rel)
        if path is None:
            return None
        with open(path, "wb") as f:
            f.write(data)
        self._record(category, path, len(data), origin)
        return path

    def copy_from(self, category: str, rel: str, src,
                  origin: str = "") -> Optional[Path]:
        path = self._prepare(category, rel)
        if path is None:
            return None
        size = 0
        with open(src, "rb") as fi, open(path, "wb") as fo:
            while True:
                block = fi.read(1 << 20)
                if not block:
                    break
                fo.write(block)
                size += len(block)
        self._record(category, path, size, origin or str(src))
        return path

    def write_auto(self, rel: str, data: bytes, origin: str = "") -> Optional[Path]:
        """不指定分类，按扩展名自动分类。"""
        return self.write_bytes(kinds.classify(rel), rel, data, origin)

    def count(self, category: str) -> int:
        return self.counts.get(category, 0)

    def total(self) -> int:
        return sum(self.counts.values())

    def summary(self) -> Dict[str, int]:
        return dict(self.counts)
