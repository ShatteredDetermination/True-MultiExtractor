# -*- coding: utf-8 -*-
"""发布前的清理与打包（源码包）。

用法：
    python make_release.py                 # 只清理临时文件
    python make_release.py --zip           # 清理后打一个 zip
    python make_release.py --drop-utmt     # 不打包 UTMT（规避 GPL，见 THIRD_PARTY.md）
    python make_release.py --keep-download # 保留 tools/_download 里的安装包

清理掉的东西：
    __pycache__/            Python 字节码缓存
    work/                   脱壳/解包的中间产物
    Extract Resources/      提取结果（默认保存位置，见 setup.ini）
    output/                 旧版默认保存位置，顺手一起清
    dist/ build_exe/        打 exe 的产物和中间文件
    selftest/_out/          自测的输出目录
    tools/_download/        下载的安装包（已解压完就没用了）
    setup.ini               本机配置，不该跟着发布包走
"""

import argparse
import os
import shutil
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 直接整个删掉的目录（相对项目根）
PURGE_DIRS = (
    "work",
    "Extract Resources",
    "output",
    "dist",
    "build_exe",
    "selftest/_out",
    "tools/_download",
)

# 单独删掉的文件
PURGE_FILES = (
    "setup.ini",
)

# 不打包进发布 zip 的东西
EXCLUDE_DIRS = {
    "__pycache__", "work", "output", "Extract Resources", "_out",
    "_download", "dist", "build_exe", "resources", ".git",
}


def purge(keep_download: bool) -> None:
    """删临时目录和 __pycache__。"""
    targets = list(PURGE_DIRS)
    if keep_download:
        targets = [t for t in targets if t != "tools/_download"]

    for rel in targets:
        path = ROOT / rel
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            print(f"  删除 {rel}/")

    for rel in PURGE_FILES:
        path = ROOT / rel
        if path.exists():
            try:
                path.unlink()
                print(f"  删除 {rel}")
            except OSError:
                pass

    n = 0
    for root, dirs, _files in os.walk(ROOT):
        for d in list(dirs):
            if d == "__pycache__":
                shutil.rmtree(Path(root) / d, ignore_errors=True)
                dirs.remove(d)
                n += 1
    if n:
        print(f"  删除 {n} 个 __pycache__ 目录")


def stat_tree(root: Path, skip_utmt: bool = False) -> tuple:
    """统计文件数和总大小。"""
    count = 0
    total = 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        if skip_utmt and Path(dirpath).name == "utmt":
            dirs[:] = []
            continue
        for f in files:
            count += 1
            try:
                total += (Path(dirpath) / f).stat().st_size
            except OSError:
                pass
    return count, total


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def make_zip(drop_utmt: bool) -> Path:
    """打一个源码发布 zip。"""
    stamp = time.strftime("%Y%m%d")
    name = f"MultiExtractor_src_{stamp}.zip"
    out = ROOT.parent / name
    print(f"\n  打包 -> {out}")

    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dirpath, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            if drop_utmt and Path(dirpath).name == "utmt":
                dirs[:] = []
                continue
            for f in files:
                full = Path(dirpath) / f
                rel = full.relative_to(ROOT.parent)
                try:
                    z.write(full, rel.as_posix())
                    n += 1
                except OSError:
                    pass
    print(f"  写入 {n} 个文件, {human(out.stat().st_size)}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="发布前清理与打包（源码包）")
    ap.add_argument("--zip", action="store_true", help="清理后打一个 zip")
    ap.add_argument("--drop-utmt", action="store_true",
                    help="不打包 tools/utmt（规避 GPL，详见 THIRD_PARTY.md）")
    ap.add_argument("--keep-download", action="store_true",
                    help="保留 tools/_download 里的安装包")
    args = ap.parse_args()

    print("=" * 60)
    print("  清理")
    print("=" * 60)
    purge(args.keep_download)

    count, total = stat_tree(ROOT, skip_utmt=args.drop_utmt)
    print("\n" + "=" * 60)
    print(f"  当前项目: {count} 个文件, {human(total)}")
    if args.drop_utmt:
        print("  （已排除 tools/utmt）")
    print("=" * 60)

    if args.zip:
        make_zip(args.drop_utmt)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
