# -*- coding: utf-8 -*-
"""在线更新（Self-Updater）。

用户的需求是「全自动在线更新」：CLI 查一个远端 `version.json`，比版本、
下 `UpdatePack.zip`、校验 SHA256、写一个 `daemon.bat` 把自己换掉再重启。

为什么不能自己解压自己
----------------------
Windows 上正在运行的程序（exe 与它旁边被加载的 dll）是**锁住**的，解压覆盖
必然失败。所以「替换」这一步必须交给一个独立进程：

    main.py --update  →  下载 → 校验 → 写 daemon.bat → 拉起它 → 自己退出
    daemon.bat        →  等 2 秒 → tar 解压覆盖 → 删包 → 启动新 exe → 删自己

本模块只负责「查、比、下、校验、写 bat、拉起、清残留」这几件事，
解压逻辑在 bat 里（用 Windows 自带的 tar，不引入依赖）。

版本比较规则（用户指定）
------------------------
**剥离 E / Rc / S / WIP 后缀，只比主版本号**（`0.18E` 与 `0.18R` 视为同一版）。
后缀只用来判断「是不是公测版」，以及在 `verid=` 明确点名时做同线升级。

远端 `version.json`::

    {
      "latest_stable": "0.17R",
      "latest_beta": "0.18E",
      "download_url": "https://.../UpdatePack.zip",
      "sha256": "abcd...",
      "changelog": "修复了..."
    }

断网、超时（3 秒）、JSON 坏掉都**静默失败**：返回 None，绝不抛异常，
调用方最多打一行 `[warn] 检查更新超时`。
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from me import config

VERSION_JSON = "version.json"
PACK_NAME = "UpdatePack.zip"
DAEMON_NAME = "daemon.bat"
STATE_NAME = "update.state"

# 默认远端：仓库根目录的 version.json（GitHub raw）
DEFAULT_URL = ("https://raw.githubusercontent.com/ShatteredDetermination/"
               "True-MultiExtractor/main/version.json")
ENV_URL = "ME_UPDATE_URL"
ENV_TIMEOUT = "ME_UPDATE_TIMEOUT"
ENV_DIR = "ME_UPDATE_DIR"        # 覆盖「程序根」，自测用

TIMEOUT = 3.0                    # 秒。用户明确要求 3 秒。

PACK_INFO = "update_pack.txt"    # 包里可以带一份清单（可选，仅供人看）

_VER_RE = re.compile(r"^(\d+)\.(\d+)(R|Rc|E|WIP|S)?(?:\s+Beta\s+(\d{5}))?$")
_VER_LOOSE = re.compile(r"(\d+)\.(\d+)(R|Rc|E|WIP|S)?", re.IGNORECASE)

# 同一条版本线上，后缀的「新旧」排序（显式点名 verid= 时才用得上）
TAG_RANK = {"S": 0, "WIP": 1, "E": 2, "Rc": 3, "R": 4, "": 4}


# ---------------------------------------------------------------- 路径
def root() -> Path:
    """程序根：daemon.bat / UpdatePack.zip 都落在这里。"""
    raw = os.environ.get(ENV_DIR)
    if raw:
        return Path(raw)
    return Path(config.ROOT)


def pack_path(base: Optional[Path] = None) -> Path:
    return Path(base) / PACK_NAME if base else root() / PACK_NAME


def daemon_path(base: Optional[Path] = None) -> Path:
    return Path(base) / DAEMON_NAME if base else root() / DAEMON_NAME


def state_path(base: Optional[Path] = None) -> Path:
    return Path(base) / STATE_NAME if base else root() / STATE_NAME


def local_version() -> str:
    return config.VERSION


# ---------------------------------------------------------------- 版本
def parse_version(text: str) -> Optional[Tuple[int, int, str, str]]:
    """`0.18E Beta 00096` -> (0, 18, "E", "00096")。认不出的返回 None。"""
    if not text:
        return None
    raw = str(text).strip().strip('"').strip("'")
    m = _VER_RE.match(raw)
    if m:
        major, minor, tag, build = m.groups()
        return int(major), int(minor), (tag or ""), (build or "")
    m = _VER_LOOSE.search(raw)          # 宽松：允许 "v0.18E"、"0.18 E" 这类写法
    if m:
        major, minor, tag = m.groups()
        return int(major), int(minor), (tag or "").upper(), ""
    return None


def main_version(text: str) -> Optional[Tuple[int, int]]:
    """只取主版本号（用户指定：比较时剥离后缀）。"""
    v = parse_version(text)
    return (v[0], v[1]) if v else None


def tag_of(text: str) -> str:
    v = parse_version(text)
    return v[2] if v else ""


def is_beta(text: str) -> bool:
    """是不是公测/实验版（E / Rc / WIP）。"""
    return tag_of(text) in ("E", "Rc", "WIP")


def is_stable(text: str) -> bool:
    return tag_of(text) in ("R", "")


def kind_of(text: str) -> str:
    return "beta" if is_beta(text) else ("stable" if is_stable(text) else "")


def compare(a: str, b: str) -> int:
    """按主版本号比较：a<b -> -1，a==b -> 0，a>b -> 1。认不出按 0 处理。"""
    va, vb = main_version(a), main_version(b)
    if va is None or vb is None:
        return 0
    return (va > vb) - (va < vb)


def same_line_better(local: str, remote: str) -> bool:
    """同一条版本线上，远端后缀更「正式」（0.18E -> 0.18Rc -> 0.18R）。"""
    if compare(local, remote) != 0:
        return False
    return TAG_RANK.get(tag_of(remote), 0) > TAG_RANK.get(tag_of(local), 0)


# ---------------------------------------------------------------- 远端清单
@dataclass
class Info:
    latest_stable: str = ""
    latest_beta: str = ""
    download_url: str = ""
    sha256: str = ""
    changelog: str = ""
    raw: Dict[str, str] = field(default_factory=dict)

    def versions(self) -> List[str]:
        return [v for v in (self.latest_beta, self.latest_stable) if v]


def parse(text: str) -> Optional[Info]:
    """解析 version.json。坏掉/没版本号一律 None，不抛异常。"""
    if not text or not text.strip():
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    info = Info(
        latest_stable=str(data.get("latest_stable") or "").strip(),
        latest_beta=str(data.get("latest_beta") or "").strip(),
        download_url=str(data.get("download_url") or "").strip(),
        sha256=str(data.get("sha256") or "").strip().lower(),
        changelog=str(data.get("changelog") or "").strip(),
        raw={k: str(v) for k, v in data.items() if isinstance(v, (str, int, float))},
    )
    if not info.versions():
        return None
    return info


def remote_url() -> str:
    return (os.environ.get(ENV_URL) or DEFAULT_URL).strip()


def timeout_s() -> float:
    raw = os.environ.get(ENV_TIMEOUT)
    if raw:
        try:
            return max(0.2, min(30.0, float(raw)))
        except ValueError:
            pass
    return TIMEOUT


def fetch(url: Optional[str] = None,
          timeout: Optional[float] = None) -> Optional[Info]:
    """同步拉 version.json。任何失败都返回 None（静默）。"""
    if os.environ.get("ME_NO_NET") == "1":
        return None
    u = url if url is not None else remote_url()
    t = timeout if timeout is not None else timeout_s()
    if not u:
        return None
    req = urllib.request.Request(u, headers={"User-Agent": "MultiExtractor"})
    try:
        with urllib.request.urlopen(req, timeout=t) as resp:
            status = getattr(resp, "status", None)
            if status is not None and status != 200:
                return None
            data = resp.read(256 * 1024)
        return parse(data.decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None
    except Exception:                    # noqa: BLE001 网络栈的意外也吞掉
        return None


def pick(info: Info, want: str = "") -> Tuple[str, str]:
    """决定拿哪个版本。返回 (版本串, 说明)。

    want: "" 或 "latest" → 自动（有公测版优先，见 choose）
          "beta" / "stable" → 指定通道
          "0.18E" 这类具体版本 → 必须能在清单里对上
    """
    want = (want or "").strip()
    if want.startswith("verid="):
        want = want[len("verid="):].strip()

    if not want or want.lower() in ("latest", "auto", ""):
        return choose(info)

    if want.lower() in ("beta", "test", "e"):
        if not info.latest_beta:
            return "", "远端没有公测版"
        return info.latest_beta, "按通道选择公测版"

    if want.lower() in ("stable", "r", "release"):
        if not info.latest_stable:
            return "", "远端没有稳定版"
        return info.latest_stable, "按通道选择稳定版"

    if compare(want, "") == 0 and main_version(want) is None:
        return "", f"看不懂的版本号: {want}"
    for cand in info.versions():
        if compare(cand, want) == 0 and (tag_of(cand) == tag_of(want)
                                        or not tag_of(want)):
            return cand, "按指定版本"
    return "", f"远端清单里没有 {want}（有 {', '.join(info.versions())}）"


def choose(info: Info) -> Tuple[str, str]:
    """自动选择：公测版优先（用户要的就是「有公测版可用」）。"""
    local = local_version()
    stable, beta = info.latest_stable, info.latest_beta

    # 本机是稳定版、远端有更高的公测版 -> 提示公测版
    if stable and beta and is_stable(local) and compare(beta, local) > 0:
        return beta, "远端有公测版可用"
    if beta and compare(beta, local) > 0:
        return beta, "远端公测版更高"
    if stable and compare(stable, local) > 0:
        return stable, "远端稳定版更高"
    # 同一条版本线只换后缀（0.18E -> 0.18R）**不自动提示**：
    # 用户定的规则是「只比主版本号」，按这条走才不会每次启动都催人更新。
    # 想在同一条线上换后缀，用 --update verid=0.18R 明确点名。
    return "", "已是最新"


def _explicit(want: str) -> bool:
    """want 是不是「明确点名某个版本」（而不是 latest / beta / stable）。"""
    w = (want or "").strip().lower()
    if w.startswith("verid="):
        w = w[len("verid="):].strip()
    return bool(w) and w not in ("latest", "auto", "beta", "test", "e",
                                 "stable", "r", "release")


def plan(info: Info, want: str = "") -> Dict[str, object]:
    """给 UI/事件流用的一步到位结论。"""
    ver, why = pick(info, want)
    local = local_version()
    ahead = bool(ver) and (
        compare(ver, local) > 0
        or (_explicit(want) and same_line_better(local, ver)))
    return {
        "local": local,
        "remote": ver,
        "reason": why,
        "available": ahead,
        "kind": kind_of(ver) if ver else "",
        "beta_available": bool(info.latest_beta) and is_stable(local)
                          and compare(info.latest_beta, local) > 0,
        "url": info.download_url,
        "sha256": info.sha256,
        "changelog": info.changelog,
    }


# ---------------------------------------------------------------- 下载与校验
def sha256_file(path: Path, chunk: int = 1 << 18) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def download(url: str, dest: Path, expect_sha: str = "",
             progress: Optional[Callable[[int, int, str], None]] = None,
             timeout: float = 60.0) -> Tuple[bool, str, int]:
    """下载更新包并校验 SHA256。返回 (成功, 错误, 字节数)。

    校验不一致时**删掉文件**再返回失败 —— 半截/被篡改的包绝不能留在盘上
    等下一次启动被当成「已下载的更新」。
    """
    if not url:
        return False, "清单里没有 download_url", 0
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    done = 0
    last = 0.0
    import time as _time
    req = urllib.request.Request(url, headers={"User-Agent": "MultiExtractor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            with open(tmp, "wb") as fh:
                while True:
                    block = resp.read(1 << 18)
                    if not block:
                        break
                    fh.write(block)
                    done += len(block)
                    now = _time.time()
                    if progress and (now - last >= 0.4 or (total and done >= total)):
                        last = now
                        progress(done, total, dest.name)
        if done <= 0:
            raise IOError("一个字节都没下到")
        os.replace(tmp, dest)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            IOError, ValueError) as exc:
        _unlink(tmp)
        return False, f"{type(exc).__name__}: {exc}", 0
    except Exception as exc:                # noqa: BLE001
        _unlink(tmp)
        return False, f"{type(exc).__name__}: {exc}", 0

    got = sha256_file(dest)
    want = (expect_sha or "").strip().lower()
    if want and got != want:
        _unlink(dest)
        return False, f"SHA256 不一致（期望 {want[:16]}…，实得 {got[:16]}…），已删除", done
    return True, "", done


def _unlink(p: Path) -> None:
    try:
        if Path(p).exists():
            Path(p).unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- 守护进程
def exe_name() -> str:
    """要重启的可执行文件名：打包版是自己的 exe，源码版是 python + main.py。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).name
    return ""


