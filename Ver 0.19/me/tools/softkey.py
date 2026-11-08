# -*- coding: utf-8 -*-
"""首次无网、且本地没有缓存的「软键」防呆菜单。

场景：Slim 包刚解压到一台新机器，没有 tools/、没网、也没有 %LOCALAPPDATA%
里的 tools.ini 缓存。这时候第三方通道一定用不了，但**程序不能就这么废了**：
自研通道不需要任何外部工具，GameMaker 的 --rebuild 也能独立跑。

四颗软键（数字键直接选）：

    1  自动下载缺失的外部工具 —— 程序自己下、自己解压、自己验（见 fetch.py）
    2  扫描本机目录，自动找工具 —— 在常见位置找同名 exe
    3  指定工具根目录        —— 写 toolsdir.ini
    4  仅用自研核心继续        —— 本次会话强制自研，不再提示

按 1 不是「给你个链接」，是真的把文件取回来：有直链的走直链（按镜像顺序
重试），只有 .7z 的（AssetStudio）才下全量包、从里面挑 `tools/<子目录>/`。

设计约束：这个模块只做交互，不 import 引擎；找不到工具是常态，不抛异常。
"""

import os
from pathlib import Path
from typing import List, Optional

from me.tools import fetch
from me.tools import manifest as mf
from me.tools import paths

KEYS = (
    ("1", "下载缺失工具"),
    ("2", "扫描本机工具"),
    ("3", "指定工具根目录"),
    ("4", "仅用自研核心"),
)

# 扫描时最多往下走几层，别把整个 C 盘翻一遍
SCAN_DEPTH = 6


def needed() -> bool:
    """是否需要弹软键菜单：有工具就不需要；清单来自远端/缓存也不慌。

    ME_NO_SOFTKEY=1 直接跳过（自测/脚本化调用用，避免被菜单挡住）。
    """
    if os.environ.get("ME_NO_SOFTKEY") == "1":
        return False
    if paths.any_installed():
        return False
    return mf.snapshot().source == "builtin"


def _scan_roots() -> List[Path]:
    home = Path.home()
    roots = [
        paths.ROOT,
        paths.ROOT.parent,
        home / "Desktop",
        home / "Downloads",
        home / "Documents",
        Path("C:/Tools"),
        Path("C:/MultiExtractor"),
        Path("D:/Games"),
    ]
    seen, out = set(), []
    for r in roots:
        try:
            key = str(r.resolve()).lower()
        except OSError:
            continue
        if r.is_dir() and key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _names_to_find() -> List[str]:
    names: List[str] = []
    for spec in mf.snapshot().tools.values():
        for rel in spec.paths:
            names.append(Path(rel).name.lower())
    return sorted(set(names))


def scan(found_cb=None) -> Optional[Path]:
    """在常见目录里找工具可执行文件。找到返回「工具根目录」候选。"""
    names = _names_to_find()
    if not names:
        return None
    for root in _scan_roots():
        stack = [(root, 0)]
        while stack:
            d, depth = stack.pop()
            if depth > SCAN_DEPTH:
                continue
            try:
                entries = list(d.iterdir())
            except OSError:
                continue
            for e in entries:
                if found_cb is not None and e.is_dir():
                    found_cb(e)
                if e.is_file() and e.name.lower() in names:
                    # 约定：找到的 exe 所在目录的「上两级」是工具根，
                    # 例如 <root>/utmt/UndertaleModCli.exe
                    cand = e.parent.parent if e.parent != root else e.parent
                    return cand
            for e in entries:
                if e.is_dir() and not e.name.startswith("."):
                    stack.append((e, depth + 1))
    return None


