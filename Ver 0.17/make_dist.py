# -*- coding: utf-8 -*-
"""把 CLI 打包版和 GUI 壳组装成一份可直接拷走的发布目录。

    python make_dist.py                     # 用默认 GUI 路径组装 + 压 zip
    python make_dist.py --gui "D:\\壳\\MultiExtractorGUI.exe"
    python make_dist.py --skip-cli          # CLI 已经打好了，只组装
    python make_dist.py --check             # 只检查已有产物是否合规

产物结构（默认「同级平铺」：CLI 与壳放在同一个目录，壳优先看同级）::

    dist/MultiExtractor-<版本>-win64/
        MultiExtractorGUI.exe       .NET 10 自包含单文件壳
        MultiExtractorCLI.exe       CLI（壳优先在同一目录找到它）
        resources/  tools/          CLI 的运行时与外部工具
        setup.ini  version.ini      保存位置 / 版本署名
        README.md  ...              文档

    --layout subdir 仍可产出旧结构（CLI 放 cli\\ 子目录，壳会退一步去那里找）。

为什么要有这个脚本
------------------
壳和 CLI 在两个仓库里，靠手拷会漏东西、版本号也会对不上。这里把
「先 build_exe、再拷壳、再压包」固定成一条命令，并且断言关键文件在位。

顺序很重要
----------
make_release.py 会清掉 dist/ 和 build_exe/（它们都是产物目录），所以：

    python make_release.py --zip     # 先出源码包
    python make_dist.py              # 再组装 GUI + CLI 发布包

反过来的话，源码清理会把刚组装好的发布包删掉。
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from me import config  # noqa: E402

APP = "MultiExtractor"             # 产品名：目录名 / zip 名
CLI_EXE = "MultiExtractorCLI.exe"  # CLI 可执行文件（与壳同目录时要分得清）
GUI_EXE = "MultiExtractorGUI.exe"
CLI_SUBDIR = "cli"
PKG_PREFIX = f"{APP}-"
DEFAULT_CLI = ROOT / "dist" / APP
DEFAULT_OUT = ROOT / "dist"

# GUI 默认路径：本机开发时的 dotnet publish 输出。
# 别的机器上用 --gui 或 ME_GUI_EXE 指过去。
DEFAULT_GUI = Path(os.environ.get(
    "ME_GUI_EXE",
    r"C:\Users\Administrator\source\repos\MultiExtractorGUI"
    r"\MultiExtractorGUI\dist\MultiExtractorGUI.exe"))

ZIP_LEVEL = 6


def log(msg: str) -> None:
    print(f"[i] {msg}", flush=True)


def die(msg: str) -> None:
    print(f"[err] {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def short_version() -> str:
    """包名只取标签部分：0.17 Beta 00091 -> 0.17。"""
    return config.VERSION.split()[0]


def clone(src: Path, dst: Path) -> None:
    """同卷硬链接，跨卷退回复制 —— 组装 200MB 目录不该再花一份空间。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def run_cli_build() -> None:
    log("先打 CLI（build_exe.py）...")
    t0 = time.time()
    proc = subprocess.run([sys.executable, str(ROOT / "build_exe.py")],
                          cwd=str(ROOT))
    if proc.returncode != 0:
        die(f"build_exe.py 失败，退出码 {proc.returncode}")
    log(f"CLI 打包完成，耗时 {time.time() - t0:.1f}s")


def cli_target(pkg: Path, layout: str) -> Path:
    """CLI 文件放哪儿：flat = 与壳同级（壳优先看这里），subdir = cli\\ 子目录。"""
    return pkg if layout == "flat" else pkg / CLI_SUBDIR


def assemble(cli_dir: Path, gui_exe: Path, out_dir: Path,
             layout: str = "flat") -> Path:
    if not (cli_dir / CLI_EXE).exists():
        die(f"CLI 产物不完整，缺 {cli_dir / CLI_EXE}")
    if not gui_exe.exists():
        die(f"找不到 GUI exe: {gui_exe}\n"
            "    先执行: dotnet publish -c Release"
            "（在 MultiExtractorGUI 目录里），或用 --gui 指定路径")

    pkg = out_dir / f"{PKG_PREFIX}{short_version()}-win64"
    if pkg.exists():
        log(f"清理旧目录 {pkg}")
        shutil.rmtree(pkg, ignore_errors=True)

    base = cli_target(pkg, layout)
    n = 0
    for src in cli_dir.rglob("*"):
        if src.is_file():
            clone(src, base / src.relative_to(cli_dir))
            n += 1
    clone(gui_exe, pkg / GUI_EXE)
    where = "同级目录" if layout == "flat" else f"{CLI_SUBDIR}\\ 子目录"
    log(f"组装完成：{n} 个 CLI 文件（{where}）+ 1 个壳")
    return pkg