def write_daemon(base: Optional[Path] = None, exe: str = "",
                 pack: str = PACK_NAME) -> Path:
    """生成 daemon.bat。返回路径。

    要点（都是踩过坑的）：
      * 先等 2 秒再动手 —— 主进程退出、文件句柄才真的放开；
        用 `ping -n 3` 而不是 `timeout /t 2`：detached 拉起时没有控制台，
        `timeout` 会直接失败、等于没等；
      * `cd /d "%~dp0"` —— 守护进程可能被从别的目录拉起；
      * `tar -xf ... -C "%~dp0."` —— 用 Windows 自带的 bsdtar，解 zip 也行；
        必须带那个点：`%~dp0` 自带结尾反斜杠，`"...\\"` 会把引号转义；
      * 解压失败**不要**删包，留着让用户手动解；
      * 只有成功才 `start` 新程序，避免「更新失败还照样启动」；
      * 自删除用 `(goto) 2>nul & del "%~f0"`，别用嵌套引号的 start /b 写法。
    """
    base = Path(base) if base else root()
    base.mkdir(parents=True, exist_ok=True)
    bat = base / DAEMON_NAME
    exe = exe or exe_name()

    if exe:
        launch = f'start "" "{exe}"'
    else:
        # 源码版：用同一个解释器把 main.py 重新拉起来
        py = Path(sys.executable)
        launch = f'start "" "{py}" "{base / "main.py"}"'

    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "title MultiExtractor Updater",
        "rem ---- 1) 等主进程完全退出，释放 exe / dll 的文件锁 ----",
        "rem     用 ping 而不是 timeout：守护进程是被 detached 拉起的，没有控制台，",
        "rem     timeout 会直接报 Input redirection is not supported 然后不等待。",
        "ping -n 3 127.0.0.1 >nul 2>nul",
        'cd /d "%~dp0"',
        "rem ---- 2) 解压覆盖（Windows 自带 tar，支持 zip）----",
        f'if not exist "{pack}" (',
        f'    echo [x] 找不到 {pack}，更新取消',
        "    pause",
        "    exit /b 1",
        ")",
        # 注意 "%~dp0." —— %~dp0 结尾自带反斜杠，写成 "...\\" 会把引号转义掉，
        # tar 收到的路径就带着一个引号，报 could not chdir。加个点最稳。
        f'tar -xf "{pack}" -C "%~dp0."',
        "if errorlevel 1 (",
        f'    echo [x] 更新解压失败，请手动解压 {pack}',
        "    pause",
        "    exit /b 1",
        ")",
        "rem ---- 3) 清理下载包 ----",
        f'del /f /q "{pack}" >nul 2>nul',
        "rem ---- 4) 启动新版本 ----",
        launch,
        "rem ---- 5) 自删除（先读完整行再删自己）----",
        '(goto) 2>nul & del /f /q "%~f0" & exit /b 0',
        "",
    ]
    # 用 ASCII 写：cmd 的 chcp 65001 只在跑起来之后生效，脚本本身是 UTF-8
    # 无 BOM 时中文可能被误读；这些提示语保持英文更稳，但用户看的是程序里的中文。
    bat.write_text("\r\n".join(lines), encoding="utf-8", newline="")
    return bat


