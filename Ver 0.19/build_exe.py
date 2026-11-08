# -*- coding: utf-8 -*-
"""把 MultiExtractor 打包成「免装 Python」的 exe。

    python build_exe.py                       # 打包到 dist/MultiExtractor/
    python build_exe.py --dest "D:\\某处"      # 组装到指定目录
    python build_exe.py --zip                 # 顺便压一个 zip
    python build_exe.py --slim                # Slim 包：不带 tools/（V0.18E 起这是默认形态）
    python build_exe.py --no-tools            # 先不拷 tools/（调试打包本身用）

Slim 与全量的区别只有一处：包里有不有 tools/。Slim 包的外部工具靠
tools.ini 清单 + 软键菜单在用户机器上现补，所以冒烟自测要反过来断言
「四个工具都在**清单**里、但都**没装**」，并且目录里不许出现 tools/。

为什么要用 onedir 而不是 onefile
--------------------------------
随包发布的 tools/ 里有四个外部 CLI（UTMT / AssetStudio / evbunpack / repak），
加起来 170 多 MB，而且它们必须是磁盘上的真实文件才能被当成子进程启动。
onefile 会把它们塞进 exe、每次启动再解压一遍 —— 启动要等十几秒，纯属找罪受。
所以这里用 onedir：exe 只装 Python 代码，tools/ 老实待在它旁边。

运行时目录叫 resources 而不是 PyInstaller 默认的 _internal
---------------------------------------------------------
_internal 这名字对用户没有任何意义，还容易被误当成「内部文件、可以删」。
用 --contents-directory 改成 resources，含义清楚些。

打包完的目录结构（就是最终发布包的样子）::

    <dest>/
        MultiExtractorCLI.exe   <- 双击这个（GUI 里叫「同级目录的 CLI」）
        resources/              <- Python 运行时（别删）
        tools/                  <- 外部 CLI（不含维护者脚本 setup_tools.py）
        setup.ini               <- 提取结果的保存位置，用户拿记事本改
        README.md  FORMATS.md  THIRD_PARTY.md  GUI-API.md  CLI-ARGS.md

发布包里保证没有任何 .py / .pyc —— 见 check_no_source()。
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

from me import cli_path_ini, config, setup_ini  # noqa: E402

# exe 名与目录名分开：
#   EXE     = MultiExtractorCLI  —— 用户双击的就是它，和 MultiExtractorGUI.exe
#             放同一目录时必须一眼分得清谁是谁，所以带 CLI 后缀
#   PRODUCT = MultiExtractor     —— 产物目录名 / zip 名保持产品名
EXE = "MultiExtractorCLI"
PRODUCT = "MultiExtractor"
APP = EXE          # 兼容旧引用：凡是拼 exe 名的地方都用 APP
DOCS = ("README.md", "FORMATS.md", "THIRD_PARTY.md", "GUI-API.md",
        "CLI-ARGS.md")

# PyInstaller 的原始产物和中间文件都塞在这个目录里，
# 真正的发布目录由 --dest 决定（默认 dist/MultiExtractor）。
BUILD_DIR = ROOT / "build_exe"
PYI_DIST = BUILD_DIR / "dist"
PYI_WORK = BUILD_DIR / "work"
DEFAULT_DEST = ROOT / "dist" / PRODUCT

# 运行时目录名（PyInstaller --contents-directory）
CONTENTS_DIR = "resources"

# 外部工具目录（原样复制到发布包里）
TOOLS_SRC = ROOT / "tools"

# tools/ 里这些文件不带进发布包。
# setup_tools.py 是维护者用的下载/更新脚本，要本机装 Python 才能跑 ——
# 打包版用户没有 Python，留着既没用，又平白多出一份可读源码。
TOOLS_SKIP_FILES = {"setup_tools.py"}

# 发布包里绝不允许出现的文件后缀（Python 源码 / 字节码）。
# 我们的代码应该全部编译进 exe 里的 PYZ，外面不该有明文。
SOURCE_SUFFIXES = {".py", ".pyc", ".pyo"}

# 发布包压缩级别。包体大头是 .NET 程序集和 DLL，压得动，级别给足换体积。
ZIP_LEVEL = 6

# 明确排掉用不到的东西，别让 exe 白胖一圈。
# numpy 是实测排除的：它只用于 CTF 图片转码的加速，而在真实的 137MB
# Clickteam 游戏（dtlg）上开与不开分别是 8.0s / 8.1s —— 瓶颈在磁盘 I/O，
# 不在像素转换。省下约 40MB 而没有任何可测量的代价。
EXCLUDES = (
    "numpy",
    "tkinter",
    "PIL",
    "matplotlib",
    "pytest",
    "IPython",
    "notebook",
    "setuptools",
    "pip",
)

# 这些引擎模块是 importlib 动态导入的（me/engines/__init__.py 用 f-string 拼模块名），
# PyInstaller 的静态分析看不见它们，必须显式收集，否则打出来的 exe
# 会「能启动但一个引擎都没有」，而且报错还被吞进 LOAD_ERRORS 里。
COLLECT_SUBMODULES = ("me",)

# 冒烟自测要求：--engines 必须列出这些键
EXPECT_ENGINES = (
    "electron", "gamemaker", "clickteam", "enigma", "godot",
    "rpgmaker", "unity", "unreal", "generic",
)
# --tools 必须找到这些外部工具
EXPECT_TOOLS = ("utmt", "assetstudio", "evbunpack", "repak")

# Slim 包的体积上限（MB）。上限存在的意义是**挡住 tools/ 被偷偷带进来**：
# 不带外部 CLI 的 CLI 目录大约 40~60 MB，一旦越过这个数就说明塞了别的东西。
SLIM_BUDGET_MB = 120.0


def log(msg: str) -> None:
    print(f"[i] {msg}", flush=True)


def die(msg: str) -> None:
    print(f"[err] {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


# ------------------------------------------------------------------ 步骤
def check_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        die("未安装 PyInstaller。先执行:\n"
            f"    {sys.executable} -m pip install pyinstaller")


def clean(dest: Path) -> None:
    for d in (BUILD_DIR, dest):
        if d.exists():
            log(f"清理 {d}")
            shutil.rmtree(d, ignore_errors=True)


def run_pyinstaller() -> None:
    argv = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",          # 见模块开头说明：onefile 在这里是错的
        "--console",         # 需要交互输入 + 实时看外部 CLI 的输出
        "--contents-directory", CONTENTS_DIR,
        "--name", EXE,
        "--distpath", str(PYI_DIST),
        "--workpath", str(PYI_WORK),
        "--specpath", str(BUILD_DIR),
    ]
    for mod in COLLECT_SUBMODULES:
        argv += ["--collect-submodules", mod]
    for mod in EXCLUDES:
        argv += ["--exclude-module", mod]
    argv.append(str(ROOT / "main.py"))

    log("PyInstaller 打包中...")
    t0 = time.time()
    proc = subprocess.run(argv, cwd=str(ROOT))
    if proc.returncode != 0:
        die(f"PyInstaller 失败，退出码 {proc.returncode}")
    log(f"PyInstaller 完成，耗时 {time.time() - t0:.1f}s")

    raw = PYI_DIST / EXE
    if not (raw / f"{APP}.exe").exists():
        die(f"未生成 {raw / (APP + '.exe')}")


def _clone(src: Path, dst: Path) -> None:
    """复制文件；同一卷上优先用硬链接，快且不占额外空间。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def copy_runtime(dest: Path) -> int:
    """把 exe 和 resources/ 搬到发布目录。"""
    raw = PYI_DIST / EXE
    n = 0
    for src in raw.rglob("*"):
        if not src.is_file():
            continue
        _clone(src, dest / src.relative_to(raw))
        n += 1
    log(f"带入 exe + {CONTENTS_DIR}/ 共 {n} 个文件")
    return n