def download(ui, allow_package: Optional[bool] = None, only_cache: bool = False):
    """真的把缺的工具下下来。返回 (结果列表, 是否用了全量包)。

    有直链的走直链；只有 .7z 的（AssetStudio）走全量包 —— 全量包一百多 MB，
    所以交互式里会先问一句，非交互（allow_package=True）则直接下。

    ``only_cache=True``（设置面板 [10]）只用本地缓存安装包：
    一个字节都不下，缓存里没有就直说没有。
    """
    missing = fetch.missing_keys()
    if not missing:
        ui.ok("外部工具齐全，无需下载")
        return [], False

    if only_cache:
        cached = fetch.cached_packages()
        if not cached:
            ui.warn("没有本地缓存安装包: " + str(fetch.download_dir()))
            return [], False
        ui.step("本地缓存安装包: " + ", ".join(cached))
    else:
        ui.step("下载: " + ", ".join(missing))

    def tick(done, total, name):
        # 有总长才画百分比；没有就按已下字节数报（Content-Length 缺失很常见）
        ui.progress(done, total, f"下载 {name}")

    results, used_pkg = fetch.install(
        missing, log=lambda m: ui.detail(m), tick=tick,
        allow_package=(True if only_cache else False), only_cache=only_cache)

    for res in results:
        (ui.ok if res.ok else ui.warn)(res.line())

    still = [r.key for r in results if not r.ok]
    if still and not only_cache and allow_package is not False:
        url = fetch.package_url()
        if url.lower().endswith(".zip"):
            if allow_package is None:
                ans = ui.clean_input(ui.ask(
                    f"还要下全量包（约 140 MB）来补 {', '.join(still)} 吗？[Y/n]"))
                allow_package = ans.lower() not in ("n", "no", "不")
            if allow_package:
                ui.step("下载全量包 ...")
                pkg_results = fetch.install_from_package(
                    still, log=lambda m: ui.detail(m), tick=tick)
                got = {r.key: r for r in pkg_results}
                results = [got.get(r.key, r) for r in results]
                used_pkg = True
                for res in pkg_results:
                    (ui.ok if res.ok else ui.warn)(res.line())
        else:
            ui.warn("清单里的全量包地址不是 zip，没法自动取")

    if paths.any_installed():
        ui.ok("外部工具可用")
    else:
        ui.warn("外部工具仍缺失: [2] 扫描 [3] 指定根目录 [4] 仅自研")
    return results, used_pkg


def adopt(directory) -> bool:
    """把目录认作工具根：写 toolsdir.ini，再验一次。"""
    d = Path(directory)
    if not d.is_dir():
        return False
    paths.set_override_dir(d)
    return paths.any_installed()


def render(ui) -> str:
    """打印软键菜单并等一次选择。返回动作名。"""
    man = mf.snapshot()
    ui.sep()
    ui.warn("未安装外部工具")
    ui.detail(mf.status_line(man))
    if man.full_package:
        ui.detail(f"全量包: {man.full_package}")
    ui.info("软键:")
    for key, label in KEYS:
        ui.detail(f"[{key}] {label}")
    ui.detail("输入 /help 列出指令")
    return "ask"


def ask(ui) -> str:
    """读一次软键，返回 1..4 对应的动作名；非法输入返回 ""。

    行首带斜杠的一律当**指令**（`/settings` 之类）：软键菜单是程序见到的
    第一个界面，用户在这一步敲 `/settings` 想进设置是很自然的事。
    """
    raw = ui.clean_input(ui.ask("软键"))
    if raw.startswith("/"):
        dispatch = getattr(ui, "command", None)
        if dispatch is not None and dispatch(raw) == "quit":
            return "quit"
        return ""
    return {
        "1": "download",
        "2": "scan",
        "3": "pick",
        "4": "self",
    }.get(raw, "")


def handle(ui, action: str) -> str:
    """执行一颗软键。返回 'ok'（可继续）/ 'self'（仅自研）/ ''（无变化）。"""
    if action == "download":
        download(ui)
        return "ok"

    if action == "scan":
        ui.step("扫描 ...")
        hit = scan()
        if hit is None:
            ui.warn("未找到工具")
            return "ok"
        if adopt(hit):
            ui.ok(f"工具根目录: {hit}")
            return "ok"
        ui.warn(f"工具不全: {hit}")
        return "ok"

    if action == "pick":
        raw = ui.clean_input(ui.ask("工具根目录"))
        if not raw:
            return "ok"
        if adopt(Path(raw)):
            ui.ok(f"工具根目录: {raw}")
        else:
            ui.warn(f"未找到工具: {raw}")
        return "ok"

    if action == "self":
        ui.ok("仅用自研核心")
        return "self"

    return ""


def run(ui) -> str:
    """完整的软键循环。返回 'self'（强制自研）/ 'ok' / 'quit'（用户 /quit）。"""
    while True:
        render(ui)
        action = ask(ui)
        if action == "quit":
            return "quit"
        if not action:
            ui.warn("范围 1-4")
            continue
        res = handle(ui, action)
        if res == "self":
            return "self"
        if paths.any_installed():
            ui.ok("外部工具可用")
            return "ok"