def launch_daemon(bat: Optional[Path] = None, base: Optional[Path] = None) -> bool:
    """静默拉起守护进程（不弹窗、不等它）。"""
    bat = Path(bat) if bat else daemon_path(base)
    if not bat.exists():
        return False
    comspec = os.environ.get("COMSPEC") or "cmd.exe"
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | \
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen([comspec, "/c", str(bat)],
                         cwd=str(bat.parent), creationflags=flags,
                         close_fds=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- 启动自检
def sanitize(base: Optional[Path] = None) -> bool:
    """删掉上一次更新留下的 daemon.bat。返回是否删了东西。

    有这个文件说明「上次更新跑完了但守护进程没删掉自己」（或者用户中途取消）。
    启动第一秒就清掉，免得用户看到莫名其妙的一堆 .bat。
    """
    p = daemon_path(base)
    if not p.exists():
        return False
    _unlink(p)
    return not p.exists()


def pending(base: Optional[Path] = None) -> Optional[Path]:
    """有没有已经下好的更新包（上次下载完但没装）。"""
    p = pack_path(base)
    try:
        return p if p.is_file() and p.stat().st_size > 0 else None
    except OSError:
        return None


def write_state(base: Optional[Path] = None, **fields) -> None:
    """把「我们下了什么」记一份，供下次启动自检时显示。坏掉也没关系。"""
    try:
        p = state_path(base)
        p.write_text(json.dumps(fields, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except OSError:
        pass


def read_state(base: Optional[Path] = None) -> Dict[str, str]:
    try:
        p = state_path(base)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def clear_state(base: Optional[Path] = None) -> None:
    _unlink(state_path(base))
