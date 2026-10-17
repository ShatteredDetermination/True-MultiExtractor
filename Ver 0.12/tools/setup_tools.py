# -*- coding: utf-8 -*-
"""下载并部署外部 CLI 到 tools/ 目录。

发布包已经带好工具，用户端不需要跑这个脚本；
它是给维护者用的：换机器 / 升级工具版本 / 补下缺失的工具。

用法：
    python tools/setup_tools.py              # 下载全部缺失的
    python tools/setup_tools.py --force      # 强制重下
    python tools/setup_tools.py --mirror     # 走 GitHub 镜像（国内加速）
    python tools/setup_tools.py utmt         # 只下某一个
"""

import argparse
import os
import shutil
import sys
import time
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DL_DIR = os.path.join(HERE, "_download")

# 镜像前缀，按顺序尝试
MIRRORS = (
    "",
    "https://ghfast.top/",
    "https://ghproxy.net/",
    "https://gh-proxy.com/",
)

# name: (展示名, 下载地址, 解压到 tools/ 下的子目录, 期望的可执行文件名)
TOOLS = {
    "utmt": (
        "UndertaleModTool CLI (GameMaker data.win)",
        "https://github.com/UnderminersTeam/UndertaleModTool/releases/download/0.9.2.0/UTMT_CLI_v0.9.2.0-Windows.zip",
        "utmt",
        "UndertaleModCli.exe",
    ),
    "assetstudio": (
        "AssetStudio CLI (Unity)",
        None,  # 本地已有，不需要下载
        "assetstudio",
        "AssetStudio.CLI.exe",
    ),
    "evbunpack": (
        "evbunpack (Enigma Virtual Box 脱壳)",
        "https://github.com/mos9527/evbunpack/releases/download/0.2.6/evbunpack.exe",
        "evbunpack",
        "evbunpack.exe",
    ),
    "repak": (
        "repak (Unreal .pak)",
        "https://github.com/trumank/repak/releases/download/v0.2.3/repak_cli-x86_64-pc-windows-msvc.zip",
        "repak",
        "repak.exe",
    ),
}


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def stream_download(url: str, dest: str, label: str = "") -> bool:
    """流式下载，每 2 秒报一次进度。"""
    req = urllib.request.Request(url, headers={"User-Agent": "MultiExtractor-setup"})
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            t0 = time.time()
            last = 0.0
            with open(tmp, "wb") as f:
                while True:
                    block = resp.read(1 << 18)
                    if not block:
                        break
                    f.write(block)
                    done += len(block)
                    now = time.time()
                    if now - last >= 2.0:
                        last = now
                        speed = done / max(now - t0, 0.001)
                        if total:
                            pct = done * 100 // total
                            sys.stdout.write(
                                f"\r    {label} {pct:3d}%  {human(done)}/{human(total)}"
                                f"  {human(speed)}/s      "
                            )
                        else:
                            sys.stdout.write(
                                f"\r    {label} {human(done)}  {human(speed)}/s      "
                            )
                        sys.stdout.flush()
        sys.stdout.write("\r" + " " * 70 + "\r")
        if total and done < total:
            raise IOError(f"只下到 {done}/{total} 字节")
        os.replace(tmp, dest)
        return True
    except Exception as exc:
        sys.stdout.write("\r" + " " * 70 + "\r")
        print(f"    下载失败: {exc}")
        for p in (tmp,):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        return False


def download_with_mirrors(url: str, dest: str, label: str,
                          mirror_first: bool = False) -> bool:
    """按镜像列表顺序尝试下载。"""
    order = list(MIRRORS)
    if mirror_first:
        order = order[1:] + [""]
    for prefix in order:
        full = prefix + url
        tag = prefix or "直连"
        print(f"  尝试 {tag} ...")
        if stream_download(full, dest, label):
            return True
    return False


def _pkg_ok(pkg: str, is_zip: bool) -> bool:
    """判断本地缓存的安装包是否还能用。

    之前踩过的坑：中断的下载会留下 0 字节文件，只看 os.path.exists 会误判成
    「已下载」，解压时才报 File is not a zip file。
    """
    if not os.path.exists(pkg) or os.path.getsize(pkg) < 1024:
        return False
    if not is_zip:
        return True
    try:
        with zipfile.ZipFile(pkg) as z:
            return z.testzip() is None and bool(z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def deploy(key: str, force: bool = False, mirror_first: bool = False) -> bool:
    """下载并解压一个工具。"""
    label, url, subdir, exe = TOOLS[key]
    dest_dir = os.path.join(HERE, subdir)
    print(f"\n=== {key}: {label} ===")

    if not url:
        print("  该工具需要手工放置（本项目不从网络获取）。")
        return os.path.exists(os.path.join(dest_dir, exe))

    if os.path.exists(os.path.join(dest_dir, exe)) and not force:
        print(f"  已存在，跳过：{subdir}/{exe}")
        return True

    os.makedirs(DL_DIR, exist_ok=True)
    os.makedirs(dest_dir, exist_ok=True)
    is_zip = url.lower().endswith(".zip")
    pkg = os.path.join(DL_DIR, os.path.basename(url))

    if force or not _pkg_ok(pkg, is_zip):
        if not download_with_mirrors(url, pkg, key, mirror_first):
            print(f"  !! {key} 下载失败")
            return False

    if is_zip:
        print(f"  解压 -> tools/{subdir}/")
        with zipfile.ZipFile(pkg) as z:
            _extract_flat(z, dest_dir)
    else:
        shutil.copy2(pkg, os.path.join(dest_dir, os.path.basename(url)))

    ok = os.path.exists(os.path.join(dest_dir, exe))
    print(f"  {'OK' if ok else '未找到 ' + exe}")
    return ok


def _extract_flat(zf: zipfile.ZipFile, dest: str) -> None:
    """解压并去掉顶层多余目录（很多 release 包多套一层文件夹）。"""
    names = zf.namelist()
    tops = {n.split("/")[0] for n in names if "/" in n}
    strip = ""
    if len(tops) == 1 and not any(n.count("/") == 0 and n for n in names if not n.endswith("/")):
        strip = next(iter(tops)) + "/"
    for info in zf.infolist():
        name = info.filename
        if strip and name.startswith(strip):
            name = name[len(strip):]
        if not name or name.endswith("/"):
            continue
        target = os.path.join(dest, name.replace("/", os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def main() -> int:
    ap = argparse.ArgumentParser(description="部署外部 CLI 到 tools/")
    ap.add_argument("keys", nargs="*", help="只处理指定的工具（默认全部）")
    ap.add_argument("--force", action="store_true", help="强制重新下载")
    ap.add_argument("--mirror", action="store_true", help="优先走国内镜像")
    args = ap.parse_args()

    keys = args.keys or list(TOOLS.keys())
    bad = [k for k in keys if k not in TOOLS]
    if bad:
        print(f"未知工具: {', '.join(bad)}")
        print(f"可选: {', '.join(TOOLS)}")
        return 2

    results = {}
    for key in keys:
        try:
            results[key] = deploy(key, args.force, args.mirror)
        except Exception as exc:  # 单个工具失败不影响别的
            print(f"  !! {key} 异常: {exc}")
            results[key] = False

    print("\n=== 结果 ===")
    for key, ok in results.items():
        print(f"  [{'OK' if ok else '!!'}]  {key}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
