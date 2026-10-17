# -*- coding: utf-8 -*-
"""外部工具的**自动下载**（V0.18E 起）。

用户的话很直白：「要求他会自己下 —— 不是给你个链接让你下，是他会自己瞎（下）。」
所以这一层不做「打开浏览器 / 打印地址」那种事，而是真的把文件取回来：

    tools.ini 里每个工具带 url 与 paths
        url 是 zip  -> 解压到 <工具根>/<子目录>/（自动去掉多余顶层目录）
        url 是单文件 -> 直接放到 <工具根>/<子目录>/<文件名>
    下完按 paths 里第一条候选路径验一遍，验不过就当失败

没有 url 的工具（例如 AssetStudio，上游只发 .7z）走**全量包**：下载
`[meta] full_package` 那个 zip，只从里面挑 `tools/<子目录>/` 出来。
两条路都是程序自己干，用户只需要按一次键。

三道防线
--------
下载是最容易把程序卡死或写脏的地方，所以：

1. 先写 `.part`，成功才原子替换 —— 中断不会留下半截「看起来已下好」的文件；
2. 包体校验（zip 能否打开、单文件够不够大），坏包不算下完；
3. 解压时去掉多余顶层目录（release 包常多套一层）。

失败一律返回结果对象，不抛异常 —— 下载失败是常态，不是崩溃。
"""

import os
import shutil
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from me.tools import manifest as mf
from me.tools import paths

# GitHub 直连常被墙，按顺序试。可用 ME_TOOLS_MIRROR 覆盖（逗号分隔，空串=直连）。
MIRRORS = (
    "",
    "https://ghfast.top/",
    "https://ghproxy.net/",
    "https://gh-proxy.com/",
)
MIRROR_ENV = "ME_TOOLS_MIRROR"

CONNECT_TIMEOUT = 60.0        # 秒。下载和「拉清单」不是一回事，这里可以宽松
CHUNK = 1 << 18
MIN_FILE = 1024               # 小于这个字节的单文件一律当成下坏了

_LOG = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


@dataclass
class Result:
    """一个工具的下载结果。"""

    key: str
    ok: bool = False
    how: str = ""             # skip / zip / file / package / manual / fail
    path: Optional[Path] = None
    size: int = 0
    error: str = ""

    def line(self) -> str:
        name = f"{self.key:<12}"
        if self.ok and self.how == "skip":
            return f"{name} 已就位，跳过"
        if self.ok:
            mb = self.size / 1024 / 1024
            extra = f"（{mb:.1f} MB）" if mb >= 0.1 else ""
            return f"{name} 下载完成 {extra}{self.path or ''}".rstrip()
        if self.how == "manual":
            return f"{name} 需要手工放置：{self.error}"
        return f"{name} 失败：{self.error}"


# ---------------------------------------------------------------- 小工具
def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def mirrors() -> Tuple[str, ...]:
    raw = os.environ.get(MIRROR_ENV)
    if raw is None:
        return MIRRORS
    parts = [p.strip() for p in raw.split(",")]
    return tuple(p for p in parts if p != "") or ("",)


def download_dir() -> Path:
    """安装包缓存目录（%LOCALAPPDATA%\\MultiExtractor\\download）。

    放用户目录而不是 tools/ 里：这些是中间产物，一百多 MB，
    下次重装同一个工具时能直接复用，不必再下一次。
    """
    raw = os.environ.get("ME_DOWNLOAD_DIR")
    if raw:
        return Path(raw)
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "MultiExtractor" / "download"


def _cached_path(url: str) -> Path:
    return download_dir() / Path(url.split("?")[0]).name


def _fetch_package(url: str, log: _LOG,
                   tick: Optional[Callable[[int, int, str], None]] = None,
                   force: bool = False) -> Tuple[Optional[Path], int, str]:
    """取一个安装包：缓存能用就直接用，否则下载。返回 (路径, 字节数, 错误)。"""
    pkg = _cached_path(url)
    if not force and pkg.is_file():
        if url.lower().endswith(".zip"):
            good, _why = _zip_ok(pkg)
        else:
            good = pkg.stat().st_size >= MIN_FILE
        if good:
            log(f"  用缓存的安装包 {pkg.name}")
            return pkg, pkg.stat().st_size, ""
    ok, size, err = _try_download(url, pkg, log, tick)
    if not ok:
        return None, 0, err
    return pkg, size, ""


