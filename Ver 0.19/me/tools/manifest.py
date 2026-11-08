# -*- coding: utf-8 -*-
"""外部工具清单（tools.ini）：远端拉取 → 本地缓存 → 内置兜底。

为什么要有这一层
----------------
V0.18E 起发布包叫 Slim：**不带** `tools/` 里那 178 MB 外部 CLI。
那「有哪些工具、去哪下、缺了怎么办」就必须变成数据，而不是写死在代码里。

三级来源，任何一级失败都不影响程序启动：

    远端 tools.ini      启动时后台线程去拉，3 秒超时，绝不阻塞主流程
    本地缓存            上次成功拉到的原文，存在 %LOCALAPPDATA%\\MultiExtractor\\
    内置兜底            代码里这份 BUILTIN，保证断网也有清单可显示

术语
----
* Manifest —— 一份工具清单（含下载地址与相对路径候选）。
* ToolSpec —— 单个工具：键名、显示名、候选路径、独立下载地址。

tools.ini 格式（远端与缓存同一套）::

    [meta]
    version = 1
    updated = 2026-10-18
    full_package = https://example.com/MultiExtractor-full.zip

    [utmt]
    name = UndertaleModTool CLI
    kind = cli
    note = GameMaker data.win
    paths = utmt/UndertaleModCli.exe, utmt/UndertaleModTool.exe
    url = https://example.com/utmt.zip

约束
----
这个模块**只做网络与解析**，不碰引擎、不 import me.engines ——
自测可以在无网、无目录的情况下把三级来源全跑一遍。
"""

import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 远端清单地址。可用环境变量 ME_TOOLS_URL 覆盖（部署到别的托管时用）。
DEFAULT_URL = ("https://raw.githubusercontent.com/ShatteredDetermination/"
               "True-MultiExtractor/main/tools.ini")
ENV_URL = "ME_TOOLS_URL"
TIMEOUT = 3.0           # 秒。用户明确要求 3 秒。
TIMEOUT_ENV = "ME_TOOLS_TIMEOUT"

CACHE_DIR_NAME = "MultiExtractor"
CACHE_FILE = "tools.ini"


@dataclass(frozen=True)
class ToolSpec:
    """一个外部工具。"""

    key: str
    name: str
    kind: str = "cli"           # cli / gui / python
    note: str = ""
    paths: Tuple[str, ...] = ()  # 相对 tools/ 的候选路径
    url: str = ""                # 单独下载地址（可选）
    size_mb: float = 0.0

    @property
    def primary(self) -> str:
        """首选相对路径；没有就空串。"""
        return self.paths[0] if self.paths else ""


@dataclass
class Manifest:
    """一份清单。source 说明它从哪来：remote / cache / builtin。"""

    source: str
    version: str = ""
    updated: str = ""
    full_package: str = ""
    tools: Dict[str, ToolSpec] = field(default_factory=dict)

    def keys(self) -> List[str]:
        return list(self.tools)


