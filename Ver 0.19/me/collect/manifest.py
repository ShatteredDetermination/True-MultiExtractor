# -*- coding: utf-8 -*-
"""清单导出：把提取结果写成 JSON 和人类可读的 TXT 报表。"""

import json
import time
from pathlib import Path


def write_manifest(out_root, writer, extra=None) -> Path:
    """写出 output/manifest.json。"""
    root = Path(out_root)
    root.mkdir(parents=True, exist_ok=True)
    data = {
        "tool": "True MultiExtractTool",
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "engine": writer.engine,
        "source": writer.source,
        "counts": writer.counts,
        "bytes": writer.bytes,
        "total_files": writer.total(),
        "files": writer.records,
    }
    if extra:
        data.update(extra)
    path = root / "manifest.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def write_report(out_root, writer, title: str = "提取报表") -> Path:
    """写出 output/REPORT.txt（给人看的）。"""
    from me.config import CATEGORY_LABEL

    root = Path(out_root)
    root.mkdir(parents=True, exist_ok=True)
    lines = [
        "=" * 60,
        f"  {title}",
        f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"  来源: {writer.source}",
        f"  引擎: {writer.engine or '未知'}",
        "=" * 60,
        "",
        "分类统计:",
    ]
    for cat, n in sorted(writer.counts.items(), key=lambda kv: -kv[1]):
        label = CATEGORY_LABEL.get(cat, cat)
        size = writer.bytes.get(cat, 0)
        lines.append(f"  {label:<6} {n:>7} 个   {_human(size):>10}")
    lines += [
        f"  {'合计':<6} {writer.total():>7} 个   "
        f"{_human(sum(writer.bytes.values())):>10}",
        "",
        "=" * 60,
    ]
    path = root / "REPORT.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} GB"