def tools_root() -> Path:
    """工具根目录：用户指定过就用它，否则 <程序根>/tools。

    读的是 ``paths`` 模块里的实时值，不是 import 时抓到的常量 ——
    这样自测能把工具根换到临时目录，也不会和 paths.find() 的口径不一致。
    """
    od = paths.override_dir()
    if od is not None:
        return od
    return Path(paths.TOOLS_DIR)


def _split_rel(spec: mf.ToolSpec) -> Tuple[str, str]:
    """`utmt/UndertaleModCli.exe` -> ("utmt", "UndertaleModCli.exe")。"""
    prim = spec.primary.replace("\\", "/")
    parts = [p for p in prim.split("/") if p]
    if len(parts) <= 1:
        return parts[0] if parts else spec.key, ""
    return "/".join(parts[:-1]), parts[-1]


def missing_keys(root: Optional[Path] = None) -> List[str]:
    """还缺哪些工具（按清单顺序）。"""
    base = Path(root) if root else tools_root()
    out = []
    for key, spec in paths.specs().items():
        found = any((base / rel.replace("\\", "/")).is_file()
                    for rel in spec.paths)
        if not found:
            out.append(key)
    return out


# ---------------------------------------------------------------- 下载
def _try_download(url: str, dest: Path, log: _LOG,
                  tick: Optional[Callable[[int, int, str], None]] = None
                  ) -> Tuple[bool, int, str]:
    """按镜像顺序下载一个 URL 到 dest。返回 (成功, 字节数, 错误)。"""
    # 镜像只对 http(s) 有意义：file:// 是自测用的本地地址，加了前缀就废了
    order = mirrors() if url.lower().startswith(("http://", "https://")) else ("",)
    last = ""
    for prefix in order:
        full = (prefix + url) if prefix else url
        tag = prefix or "直连"
        log(f"  从 {tag} 取 {url.rsplit('/', 1)[-1]}")
        ok, size, err = _download_once(full, dest, log, tick)
        if ok:
            return True, size, ""
        last = f"{tag}: {err}"
    return False, 0, last or "没有可用的下载地址"


def _download_once(url: str, dest: Path, log: _LOG,
                   tick: Optional[Callable[[int, int, str], None]] = None
                   ) -> Tuple[bool, int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "MultiExtractor"})
    tmp = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    last = 0.0
    try:
        with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            with open(tmp, "wb") as fh:
                while True:
                    block = resp.read(CHUNK)
                    if not block:
                        break
                    fh.write(block)
                    done += len(block)
                    # 进度节流：GUI 的进度条与事件流都不该被字节数刷屏
                    now = time.time()
                    if tick is not None and (now - last >= 0.4 or
                                             (total and done >= total)):
                        last = now
                        tick(done, total, dest.name)
                    if done > 4 * 1024 ** 3:          # 4GB 上限，防呆
                        raise IOError("包体超过 4GB，拒绝")
        if total and done < total:
            raise IOError(f"只下到 {done}/{total} 字节")
        if done <= 0:
            raise IOError("一个字节都没下到")
        os.replace(tmp, dest)
        return True, done, ""
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            IOError, ValueError) as exc:
        _unlink(tmp)
        return False, 0, f"{type(exc).__name__}: {exc}"
    except Exception as exc:                    # noqa: BLE001 网络栈的意外也吞掉
        _unlink(tmp)
        return False, 0, f"{type(exc).__name__}: {exc}"


def _unlink(p: Path) -> None:
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass


def _zip_ok(pkg: Path) -> Tuple[bool, str]:
    """zip 完整性：能打开、有条目、CRC 表读得动。"""
    try:
        with zipfile.ZipFile(pkg) as z:
            if not z.namelist():
                return False, "压缩包是空的"
            bad = z.testzip()
            if bad is not None:
                return False, f"压缩包损坏（{bad}）"
        return True, ""
    except (zipfile.BadZipFile, OSError) as exc:
        return False, f"不是有效的 zip：{exc}"