# ---------------------------------------------------------------- 内置兜底
# 与 V0.17 的 me/tools/paths.py 注册表保持一致；远端改了这里不用跟着改，
# 它只在「断网且无缓存」时用来保证界面不空。
BUILTIN = Manifest(
    source="builtin",
    version="1",
    updated="2026-10-18",
    # 必须是能直接下的 zip（自动下载要用它兜底）；远端 tools.ini 会覆盖这里。
    full_package="https://github.com/ShatteredDetermination/"
                 "True-MultiExtractor/releases/download/v0.17R/"
                 "MultiExtractor_0.17R.zip",
    tools={
        "utmt": ToolSpec(
            key="utmt", name="UndertaleModTool CLI", kind="cli",
            note="GameMaker data.win（第三方通道）",
            paths=("utmt/UndertaleModCli.exe", "utmt/UndertaleModTool.exe"),
            url="https://github.com/UnderminersTeam/UndertaleModTool/"
                "releases/download/0.9.2.0/UTMT_CLI_v0.9.2.0-Windows.zip",
            size_mb=25.0,
        ),
        "assetstudio": ToolSpec(
            key="assetstudio", name="AssetStudio CLI", kind="cli",
            note="Unity（第三方通道）；上游只发 .7z，标准库解不了，"
                 "只能从全量包里取或手工放置",
            paths=("assetstudio/AssetStudio.CLI.exe",),
        ),
        "evbunpack": ToolSpec(
            key="evbunpack", name="evbunpack", kind="cli",
            note="Enigma Virtual Box",
            paths=("evbunpack/evbunpack.exe",),
            url="https://github.com/mos9527/evbunpack/releases/download/"
                "0.2.6/evbunpack.exe",
            size_mb=12.0,
        ),
        "repak": ToolSpec(
            key="repak", name="repak", kind="cli",
            note="Unreal Engine .pak",
            paths=("repak/repak.exe",),
            url="https://github.com/trumank/repak/releases/download/"
                "v0.2.3/repak_cli-x86_64-pc-windows-msvc.zip",
            size_mb=3.0,
        ),
    },
)


# ---------------------------------------------------------------- 路径
def cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / CACHE_DIR_NAME


def cache_path() -> Path:
    return cache_dir() / CACHE_FILE


def remote_url() -> str:
    return (os.environ.get(ENV_URL) or DEFAULT_URL).strip()


def timeout_s() -> float:
    raw = os.environ.get(TIMEOUT_ENV)
    if raw:
        try:
            return max(0.2, min(30.0, float(raw)))
        except ValueError:
            pass
    return TIMEOUT


# ---------------------------------------------------------------- 解析
def _split_paths(raw: str) -> Tuple[str, ...]:
    parts = [p.strip().replace("\\", "/") for p in raw.split(",")]
    return tuple(p for p in parts if p)


def parse(text: str, source: str) -> Optional[Manifest]:
    """把 tools.ini 文本解析成 Manifest。解析不出任何工具就返回 None。

    不用 configparser：它遇到重复键 / 缺 section 会整段抛异常，
    而这份文件是**远端可控**的，必须当成不可信输入逐行容错。
    """
    if not text or not text.strip():
        return None

    m = Manifest(source=source)
    section = ""
    tools: Dict[str, dict] = {}
    order: List[str] = []

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if section and section.lower() != "meta":
                if section not in tools:
                    tools[section] = {}
                    order.append(section)
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().lower(), v.strip().strip('"').strip("'")
        if section.lower() == "meta":
            if k == "version":
                m.version = v
            elif k == "updated":
                m.updated = v
            elif k in ("full_package", "package", "download"):
                m.full_package = v
            continue
        if section:
            tools[section][k] = v

    for key in order:
        d = tools[key]
        paths = _split_paths(d.get("paths", "") or d.get("path", ""))
        if not paths:
            continue
        size = 0.0
        try:
            size = float(d.get("size_mb", "0") or 0)
        except ValueError:
            size = 0.0
        m.tools[key] = ToolSpec(
            key=key,
            name=d.get("name", key),
            kind=d.get("kind", "cli"),
            note=d.get("note", ""),
            paths=paths,
            url=d.get("url", ""),
            size_mb=size,
        )

    return m if m.tools else None