def copy_tools(dest: Path) -> int:
    """把 tools/ 原样搬进发布目录（跳过下载缓存和 __pycache__）。"""
    if not TOOLS_SRC.exists():
        die(f"未找到 {TOOLS_SRC}")

    skip_dirs = {"_download", "__pycache__"}
    n = 0
    total = 0
    skipped = []
    for src in TOOLS_SRC.rglob("*"):
        rel = src.relative_to(TOOLS_SRC)
        if any(part in skip_dirs for part in rel.parts):
            continue
        if src.is_dir():
            continue
        if src.name in TOOLS_SKIP_FILES:
            skipped.append(rel.as_posix())
            continue
        _clone(src, dest / "tools" / rel)
        n += 1
        total += src.stat().st_size
    log(f"带入外部工具 {n} 个文件（{total / 1024 / 1024:.1f} MB）")
    if skipped:
        log(f"  已跳过维护者脚本：{', '.join(skipped)}")
    return n


def copy_docs(dest: Path) -> None:
    for name in DOCS:
        src = ROOT / name
        if src.exists():
            _clone(src, dest / name)
    log("带入说明文档")


def write_setup_ini(dest: Path) -> None:
    """在发布目录里生成一份带注释的默认 setup.ini。"""
    target = dest / setup_ini.SETUP_NAME
    target.write_text(setup_ini.render(), encoding="utf-8")
    log(f"生成 {setup_ini.SETUP_NAME}（默认保存到 "
        f"{setup_ini.DEFAULT_OUTPUT!r}）")