def extract_zip(pkg: Path, dest: Path, only: str = "") -> Tuple[int, str]:
    """解压 pkg 到 dest，去掉多余的顶层目录。

    ``only`` 是「只要包里这一层」的标记，例如 ``tools/utmt``：
    从全量包里挑工具时用，避免把 CLI 和壳一起解到 tools/ 下面。
    """
    try:
        with zipfile.ZipFile(pkg) as zf:
            infos = zf.infolist()
            names = [i.filename for i in infos]
            strip = _common_prefix(names)
            n = 0
            marker = only.replace("\\", "/").strip("/") + "/" if only else ""
            for info in infos:
                name = info.filename.replace("\\", "/")
                if strip and name.startswith(strip):
                    name = name[len(strip):]
                if not name or name.endswith("/"):
                    continue
                if marker:
                    # 按**路径段**匹配，别用字符串下标：
                    # 之前用 "/"+name 再 find，取子串时差了 1 个字符，
                    # AssetStudio.CLI.exe 被解成 ssetStudio.CLI.exe。
                    parts = name.split("/")
                    want = [seg for seg in marker.strip("/").split("/") if seg]
                    hit = -1
                    for i in range(len(parts) - len(want) + 1):
                        if parts[i:i + len(want)] == want:
                            hit = i + len(want)
                            break
                    if hit < 0 or hit >= len(parts):
                        continue
                    name = "/".join(parts[hit:])
                    if not name:
                        continue
                target = dest / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                n += 1
        if n == 0:
            return 0, "压缩包里没找到可用的文件"
        return n, ""
    except (zipfile.BadZipFile, OSError) as exc:
        return 0, f"解压失败：{type(exc).__name__}: {exc}"


def _common_prefix(names: List[str]) -> str:
    """所有条目共有的第一层目录（release 包常多套一层）。"""
    tops = {n.split("/")[0] for n in names if "/" in n}
    if len(tops) != 1:
        return ""
    top = next(iter(tops))
    # 只有当所有条目都在这一层里（没有根上的散文件）才剥掉
    if any("" == n.split("/")[0] for n in names if "/" in n):
        return ""
    if not all(n.startswith(top + "/") for n in names):
        return ""
    return top + "/"