def check(pkg: Path, layout: str = "flat") -> None:
    """断言发布目录能直接用：壳在位、CLI 在位、版本一致。"""
    problems = []
    if not (pkg / GUI_EXE).exists():
        problems.append(f"缺 {GUI_EXE}")

    base = cli_target(pkg, layout)
    where = "" if layout == "flat" else f"{CLI_SUBDIR}/"
    if not (base / CLI_EXE).exists():
        problems.append(f"缺 {where}{CLI_EXE}")
    for rel in ("resources", "tools", "setup.ini", "version.ini"):
        if not (base / rel).exists():
            problems.append(f"缺 {where}{rel}")

    # 版本：CLI 目录的 version.ini 是唯一来源，必须与代码里的一致
    ver_ini = base / "version.ini"
    if ver_ini.exists():
        text = ver_ini.read_text(encoding="utf-8-sig", errors="replace")
        if config.VERSION not in text:
            problems.append(
                f"version.ini 与 config.VERSION 不一致（{config.VERSION}）")

    # 壳是 .NET 10 单文件：根目录的 exe 只能是「壳 + 同级 CLI」或「只有壳」
    root_exes = sorted(p.name for p in pkg.glob("*.exe"))
    want = sorted([GUI_EXE, CLI_EXE] if layout == "flat" else [GUI_EXE])
    if root_exes != want:
        problems.append(f"根目录 exe 应为 {want}，实际 {root_exes}")

    if problems:
        die("发布目录不合规:\n  " + "\n  ".join(problems))
    label = "同级目录" if layout == "flat" else "cli 子目录"
    log(f"布局合规：壳 + {label} + 版本一致")


def make_zip(pkg: Path) -> Path:
    out = pkg.parent / f"{pkg.name}.zip"
    if out.exists():
        out.unlink()
    t0 = time.time()
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=ZIP_LEVEL) as zf:
        for f in sorted(pkg.rglob("*")):
            if f.is_file():
                zf.write(f, (Path(pkg.name) / f.relative_to(pkg)).as_posix())
                n += 1
    log(f"压缩完成：{n} 个文件，{out.stat().st_size / 1024 / 1024:.1f} MB，"
        f"{time.time() - t0:.1f}s")
    return out


def dir_stats(path: Path):
    n = 0
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            n += 1
            total += f.stat().st_size
    return n, total


def main() -> int:
    ap = argparse.ArgumentParser(description="组装 CLI + GUI 发布目录")
    ap.add_argument("--cli", help=f"CLI 打包目录（默认 {DEFAULT_CLI}）")
    ap.add_argument("--gui", help=f"GUI exe（默认 {DEFAULT_GUI}）")
    ap.add_argument("--out", help=f"输出根目录（默认 {DEFAULT_OUT}）")
    ap.add_argument("--skip-cli", action="store_true",
                    help="不重新打 CLI，直接用已有产物")
    ap.add_argument("--no-zip", action="store_true", help="不压 zip")
    ap.add_argument("--layout", choices=("flat", "subdir"), default="flat",
                    help="flat = CLI 与壳同级（默认，壳优先看同级）；"
                         "subdir = CLI 放 cli\\ 子目录")
    ap.add_argument("--check", action="store_true",
                    help="只检查已有产物，不组装")
    args = ap.parse_args()

    cli_dir = Path(args.cli) if args.cli else DEFAULT_CLI
    gui_exe = Path(args.gui) if args.gui else DEFAULT_GUI
    out_dir = Path(args.out) if args.out else DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)

    pkg = out_dir / f"{PKG_PREFIX}{short_version()}-win64"

    if args.check:
        if not pkg.exists():
            die(f"没有可检查的目录: {pkg}")
        check(pkg, args.layout)
        return 0

    print(f"[i] 版本 {config.VERSION} -> {pkg}")
    if not args.skip_cli:
        run_cli_build()
    pkg = assemble(cli_dir, gui_exe, out_dir, args.layout)
    check(pkg, args.layout)

    n, total = dir_stats(pkg)
    log(f"发布目录：{pkg}")
    log(f"  {n} 个文件，{total / 1024 / 1024:.1f} MB")

    if not args.no_zip:
        log(f"成品 zip：{make_zip(pkg)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