def write_version_ini(dest: Path) -> None:
    """在发布目录里生成 version.ini（GUI 右上角版本标签读它）。

    版本与署名的唯一来源还是 me/config.py；这里只是把它先落成文件，
    让发布包在第一次运行之前就能显示正确的版本。
    """
    target = dest / cli_path_ini.VER_NAME
    target.write_text(cli_path_ini.render_version(), encoding="utf-8-sig")
    log(f"生成 {cli_path_ini.VER_NAME}（{config.VERSION} / {config.AUTHOR}）")


def check_shape(dest: Path, slim: bool = False) -> None:
    """确认发布目录的形态符合预期。slim=True 时反过来要求没有 tools/。"""
    if not (dest / f"{APP}.exe").exists():
        die(f"发布目录中无 {APP}.exe")
    if not (dest / CONTENTS_DIR).is_dir():
        die(f"发布目录缺少 {CONTENTS_DIR}/: "
            f"--contents-directory 未生效")
    if (dest / "_internal").exists():
        die("发布目录仍存在 _internal/: --contents-directory 未生效")
    if not (dest / setup_ini.SETUP_NAME).exists():
        die(f"发布目录中无 {setup_ini.SETUP_NAME}")
    if not (dest / cli_path_ini.VER_NAME).exists():
        die(f"发布目录中无 {cli_path_ini.VER_NAME}")
    if slim:
        tools = dest / "tools"
        if tools.exists():
            exes = sorted(x.name for x in tools.rglob("*.exe"))
            die(f"Slim 包里不该有 tools/（发现 {len(exes)} 个 exe: {exes[:5]}）")
        log(f"目录形态正常（Slim）：exe + {CONTENTS_DIR}/ + "
            f"{setup_ini.SETUP_NAME} + 文档，无 tools/")
    else:
        log(f"目录形态正常：exe + {CONTENTS_DIR}/ + tools/ + "
            f"{setup_ini.SETUP_NAME} + 文档")


def check_no_source(dest: Path) -> None:
    """确认发布包里没有 Python 源码 / 字节码文件。

    程序代码应该全部编译进 exe 内部的 PYZ，外面不该出现明文 .py。
    实际翻过一次车：tools/setup_tools.py 被原样搬了进来。
    这类东西混进来不会有任何功能异常，只会静悄悄地漏源码，
    所以必须靠断言挡住，不能靠人记得检查。
    """
    found = sorted(
        f.relative_to(dest).as_posix()
        for f in dest.rglob("*")
        if f.is_file() and f.suffix.lower() in SOURCE_SUFFIXES
    )
    if found:
        die("发布包内出现 Python 源码/字节码文件（共 "
            f"{len(found)} 个）:\n  " + "\n  ".join(found[:15])
            + "\n  检查 TOOLS_SKIP_FILES 与 copy_* 过滤条件。")
    log("确认发布包内无任何 .py / .pyc / .pyo")