# ---------------------------------------------------------------- 装一个
def install_one(key: str, root: Optional[Path] = None, force: bool = False,
                log: _LOG = _noop,
                tick: Optional[Callable[[int, int, str], None]] = None
                ) -> Result:
    """下载并部署一个工具。已就位则跳过（force=True 重下）。"""
    spec = paths.specs().get(key)
    if spec is None:
        return Result(key, how="fail", error="清单里没有这个工具")

    base = Path(root) if root else tools_root()
    subdir, exe = _split_rel(spec)
    dest_dir = base / subdir if subdir else base

    if not force:
        for rel in spec.paths:
            if (base / rel.replace("\\", "/")).is_file():
                return Result(key, ok=True, how="skip",
                              path=base / rel.replace("\\", "/"))

    url = (spec.url or "").strip()
    if not url:
        return Result(key, how="manual",
                      error="tools.ini 没给下载地址（上游只发 .7z 之类），"
                            "用全量包或手工放置")

    log(f"{key}: {spec.name}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    pkg, size, err = _fetch_package(url, log, tick, force)
    if pkg is None:
        return Result(key, how="fail", error=err)

    if url.lower().endswith(".zip"):
        good, why = _zip_ok(pkg)
        if not good:
            _unlink(pkg)
            return Result(key, how="fail", error=why)
        n, err2 = extract_zip(pkg, dest_dir)
        if err2:
            return Result(key, how="fail", error=err2)
        how = "zip"
        log(f"  解压 {n} 个文件 -> {dest_dir}")
    else:
        if pkg.stat().st_size < MIN_FILE:
            _unlink(pkg)
            return Result(key, how="fail",
                          error=f"下载的文件只有 {pkg.stat().st_size} 字节，太小")
        target = dest_dir / (exe or Path(url).name)
        shutil.copy2(pkg, target)
        how = "file"
        log(f"  放到 {target}")

    hit = None
    for rel in spec.paths:
        p = base / rel.replace("\\", "/")
        if p.is_file():
            hit = p
            break
    if hit is None:
        return Result(key, how="fail", size=size,
                      error="下完了解压出来却没有 " + (exe or spec.primary))
    return Result(key, ok=True, how=how, path=hit, size=size)


# ---------------------------------------------------------------- 全量包
def package_url() -> str:
    """全量包地址（必须是能直接下的 zip）。"""
    man = mf.snapshot()
    return (man.full_package or mf.BUILTIN.full_package or "").strip()


def install_from_package(keys: List[str], root: Optional[Path] = None,
                         log: _LOG = _noop,
                         tick: Optional[Callable[[int, int, str], None]] = None
                         ) -> List[Result]:
    """下载一次全量包，只从里面挑出这些工具的目录。"""
    base = Path(root) if root else tools_root()
    url = package_url()
    if not url.lower().endswith(".zip"):
        return [Result(k, how="manual",
                       error=f"全量包地址不是 zip（{url or '空'}），没法自动取") 
                for k in keys]

    log(f"全量包：{url.rsplit('/', 1)[-1]}")
    pkg, size, err = _fetch_package(url, log, tick)
    if pkg is None:
        return [Result(k, how="fail", error=err) for k in keys]

    out: List[Result] = []
    good, why = _zip_ok(pkg)
    if not good:
        _unlink(pkg)
        return [Result(k, how="fail", error=why) for k in keys]

    if True:
        for key in keys:
            spec = paths.specs().get(key)
            if spec is None:
                out.append(Result(key, how="fail", error="清单里没有这个工具"))
                continue
            subdir, exe = _split_rel(spec)
            dest = base / subdir if subdir else base
            dest.mkdir(parents=True, exist_ok=True)
            n, err2 = extract_zip(pkg, dest, only=f"tools/{subdir}")
            if err2 and subdir:
                # 有些包把工具直接铺在 tools/ 下（没有 per-tool 子目录）
                n, err2 = extract_zip(pkg, dest, only="tools")
            if err2:
                out.append(Result(key, how="fail", size=size, error=err2))
                continue
            hit = None
            for rel in spec.paths:
                p = base / rel.replace("\\", "/")
                if p.is_file():
                    hit = p
                    break
            if hit is None:
                out.append(Result(key, how="fail", size=size,
                                  error="全量包里没有 " + (exe or spec.primary)))
            else:
                log(f"  {key}: 从全量包取出 {n} 个文件")
                out.append(Result(key, ok=True, how="package", path=hit,
                                  size=size))
    return out


# ---------------------------------------------------------------- 装一批
def install(keys: Optional[List[str]] = None, root: Optional[Path] = None,
            force: bool = False, log: _LOG = _noop,
            tick: Optional[Callable[[int, int, str], None]] = None,
            allow_package: bool = True):
    """下载所有缺的工具。返回 (results, used_package)。

    先走各自的直链；没有直链的（AssetStudio 只发 .7z）再用一次全量包兜底 ——
    全量包只下**一次**，而且会留在缓存里，重装不再重下。

    ``allow_package=False`` 时绝不下全量包（一百多 MB，必须由调用方明确同意）。
    """
    if keys is None:
        keys = missing_keys(root)
    results: List[Result] = []
    need_pkg: List[str] = []

    for key in keys:
        res = install_one(key, root, force, log, tick)
        results.append(res)
        if not res.ok and allow_package:
            spec = paths.specs().get(key)
            if spec is not None and not spec.url:
                need_pkg.append(key)

    used_package = False
    if need_pkg:
        log(f"以下工具没有直链，改用全量包：{', '.join(need_pkg)}")
        pkg_results = install_from_package(need_pkg, root, log, tick)
        used_package = True
        by_key: Dict[str, Result] = {r.key: r for r in pkg_results}
        results = [by_key.get(r.key, r) for r in results]
    return results, used_package