# ---------------------------------------------------------------- 网络
def fetch(url: str, timeout: float) -> Optional[str]:
    """GET 一份文本。**任何失败都返回 None**，不抛异常、不打印。"""
    if not url:
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "MultiExtractor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # file:// 的响应没有 status（是 None），按成功算 —— 自测就靠它
            # 把「远端」这条路径跑通，不至于真去连 GitHub。
            status = getattr(resp, "status", None)
            if status is not None and status != 200:
                return None
            data = resp.read(256 * 1024)      # 清单不该超过 256KB
        return data.decode("utf-8", "replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None
    except Exception:                          # noqa: BLE001 网络栈的意外也吞掉
        return None


def save_cache(text: str) -> bool:
    """原子写缓存。失败返回 False（只读目录 / 无权限都算正常情况）。"""
    try:
        p = cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
        return True
    except OSError:
        return False


def read_cache() -> Optional[str]:
    try:
        p = cache_path()
        if not p.exists():
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ---------------------------------------------------------------- 三级来源
def load_remote(url: Optional[str] = None,
                timeout: Optional[float] = None) -> Optional[Manifest]:
    """同步拉远端。失败返回 None。"""
    u = url if url is not None else remote_url()
    t = timeout if timeout is not None else timeout_s()
    text = fetch(u, t)
    if text is None:
        return None
    m = parse(text, "remote")
    if m is None:
        return None                    # 拉到了但内容不是清单：不覆盖缓存
    save_cache(text)
    return m


def load_cache() -> Optional[Manifest]:
    text = read_cache()
    if text is None:
        return None
    return parse(text, "cache")


def load_any(url: Optional[str] = None, timeout: Optional[float] = None,
             use_remote: bool = True) -> Manifest:
    """同步解析：远端 → 缓存 → 内置。永远返回一份可用的 Manifest。"""
    if use_remote:
        m = load_remote(url, timeout)
        if m is not None:
            return m
    m = load_cache()
    if m is not None:
        return m
    return BUILTIN


# ---------------------------------------------------------------- 后台预热
# 启动时开一个守护线程去拉清单；主流程永远不等它。
_lock = threading.Lock()
_warm: Optional[Manifest] = None
_warm_done = threading.Event()
_warm_started = False
_warm_error = ""


def start_warmup(url: Optional[str] = None, timeout: Optional[float] = None,
                 force: bool = False) -> None:
    """启动后台拉取。重复调用只生效一次（force=True 可再拉）。"""
    global _warm_started, _warm, _warm_error
    with _lock:
        if _warm_started and not force:
            return
        _warm_started = True
        _warm = None
        _warm_error = ""
        _warm_done.clear()

    target = url if url is not None else remote_url()
    wait = timeout if timeout is not None else timeout_s()

    def worker() -> None:
        global _warm, _warm_error
        t0 = time.time()
        try:
            m = load_remote(target, wait)
            with _lock:
                if m is not None:
                    _warm = m
                else:
                    _warm_error = "remote-unavailable"
        except Exception as exc:                # noqa: BLE001 线程里绝不能抛
            with _lock:
                _warm_error = f"{type(exc).__name__}: {exc}"
        finally:
            _ = time.time() - t0
            _warm_done.set()

    threading.Thread(target=worker, name="tools-ini", daemon=True).start()


def warmup_done(timeout: Optional[float] = None) -> bool:
    """等后台拉取结束（有上限）。给 `--tools --wait` 这类显式场景用。"""
    return _warm_done.wait(timeout if timeout is not None else 0)


def snapshot(use_remote: bool = True) -> Manifest:
    """取当前可用的清单：后台结果 → 缓存 → 内置。绝不阻塞。"""
    if use_remote:
        with _lock:
            if _warm is not None:
                return _warm
        m = load_cache()
        if m is not None:
            return m
    return BUILTIN


def reset_for_tests() -> None:
    """清掉进程内状态（自测用）。"""
    global _warm, _warm_started, _warm_error
    with _lock:
        _warm = None
        _warm_started = False
        _warm_error = ""
        _warm_done.clear()


def status_line(m: Manifest) -> str:
    src = {"remote": "远端", "cache": "本地缓存", "builtin": "内置兜底"}
    return f"工具清单来源: {src.get(m.source, m.source)}（{len(m.tools)} 个工具）"


def summarize(m: Manifest) -> List[Tuple[str, str, str]]:
    """给 --tools 用：(键, 显示名, 说明)。"""
    return [(t.key, t.name, t.note) for t in m.tools.values()]