def smoke_test(dest: Path, slim: bool = False) -> None:
    """跑一遍打出来的 exe，确认引擎和工具都在。

    这一步是必须的：动态导入的引擎模块一旦没被打进去，
    exe 照样能启动、只是静默地少了所有引擎，光看打包日志发现不了。
    """
    exe = dest / f"{APP}.exe"
    env = dict(os.environ)
    env["ME_NO_PAUSE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # 冒烟自测必须离线可重复：不拉远端清单、不弹软键菜单
    env["ME_NO_NET"] = "1"
    env["ME_NO_SOFTKEY"] = "1"

    def run(args):
        r = subprocess.run([str(exe), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env,
                           timeout=180)
        return (r.stdout or "") + (r.stderr or ""), r.returncode

    log("冒烟自测：--engines")
    out, _code = run(["--engines"])
    missing = [k for k in EXPECT_ENGINES if k not in out]
    if missing:
        die("以下引擎未打进 exe: " + ", ".join(missing) + "\n" + out)
    log("  9 个引擎全部就位")

    if slim:
        log("冒烟自测：--tools（Slim：清单要有 4 个工具，本机一个都没装）")
        out, code = run(["--tools"])
        bad = [k for k in EXPECT_TOOLS if k not in out]
        if bad:
            die("--tools 的清单里少了这些工具: " + ", ".join(bad) + "\n" + out)
        if "工具清单来源" not in out:
            die("--tools 没说明清单来源:\n" + out)
        if "全量包" not in out:
            die("--tools 没给出全量包地址（用户不知道去哪下）:\n" + out)
        if code not in (0, 4):
            die(f"--tools 退出码异常: {code}\n" + out)
        log(f"  清单 4 个工具齐全，本机未安装（退出码 {code} 是预期的）")
    else:
        log("冒烟自测：--tools")
        out, _code = run(["--tools"])
        if "缺失" in out:
            die("外部工具未正确带入:\n" + out)
        bad = [k for k in EXPECT_TOOLS if k not in out]
        if bad:
            die("--tools 未见这些工具: " + ", ".join(bad) + "\n" + out)
        log("  4 个外部工具全部就位")

    log("冒烟自测：--setup")
    out, _code = run(["--setup"])
    if "输出目录" not in out:
        die("--setup 输出异常:\n" + out)
    # 确认它读的是发布目录里那份 setup.ini，而不是别处的
    if str(dest) not in out:
        die("--setup 指向的 setup.ini 不在发布目录:\n" + out)
    log("  setup.ini 定位正确")

    if slim:
        log("冒烟自测：--channels（Slim 下也必须有 9 行通道）")
        out, _code = run(["--channels"])
        # 每个引擎两行（第三方 / 自研），所以按「带 [ok] 且写着第三方:」数
        rows = [ln for ln in out.splitlines()
                if ln.strip().startswith("[ok]") and "第三方:" in ln]
        if len(rows) < 9:
            die(f"--channels 只有 {len(rows)} 行，引擎可能没打全:\n" + out)
        log(f"  {len(rows)} 个引擎的通道都列出来了")


def dir_stats(path: Path) -> tuple:
    n = 0
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            n += 1
            total += f.stat().st_size
    return n, total


def make_zip(dest: Path, zip_path: Path) -> tuple:
    """把发布目录压成一个 zip。"""
    log(f"压缩到 {zip_path}")
    t0 = time.time()
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()

    # 注意：级别必须给 ZipFile 构造函数。
    # 实测过一条弯路——传入显式 ZipInfo 时 ZipFile 的 compresslevel 会被
    # 完全忽略（level 1/6/9 打出来的包一样大，都走了 zlib 默认值），
    # 所以这里统一用 zf.write()，别自己造 ZipInfo 逐文件调级别。
    n = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=ZIP_LEVEL) as zf:
        for f in sorted(dest.rglob("*")):
            if not f.is_file():
                continue
            arc = Path(dest.name) / f.relative_to(dest)
            zf.write(f, str(arc).replace("\\", "/"))
            n += 1

    log(f"压缩完成：{n} 个文件，"
        f"{zip_path.stat().st_size / 1024 / 1024:.1f} MB，"
        f"耗时 {time.time() - t0:.1f}s")
    return n, zip_path.stat().st_size


# ------------------------------------------------------------------ 入口
def main() -> int:
    ap = argparse.ArgumentParser(description="打包 MultiExtractor 为 exe")
    ap.add_argument("--dest", help=f"发布目录（默认 {DEFAULT_DEST}）")
    ap.add_argument("--zip", action="store_true",
                    help="生成 zip（默认路径 <dest>.zip）")
    ap.add_argument("--zip-path", help="zip 输出路径")
    ap.add_argument("--full", action="store_true",
                    help="打全量包：带 tools/（需要先跑 tools/setup_tools.py）")
    ap.add_argument("--no-tools", action="store_true",
                    help="不带入 tools/（调试打包本身用，不做 slim 校验）")
    ap.add_argument("--skip-test", action="store_true", help="跳过冒烟自测")
    args = ap.parse_args()
    # V0.18E 起默认 Slim：不带 tools/。要全量得显式 --full。
    args.slim = not args.full and not args.no_tools

    dest = Path(args.dest).resolve() if args.dest else DEFAULT_DEST

    print(f"[i] 版本 {config.VERSION} -> {dest}")
    check_pyinstaller()
    clean(dest)
    run_pyinstaller()
    copy_runtime(dest)
    if args.slim:
        log("Slim：跳过 tools/（外部工具改由 tools.ini 清单 + 软键菜单现补）")
    elif not args.no_tools:
        copy_tools(dest)
    copy_docs(dest)
    write_setup_ini(dest)
    write_version_ini(dest)
    check_shape(dest, args.slim)
    check_no_source(dest)
    if not args.skip_test:
        smoke_test(dest, args.slim)

    n, total = dir_stats(dest)
    mb = total / 1024 / 1024
    log(f"发布目录：{dest}")
    log(f"  共 {n} 个文件，{mb:.1f} MB")
    if args.slim:
        if mb > SLIM_BUDGET_MB:
            die(f"Slim 包 {mb:.1f} MB 超过了预算 {SLIM_BUDGET_MB:.0f} MB"
                "（大概率又把 tools/ 带进来了）")
        log(f"  Slim 体积合规：{mb:.1f} MB <= {SLIM_BUDGET_MB:.0f} MB")

    if args.zip:
        zip_path = (Path(args.zip_path) if args.zip_path
                    else dest.parent / f"{PRODUCT}_{config.VERSION.split()[0]}.zip")
        make_zip(dest, zip_path)
        log(f"成品 zip：{zip_path}")

    print()
    print(f"[i] 完成，双击运行 {dest / (APP + '.exe')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
