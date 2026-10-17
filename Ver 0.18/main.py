# -*- coding: utf-8 -*-
"""True MultiExtractTool 入口。

用法：
    python main.py                          # 交互式菜单
    python main.py <文件或目录>              # 直接提取（全部类型）
    python main.py <路径> --only image,audio # 只要图片和音频
    python main.py <路径> -o D:\\out         # 指定输出目录
    python main.py --tools                  # 检查外部工具是否齐全
    python main.py --engines                # 列出已加载的引擎模块
    python main.py --setup                  # 查看/定位 setup.ini 配置文件
    python main.py --version                # 只打印版本
    python main.py <路径> --json             # NDJSON 事件流（GUI 调用）
    python main.py <路径> --experimental --self  # 换自研拆包通道（默认第三方）
    python main.py --channels               # 每个引擎的两条通道状态

不带任何参数 = 原来的交互菜单，双击 exe 就是这个。
--json 的完整约定见 GUI-API.md。
"""

import argparse
import os
import sys
import time
from pathlib import Path

# 允许从任意工作目录运行：把项目根加进 sys.path。
# 打包成 exe 后代码已经在归档里、import 机制自己认得，这一步不但没用，
# 还会把 _MEIPASS 插到最前面，所以跳过。
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from me import (cli_path_ini, commands, config, console, events,  # noqa: E402
                menu_flow, setup_ini, ui)
from me.engines import LOAD_ERRORS, names as engine_names  # noqa: E402
from me.runner import run                     # noqa: E402
from me.scanner import detect_engine          # noqa: E402
from me.tools import manifest as tool_manifest
from me.tools import paths as tool_paths
from me.tools import softkey      # noqa: E402

class _ArgError(Exception):
    """参数错误。不用 argparse 默认的 sys.exit，好让 GUI 拿到结构化报错。"""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        raise _ArgError(message)


def _build_parser() -> "_Parser":
    """命令行参数表。菜单拼出来的参数也用同一张表解析，保证两边不走岔。"""
    ap = _Parser(
        prog="MultiExtractor",
        description="多引擎游戏资源提取工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("target", nargs="?", help="游戏文件或目录")
    ap.add_argument("-o", "--out", help="输出目录")
    ap.add_argument("-e", "--engine", help="强制指定引擎")
    ap.add_argument("--only", help="只提取这些分类，逗号分隔：image,audio,text,font,video")
    ap.add_argument("--no-deep", action="store_true",
                    help="不解容器，仅收集散落文件")
    ap.add_argument("--keep-work", action="store_true",
                    help="保留 work/ 中间产物")
    ap.add_argument("--fix", action="store_true",
                    help="先重建容器结构（治愈无法解析的 data.win）再提取")
    ap.add_argument("--repair", action="store_true",
                    help="只重建容器结构，写出 <名字>_rebuilt.win，不提取")
    ap.add_argument("--rebuild", action="store_true",
                    help="全量重建：按语义模型重新排布整份文件（不等长）")
    ap.add_argument("--detect", action="store_true", help="只识别引擎，不提取")
    ap.add_argument("--tools", action="store_true", help="检查外部 CLI 是否齐全")
    ap.add_argument("--tools-dir", metavar="DIR",
                    help="指定外部工具根目录（写进 toolsdir.ini，下次生效）")
    ap.add_argument("--tools-refresh", action="store_true",
                    help="强制重新拉远端 tools.ini（默认启动时后台拉一次）")
    ap.add_argument("--tools-get", nargs="*", metavar="KEY",
                    help="自动下载缺失的外部工具（可只给某几个键，"
                         "如 --tools-get utmt repak）")
    ap.add_argument("--tools-get-full", action="store_true",
                    help="允许为了补工具下载全量包（约 140 MB）；"
                         "--tools-get 默认不下")
    ap.add_argument("--update", nargs="?", const="", default=None,
                    metavar="VERID",
                    help="在线更新：拉远端 version.json 比版本、下更新包、"
                         "校验 SHA256、拉起守护进程后退出。"
                         "可写 --update verid=0.18E / --update beta/stable")
    ap.add_argument("--engines", action="store_true", help="列出已加载的引擎模块")
    # ---- V0.17 两条通道：实验性功能 + 自研开关，两个都开才换自研
    ap.add_argument("--experimental", action="store_true",
                    help="启用实验性功能（换自研拆包通道的前置开关）")
    ap.add_argument("--self", dest="want_self", action="store_true",
                    help="使用自研拆包核心；必须同时给 --experimental，否则仍走第三方")
    ap.add_argument("--no-self", dest="want_self", action="store_false",
                    help="显式走第三方（默认）")
    ap.add_argument("--channels", action="store_true",
                    help="列出每个引擎的自研 / 第三方通道状态")
    ap.add_argument("--setup", action="store_true",
                    help="查看 setup.ini")
    ap.add_argument("-y", "--yes", action="store_true", help="不询问，直接执行")
    ap.add_argument("--json", action="store_true",
                    help="NDJSON 事件流写 stdout；人读文本转 stderr")
    ap.add_argument("--cancel-file", metavar="PATH",
                    help="该文件一出现就取消本次运行（GUI 用）")
    ap.add_argument("--categories", action="store_true",
                    help="列出可用分类")
    ap.add_argument("--version", action="store_true", help="只打印版本")
    return ap


def _dispatch(args) -> int:
    """按解析好的参数执行。main() 和交互菜单都走这里。"""
    if args.update is not None:
        return cmd_update(args.update, args)
    if args.version:
        return cmd_version()
    if args.categories:
        return cmd_categories()
    if args.tools_get is not None:
        return cmd_tools_get(args.tools_get, args.tools_get_full)
    if args.tools:
        return cmd_tools()
    if args.engines:
        return cmd_engines()
    if args.channels:
        return cmd_channels()
    if args.setup:
        return cmd_setup()
    if args.detect:
        return cmd_detect(args.target)
    if args.rebuild:
        return cmd_rebuild(Path(args.target) if args.target else None, args)
    if args.repair:
        return cmd_repair(Path(args.target) if args.target else None, args)

    # GUI 调用必须带目标：不带就掉进交互菜单等人输入，壳会一直挂着
    if args.json and not args.target:
        ui.error("--json 需要指定目标路径")
        return config.EXIT_ARGS

    categories = _parse_only(args.only)
    if categories is _BAD_ONLY:
        return config.EXIT_ARGS

    if args.target:
        return cmd_extract(Path(args.target), args, categories)

    return interactive(args)


def _run_argv(argv) -> int:
    """把菜单拼出来的参数当命令行再跑一遍 —— 菜单与命令行只有这一条执行路径。"""
    ns = _build_parser().parse_args([str(x) for x in argv])
    if ns.json and not events.enabled():
        events.enable(ns.cancel_file)
    return _dispatch(ns)


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    if args.json and not events.enabled():
        events.enable(args.cancel_file)
    if args.json:
        events.begin(app=config.APP_NAME, version=config.VERSION,
                     pid=os.getpid(), target=args.target,
                     engine=args.engine, only=args.only, out=args.out,
                     cancel_file=args.cancel_file)

    ui.banner()

    # 更新残留自检：清理上轮留下的 daemon.bat；有下好的包就问要不要装。
    # 放在 banner 之后、一切业务之前 —— 用户第一眼就能看到「可以更新」。
    code = _startup_update_check(args)
    if code is not None:
        return code

    # setup.ini 坏掉时提醒一句。以前这里会静默退回默认值，
    # 用户改了保存路径却没生效、还不知道为什么。
    _setup_err = setup_ini.load()["error"]
    if _setup_err and not args.setup:
        ui.warn(_setup_err)
        ui.detail("详情见 --setup")

    return _dispatch(args)


# ------------------------------------------------------------------ 在线更新
def _startup_update_check(args):
    """启动自检。返回 None = 继续正常流程；返回整数 = 直接以该退出码结束。

    两件事：
      1. 清理上一轮更新留下的 `daemon.bat`（有它说明守护进程跑完了）；
      2. 盘上有 `UpdatePack.zip` 就问一句「要不要现在装」——
         这是「断点恢复」：上次下完了但没装成（关机/取消/没权限）。
    """
    from me import update as upd

    try:
        if upd.sanitize():
            ui.detail("已清理上次更新留下的 daemon.bat")
    except OSError:
        pass

    pack = upd.pending()
    if pack is None:
        return None

    state = upd.read_state()
    ver = state.get("version", "")
    events.emit("update_pending", pack=str(pack),
                size=pack.stat().st_size, version=ver or None,
                changelog=state.get("changelog") or None)

    # --update 自己在管这件事；--json（GUI 调用）不阻塞，壳照事件自己弹窗
    if args.json or args.update is not None:
        return None

    # 非交互（管道 / 计划任务）时不问，免得把脚本挂死
    if not _stdin_is_tty():
        ui.info("检测到已下载的更新包；非交互模式不询问，需要时用 --update")
        return None

    ui.sep()
    ui.warn(f"检测到已下载的更新包：{pack.name}"
            f"（{upd.human(pack.stat().st_size)}）")
    if ver:
        ui.detail(f"版本: {ver}")

    action = _ask_install_pending()
    if action is None:
        ui.info("保留更新包，本次继续正常流程（下次启动还会问）")
        return None
    return action


def _stdin_is_tty() -> bool:
    """能不能安全地问一句。计划任务里 stdin 可能没有 isatty。"""
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def _ask_install_pending() -> object:
    """问一句要不要立刻装。返回 None=不装，或 EXIT_OK / EXIT_INTERNAL。"""
    from me import update as upd

    ans = ui.clean_input(ui.ask("是否立即安装？(Y/N)", "N"))
    if ans.lower() not in ("y", "yes", "是"):
        return None
    return _install_pending()


def _install_pending() -> int:
    """把盘上已有的更新包装掉：写 bat → 拉起 → 返回退出码（主程序随后退出）。"""
    from me import update as upd

    pack = upd.pending()
    try:
        bat = upd.write_daemon()
    except OSError as exc:
        ui.error(f"写 daemon.bat 失败: {exc}")
        return config.EXIT_INTERNAL
    ui.ok(f"已生成 {bat.name}，主程序即将退出交给守护进程")
    events.emit("update_launch", daemon=str(bat),
                pack=str(pack) if pack else None, reason="pending-pack")
    if not upd.launch_daemon(bat):
        ui.error("拉起守护进程失败；更新包还在，可手动解压")
        return config.EXIT_INTERNAL
    ui.info("更新会在 2 秒后开始，期间请不要重复启动本程序")
    return config.EXIT_OK


def cmd_update(spec, args) -> int:
    """在线更新（--update [verid=...]）。

    顺序即协议：**查 → 比 → 确认 → 下 → 校验 → 写 bat → 拉起 → 退出**。
    最后一步必须是「主程序自己退出」：Windows 上正在运行的 exe 文件是锁着的，
    解压覆盖只能等它没了再做，这也是 daemon.bat 存在的唯一理由。
    """
    from me import update as upd

    want = (spec or "").strip()
    if want.startswith("verid="):
        want = want[len("verid="):].strip()

    ui.step("检查更新")
    ui.detail(f"本地版本: {config.VERSION}")
    ui.detail(f"清单地址: {upd.remote_url()}")

    info = upd.fetch()
    if info is None:
        # 用户要求：断网/超时**静默失败**，只留一行 warn
        ui.warn("检查更新超时")
        events.emit("update_check", ok=False, reason="timeout",
                    url=upd.remote_url(), local=config.VERSION)
        events.emit("end", exit=config.EXIT_OK)
        return config.EXIT_OK

    p = upd.plan(info, want)
    events.emit("update_check", ok=True, local=p["local"], remote=p["remote"],
                available=p["available"], kind=p["kind"],
                beta_available=p["beta_available"], reason=p["reason"],
                changelog=p["changelog"] or None, url=p["url"] or None,
                sha256=p["sha256"] or None)

    if info.latest_stable:
        ui.info(f"远端稳定版: {info.latest_stable}")
    if info.latest_beta:
        ui.info(f"远端公测版: {info.latest_beta}")
    if info.changelog:
        ui.info("更新说明: " + info.changelog.replace("\n", " / "))

    if not p["available"]:
        reason = p["reason"] if p["reason"] != "已是最新" else ""
        ui.ok("已是最新" + (f"（{reason}）" if reason else ""))
        events.emit("end", exit=config.EXIT_OK)
        return config.EXIT_OK

    ui.ok(f"发现新版本: {p['remote']}（{p['reason']}）")

    # ---- 确认 ----
    if not args.yes:
        if p["beta_available"]:
            q = (f"有公测版可用（{p['remote']}），是否更新？"
                 "公测版可能不稳定 (Y/N)")
        else:
            q = f"是否更新到 {p['remote']}？(Y/N)"
        ans = ui.clean_input(ui.ask(q, "N"))
        if ans.lower() not in ("y", "yes", "是"):
            ui.info("已取消更新")
            events.emit("update_cancelled", reason="user")
            events.emit("end", exit=config.EXIT_OK)
            return config.EXIT_OK

    if not p["url"]:
        ui.error("清单里没有 download_url，无法下载")
        events.emit("end", exit=config.EXIT_INTERNAL)
        return config.EXIT_INTERNAL

    # ---- 下载 + 校验 ----
    pack = upd.pack_path()
    ui.step(f"下载更新包 → {pack}")
    ok, err, size = upd.download(
        p["url"], pack, p["sha256"],
        progress=lambda d, t, lbl: ui.progress(d, t, f"下载 {lbl}"))
    ui.clear_progress()
    if not ok:
        ui.error(f"下载失败: {err}")
        events.emit("update_failed", stage="download", error=err)
        events.emit("end", exit=config.EXIT_INTERNAL)
        return config.EXIT_INTERNAL

    ui.ok(f"下载完成 {upd.human(size)}，SHA256 校验通过")
    upd.write_state(version=p["remote"], sha256=p["sha256"],
                    size=size, changelog=p["changelog"])
    events.emit("update_ready", pack=str(pack), size=size,
                version=p["remote"], sha256=p["sha256"])

    # ---- 写守护进程、拉起、退出 ----
    try:
        bat = upd.write_daemon(exe=upd.exe_name())
    except OSError as exc:
        ui.error(f"写 daemon.bat 失败: {exc}")
        events.emit("end", exit=config.EXIT_INTERNAL)
        return config.EXIT_INTERNAL

    ui.ok(f"已生成 {bat.name}（解压覆盖 → 删包 → 重启）")
    events.emit("update_launch", daemon=str(bat), pack=str(pack))
    if not upd.launch_daemon(bat):
        ui.error("拉起守护进程失败；更新包已下好，下次启动会再问你")
        events.emit("end", exit=config.EXIT_INTERNAL)
        return config.EXIT_INTERNAL

    ui.info("主程序退出以释放文件锁；2 秒后开始覆盖安装")
    ui.detail("更新期间请不要重复启动本程序")
    events.emit("end", exit=config.EXIT_OK)
    return config.EXIT_OK


# ------------------------------------------------------------------ 子命令
def cmd_version() -> int:
    ui.ok(f"{config.APP_NAME} {config.VERSION}")
    events.emit("version", app=config.APP_NAME,
                version=config.VERSION, schema=events.SCHEMA)
    return config.EXIT_OK


def _pick_data_file(target):
    """把用户给的目标（文件或目录）落到 data.win 上；找不到就返回 (None, 退出码)。"""
    if target is None:
        return None, config.EXIT_ARGS
    if not target.exists():
        ui.error(f"路径不存在: {target}")
        return None, config.EXIT_TARGET
    if target.is_dir():
        from me.engines.gamemaker import find_data_file
        found = find_data_file(target)
        if found is None:
            ui.error("目录里没找到 data.win / game.droid")
            return None, config.EXIT_TARGET
        return found, config.EXIT_OK
    return target, config.EXIT_OK


def cmd_rebuild(target, args) -> int:
    """全量重建（V0.15E）：读成语义模型 → 重新排布 → 写出一份全新的文件。

    与 ``--repair`` 的区别：不保留原文件长度，也不在原字节上缝补。
    指针全部按新布局重算，块里没人引用的字节直接丢掉。
    """
    from me.formats import gamemaker_full as full

    data_file, code = _pick_data_file(target)
    if data_file is None:
        if code == config.EXIT_ARGS:
            ui.error("--rebuild 需要指定 data.win 或游戏目录")
        return code

    ui.step(f"全量重建: {data_file}")
    out_dir = Path(args.out) if args.out else data_file.parent
    dest = out_dir / (data_file.stem + "_rebuilt" + data_file.suffix)

    t0 = time.perf_counter()
    try:
        rep = full.rebuild_file(data_file, dest)
    except Exception as exc:                       # noqa: BLE001
        ui.error(f"重建失败: {type(exc).__name__}: {exc}")
        events.emit("note", level="err", text=f"重建失败: {exc}")
        events.emit("end", exit=config.EXIT_INTERNAL)
        return config.EXIT_INTERNAL
    elapsed = time.perf_counter() - t0

    for line in _rebuild_lines(rep):
        ui.ok(line)
    for n in rep.notes:
        ui.detail(n)
    events.emit("result", ok=True, written=1,
                out_dir=str(Path(rep.dst).parent),
                engine="gamemaker", engine_name="GameMaker",
                detail=f"{data_file.name} -> {Path(rep.dst).name}",
                categories={}, notes=_rebuild_lines(rep),
                fallback=False, error=None, elapsed=round(elapsed, 1))
    events.emit("end", exit=config.EXIT_OK)
    return config.EXIT_OK


def _rebuild_lines(rep) -> list:
    """重建结果的读出行（人读文本与 NDJSON 用同一份）。"""
    return [
        f"建模 {len(rep.modeled)} 块，搬运 {len(rep.raw)} 块",
        f"写出 {Path(rep.dst).name}  {rep.new_bytes:,} 字节"
        f"（原 {rep.old_bytes:,}，{rep.new_bytes - rep.old_bytes:+,}）",
        "指针已按新布局重算，读回核对条目数一致",
    ]


def cmd_repair(target, args) -> int:
    """只重建容器结构（V0.15）：把 data.win 修成原版 UTMT 也能解析的形态。

    V0.15E 起**先走全量重建**：能整份重排就整份重排（长度会变）；
    只有遇到还没建模的布局（比如更老的 GMS2 特殊精灵）才退回
    原位补丁，并在输出里说明原因。
    """
    from me.formats import gamemaker_full as full
    from me.formats import gamemaker_rebuild as rb

    data_file, code = _pick_data_file(target)
    if data_file is None:
        if code == config.EXIT_ARGS:
            ui.error("--repair 需要指定 data.win 或游戏目录")
        return code

    out_dir = Path(args.out) if args.out else data_file.parent
    dest = out_dir / (data_file.stem + "_rebuilt" + data_file.suffix)

    ui.step(f"重建结构: {data_file}")
    t0 = time.perf_counter()
    try:
        rep = full.rebuild_file(data_file, dest)
    except Exception as exc:                       # noqa: BLE001
        ui.warn(f"全量重建不可用（{type(exc).__name__}: {exc}），退回原位补丁")
        events.emit("note", level="warn",
                    text=f"全量重建不可用，退回原位补丁: {exc}")
        return _repair_in_place(data_file, dest, rb, t0)

    elapsed = time.perf_counter() - t0
    ui.ok("全量重建")
    for line in _rebuild_lines(rep):
        ui.ok(line)
    for n in rep.notes:
        ui.detail(n)
    # 必须发 result：GUI 是靠这条事件里的 written 判断「有没有产出」的，
    # 只发 note 的话壳会报「退出码 0，但未产出文件」。
    events.emit("result", ok=True, written=1,
                out_dir=str(Path(rep.dst).parent),
                engine="gamemaker", engine_name="GameMaker",
                detail=f"{data_file.name} -> {Path(rep.dst).name}",
                categories={}, notes=["全量重建"] + _rebuild_lines(rep),
                fallback=False, error=None, elapsed=round(elapsed, 1))
    events.emit("end", exit=config.EXIT_OK)
    return config.EXIT_OK


def _repair_in_place(data_file, dest, rb, t0) -> int:
    """旧路径：在原字节上缝补，保证文件长度不变。"""
    try:
        rep = rb.rebuild_file(data_file, dest)
    except Exception as exc:                       # noqa: BLE001
        ui.error(f"重建失败: {type(exc).__name__}: {exc}")
        events.emit("note", level="err", text=f"重建失败: {exc}")
        events.emit("end", exit=config.EXIT_INTERNAL)
        return config.EXIT_INTERNAL
    elapsed = time.perf_counter() - t0

    for line in rep.rebuilt:
        ui.ok(line)
    for n in rep.notes:
        ui.detail(n)

    left = [f for f in rep.findings
            if f.level == "err" and f.code.startswith("after:")]
    if left:
        ui.warn(f"诊断: err {len(left)} 项")
        for f in left[:6]:
            ui.detail(f"{f.chunk}: {f.message}")
    if rep.new_bytes != rep.old_bytes:
        ui.error("文件长度变化, 结果丢弃")
        events.emit("end", exit=config.EXIT_INTERNAL)
        return config.EXIT_INTERNAL

    ui.ok(f"写出 {rep.dst}  {rep.new_bytes:,} 字节")
    events.emit("result", ok=True, written=1,
                out_dir=str(Path(rep.dst).parent),
                engine="gamemaker", engine_name="GameMaker",
                detail=f"{data_file.name} -> {Path(rep.dst).name}",
                categories={}, notes=list(rep.rebuilt) + list(rep.notes),
                fallback=False, error=None, elapsed=round(elapsed, 1))
    events.emit("end", exit=config.EXIT_OK)
    return config.EXIT_OK


def cmd_categories() -> int:
    ui.step("可用分类")
    for key, label in config.CATEGORY_LABEL.items():
        ui.ok(f"{key:<8} {label}")
        events.emit("category", key=key, label=label)
    return config.EXIT_OK


def cmd_tools() -> int:
    ui.step("外部工具自检")

    # 清单三级来源（远端 / 缓存 / 内置）：直接写出来，用户才知道
    # 「为什么清单里只有 4 个工具」到底是没网还是上游就这么多。
    man = tool_manifest.snapshot()
    if man.source == "builtin":
        # 显式查工具时值得等一次：启动时的后台拉取可能还没回来
        tool_manifest.warmup_done(tool_manifest.timeout_s())
        man = tool_manifest.snapshot()
    ui.info(tool_manifest.status_line(man) + f"  上游: {tool_manifest.remote_url()}")
    events.emit("tools_manifest", source=man.source, version=man.version,
                updated=man.updated, count=len(man.tools),
                full_package=man.full_package,
                url=tool_manifest.remote_url())

    missing = 0
    for key, path, desc, expect in tool_paths.status():
        if path:
            ui.ok(f"{key:<12} {path}")
        else:
            missing += 1
            ui.warn(f"{key:<12} 缺失（期望 {expect}）")
            ui.detail(desc)
        events.emit("tool", key=key, ok=bool(path),
                    path=str(path) if path else None,
                    expect=str(expect), desc=desc)

    if missing:
        ui.sep()
        ui.info(f"缺失 {missing} 个工具。{tool_paths.repair_hint()}")
        ui.info(f"全量包: {tool_paths.download_hint()}")
        # 打包版里写「python main.py」是错的 —— 用户没有 Python，也没有 .py
        # repair_hint 已经把「让程序自己下」那条说了，这里只说剩下的两条路
        ui.detail(f"或 --tools-dir <目录>（{_self_invocation()}）")
    else:
        ui.ok("外部工具齐全")
    ui.sep()
    ui.info(f"运行环境: {_env_desc()}")
    return config.EXIT_TOOL if missing else config.EXIT_OK


def _self_invocation() -> str:
    """给用户抄的命令：打包版是 exe 名，源码版是 python main.py。"""
    if not config.IS_FROZEN:
        return "python main.py"
    import sys
    from pathlib import Path
    try:
        return Path(sys.executable).name
    except (AttributeError, ValueError):
        return "MultiExtractorCLI.exe"


def cmd_tools_get(keys, allow_full: bool = False) -> int:
    """自动下载缺失的外部工具（--tools-get）。

    这是「程序自己下」的入口：有直链走直链（按镜像顺序重试），
    只有 .7z 的（AssetStudio）需要全量包 —— 一百多 MB，所以默认**不下**，
    要显式 --tools-get-full 或在交互式软键里按 y。
    """
    from me.tools import fetch

    man = tool_manifest.snapshot()
    ui.step("自动下载外部工具")
    ui.info(tool_manifest.status_line(man))
    events.emit("tools_manifest", source=man.source, version=man.version,
                updated=man.updated, count=len(man.tools),
                full_package=man.full_package,
                url=tool_manifest.remote_url())

    wanted = list(keys) if keys else fetch.missing_keys()
    if not wanted:
        ui.ok("外部工具齐全，无需下载")
        events.emit("end", exit=config.EXIT_OK)
        return config.EXIT_OK

    known = set(tool_paths.specs())
    bad = [k for k in wanted if k not in known]
    if bad:
        ui.error("未知工具: " + ", ".join(bad))
        ui.detail("可选: " + ", ".join(sorted(known)))
        events.emit("note", level="err", text="未知工具: " + ", ".join(bad))
        events.emit("end", exit=config.EXIT_ARGS)
        return config.EXIT_ARGS

    ui.info(f"待下载: {', '.join(wanted)}")

    def tick(done, total, name):
        ui.progress(done, total, f"下载 {name}")

    results, used_pkg = fetch.install(
        wanted, log=lambda m: ui.detail(m), tick=tick,
        allow_package=allow_full)
    if used_pkg:
        ui.info("来源: 全量包")

    ui.sep()
    for res in results:
        (ui.ok if res.ok else ui.warn)(res.line())
        events.emit("tool_get", key=res.key, ok=res.ok, how=res.how,
                    path=str(res.path) if res.path else None,
                    size=res.size, error=res.error or None)

    left = [r.key for r in results if not r.ok]
    if left:
        ui.sep()
        ui.warn(f"仍缺 {len(left)} 个: {', '.join(left)}")
        if not allow_full:
            ui.info("加 --tools-get-full 可用全量包（约 140 MB，下过一次会缓存）")
        ui.info(f"或 --tools-dir <目录>（{_self_invocation()}）")
        events.emit("end", exit=config.EXIT_TOOL)
        return config.EXIT_TOOL

    ui.ok("外部工具齐全")
    events.emit("end", exit=config.EXIT_OK)
    return config.EXIT_OK


def _env_desc() -> str:
    """一行环境摘要，方便反馈问题时贴出来。"""
    import importlib.util
    import platform

    kind = "打包版(exe)" if config.IS_FROZEN else "源码版"
    # 这里故意用 find_spec 而不是 import numpy：
    # 写成 import numpy 的话 PyInstaller 会当成静态依赖，
    # 把我们在构建时特意排除掉的 numpy 又塞回包里。
    np_on = importlib.util.find_spec("numpy") is not None
    return (f"Python {platform.python_version()} / {kind} / "
            f"numpy {'已启用' if np_on else '未启用'}")


def cmd_engines() -> int:
    ui.step("已加载的引擎模块")
    for key, name in engine_names():
        ui.ok(f"{key:<12} {name}")
        events.emit("engine", key=key, name=name)
    if LOAD_ERRORS:
        ui.sep()
        ui.warn("以下模块加载失败:")
        for name, err in LOAD_ERRORS.items():
            ui.detail(f"{name}: {err}")
    return 0


def cmd_channels() -> int:
    """列出两条通道的状态（--channels）。

    这张表就是「哪种引擎现在能走自研」的唯一答案：自研那栏写着待补的，
    勾了自研开关也会退回第三方（判定见 me/channel.py）。
    """
    from me import channel

    ui.step("拆包通道")
    ui.info("默认第三方；实验性功能 + 自研开关同时启用才换自研；CTF 只有自研。")
    for cap in channel.matrix():
        third = cap.third_name if cap.third else "无（不接第三方）"
        self_ = cap.self_name if cap.self_ready else "待补（V0.17）"
        ui.ok(f"{cap.key:<10} 第三方: {third}")
        ui.detail(f"{'':<10} 自研  : {self_}")
        events.emit("channel_cap", engine=cap.key,
                    third=cap.third, third_name=third,
                    self_ready=cap.self_ready, self_name=self_)
    return config.EXIT_OK


def cmd_setup() -> int:
    """显示 setup.ini 的位置和当前设置。"""
    ui.step("配置（setup.ini）")
    path = setup_ini.setup_path()
    cfg = setup_ini.load()

    ui.ok(f"setup.ini: {path}")
    if not path.exists():
        ui.warn("文件不存在，写入失败，使用内置默认值")
    if cfg["error"]:
        ui.warn(f"配置异常: {cfg['error']}")

    ui.sep()
    ui.info(f"输出目录: {setup_ini.output_root()}")
    events.emit("config", file=str(path), exists=path.exists(),
                out_path=cfg[setup_ini.KEY_PATH],
                out_root=str(setup_ini.output_root()),
                error=cfg["error"] or None)
    return config.EXIT_OK


def cmd_detect(target) -> int:
    """只识别不提取。"""
    if not target:
        ui.error("--detect 需要指定路径")
        return config.EXIT_ARGS
    path = Path(target).expanduser()
    if not path.exists():
        ui.error(f"路径不存在: {path}")
        return config.EXIT_TARGET
    det = detect_engine(path)
    ui.ok(f"识别结果: {det.describe()}")
    ui.detail(f"引擎键: {det.engine}")
    if det.shell:
        ui.detail(f"保护壳: {det.shell}")
    if det.inner:
        ui.detail(f"壳内引擎: {det.inner}")
    if det.entry:
        ui.detail(f"主数据: {det.entry}")
    for ev in det.evidence:
        ui.detail(f"证据: {ev}")
    if det.truncated:
        ui.warn("扫描条目超上限，已截断；特征可能漏检")
    if not det.is_known:
        ui.warn("未能识别引擎，退回通用收集。")
    events.emit("detect", engine=det.engine, name=det.name,
                shell=det.shell, inner=det.inner, entry=det.entry,
                evidence=list(det.evidence),
                truncated=bool(det.truncated), known=bool(det.is_known))
    return config.EXIT_OK


def cmd_extract(target: Path, args, categories) -> int:
    if not target.exists():
        ui.error(f"路径不存在: {target}")
        return config.EXIT_TARGET
    res = run(target, out_dir=args.out, categories=categories,
              deep=not args.no_deep, engine_key=args.engine,
              keep_work=args.keep_work, progress=ui.progress,
              fix=args.fix, experimental=args.experimental,
              want_self=args.want_self)
    if res.written == 0:
        return config.EXIT_EMPTY
    return config.EXIT_OK


# ------------------------------------------------------------------ 交互
# 菜单只做两件事：问用户、把选择翻译成命令行参数。真正的执行走 _run_argv，
# 所以菜单和命令行永远是同一条路（见 me/menu_flow.py）。
def interactive(args) -> int:
    ctx = commands.Context(args=args)
    ui.set_command_hook(lambda raw: commands.handle(raw, ctx))
    # 第一次进菜单就把「怎么求助」说清楚：斜杠指令不好猜，得提醒。
    ui.info(ui.HELP_HINT)
    _warn_missing_tools()

    # 首次无网无缓存：先过一遍软键菜单，避免用户对着「第三方通道不可用」
    # 不知道下一步。选了 [4] 就本次会话强制自研。
    if softkey.needed():
        choice = softkey.run(ui)
        if choice == "quit":
            return config.EXIT_OK
        if choice == "self":
            args.experimental = True
            args.want_self = True

    target = _ask_target()
    if target is None:
        return config.EXIT_TARGET

    sess = menu_flow.Session(
        target=str(target), forced=args.engine or "",
        experimental=args.experimental, want_self=args.want_self,
        no_deep=args.no_deep, keep_work=args.keep_work, out=args.out or "",
    )
    ctx.set_session(sess)
    det = None

    while True:
        ui.sep()
        if not sess.analyzed:
            # 步骤 1：先分析，不分析就不给提取/重建 —— 避免用户在没识别之前乱按
            ui.info(f"目标: {sess.target}")
            ui.detail("先分析目标，再决定做什么")
            items = menu_flow.pre_analyze_options()
            idx = ui.menu("请选择:", [label for label, _k in items])
            act = items[idx][1]

            if act == menu_flow.EXIT:
                return config.EXIT_OK
            if act == menu_flow.PATH:
                nt = _ask_target()
                if nt is not None:
                    sess.target = str(nt)
                    sess.engine_key = ""
                    sess.engine_name = ""
                    sess.analyzed = False
                continue
            if act == menu_flow.ANALYZE:
                det = _analyze(sess)
                if det is None:
                    continue
                sess.engine_key = det.engine
                sess.engine_name = det.name
                sess.analyzed = True
                ui.ok(menu_flow.result_banner(sess, det))
            continue

        # 步骤 2：分析完，按引擎能力给菜单
        ui.info(menu_flow.result_banner(sess, det))
        ui.info(f"输出目录: {setup_ini.describe()}")
        if menu_flow.channel_switchable(sess.effective_engine):
            ui.info(f"拆包通道: {menu_flow.channel_label(sess)}"
                    f" —— {menu_flow.channel_detail(sess)}")

        items = menu_flow.main_options(sess)
        idx = ui.menu("请选择接下来的操作:", [label for label, _k in items])
        act = items[idx][1]

        if act == menu_flow.EXIT:
            return config.EXIT_OK

        if act == menu_flow.PATH:
            nt = _ask_target()
            if nt is not None:
                sess.target = str(nt)
                sess.engine_key = ""
                sess.engine_name = ""
                sess.analyzed = False
                det = None
            continue

        if act == menu_flow.DETAIL:
            if det is not None:
                _show_detail(det)
            continue

        if act == menu_flow.EXTRACT:
            _menu_extract(sess)
            continue

        if act == menu_flow.REPAIR_MENU:
            _menu_repair(sess)
            continue

        if act == menu_flow.CHANNEL_MENU:
            _menu_channel(sess)
            continue

        if act == menu_flow.MORE:
            _menu_more(sess, args)
            continue


def _analyze(sess):
    """分析目标：和 --detect 是同一套识别，顺手发 detect 事件。"""
    ui.step("分析目标 ...")
    det = detect_engine(Path(sess.target))
    events.emit("detect", engine=det.engine, name=det.name,
                shell=det.shell, inner=det.inner, entry=det.entry,
                evidence=list(det.evidence),
                truncated=bool(det.truncated), known=bool(det.is_known))
    ui.ok(f"识别结果: {det.describe()}")
    for ev in det.evidence[:6]:
        ui.detail(ev)
    if det.truncated:
        ui.warn("扫描条目超上限，已截断；特征可能漏检")
    if not det.is_known:
        ui.warn("未能识别引擎，退回通用收集。")
    return det


def _menu_extract(sess) -> None:
    ui.sep()
    items = menu_flow.extract_options(sess)
    idx = ui.menu("提取什么:", [label for label, _k in items])
    key = items[idx][1]
    if key == "back":
        return
    cats = None if key == "cat:all" else set(key[4:].split(","))
    argv = menu_flow.build_extract_argv(sess, cats)
    ui.detail("执行: " + " ".join(argv))
    _run_argv(argv)


def _menu_repair(sess) -> None:
    ui.sep()
    for line in menu_flow.REPAIR_WARNING.split("\n"):
        ui.warn(line)
    items = menu_flow.repair_options()
    idx = ui.menu("请选择修复模式:", [label for label, _k in items])
    mode = items[idx][1]
    if mode == "back":
        return
    argv = menu_flow.build_repair_argv(sess, mode)
    ui.detail("执行: " + " ".join(argv))
    _run_argv(argv)


def _menu_channel(sess) -> None:
    ui.sep()
    ui.info(f"当前通道: {menu_flow.channel_label(sess)}"
            f"（{menu_flow.channel_detail(sess)}）")
    items = menu_flow.channel_options(sess)
    idx = ui.menu("切换拆包通道:", [label for label, _k in items])
    action = items[idx][1]
    if action == "back":
        return
    if action == "switch_self":
        sess.experimental = True
        sess.want_self = True
    elif action == "switch_third":
        sess.want_self = False
    ui.ok(f"通道: {menu_flow.channel_label(sess)}")
    ui.detail("之后的提取/重建都会带上 "
              + ("--experimental --self" if sess.want_self else "--experimental"))


def _menu_more(sess, args) -> None:
    ui.sep()
    items = menu_flow.more_options(sess)
    idx = ui.menu("更多选项:", [label for label, _k in items])
    action = items[idx][1]
    if action == "back":
        return
    if action == "toggle_deep":
        sess.no_deep = not sess.no_deep
        ui.ok(f"不解容器: {'开' if sess.no_deep else '关'}")
        return
    if action == "toggle_work":
        sess.keep_work = not sess.keep_work
        ui.ok(f"保留中间产物: {'开' if sess.keep_work else '关'}")
        return
    if action == "engine":
        _menu_engine(sess)
        return
    if action == "tools":
        cmd_tools()
    elif action == "engines":
        cmd_engines()
    elif action == "setup":
        cmd_setup()
    elif action == "version":
        cmd_version()


def _ask_target():
    """问用户要路径，返回 Path 或 None。

    这一行也认斜杠指令：用户第一下手边没有路径，敲 `/settings` 就该进设置，
    而不是被当成一个名叫 `/settings` 的路径去 stat 一遍再报「不存在」。
    """
    while True:
        # ui.ask 已经去过引号/空白/BOM，这里再兜一次底（用户可能从别处粘进来）
        raw = ui.clean_input(ui.ask("目标路径"))
        if raw.startswith("/"):
            if ui.command(raw) == "quit":
                raise SystemExit(config.EXIT_OK)
            continue
        if not raw:
            ui.error("未输入路径")
            return None
        target = Path(raw)
        if not target.exists():
            # 打错了不该被一脚踢出去：说清楚，然后重问
            ui.error(f"路径不存在: {target}")
            continue
        return target


def _show_detail(det) -> None:
    """把识别详情完整打一遍。"""
    ui.sep()
    ui.info(f"引擎键  : {det.engine}")
    ui.info(f"显示名  : {det.name}")
    if det.shell:
        ui.info(f"保护壳  : {det.shell}")
    if det.inner:
        ui.info(f"壳内引擎: {det.inner} ({det.inner_name})")
    if det.entry:
        ui.info(f"主数据  : {det.entry}")
    if det.evidence:
        ui.info("证据:")
        for ev in det.evidence:
            ui.detail(ev)
    else:
        ui.info("证据: （无）")


def _menu_engine(sess) -> None:
    """强制指定引擎（-e）。自动识别被二次打包/特征被删时，用户自己知道答案。"""
    from me.engines.menu import build_engine_menu
    items = build_engine_menu()
    ui.sep()
    idx = ui.menu("指定引擎（-e）:", [label for label, _key in items])
    label, key = items[idx]
    sess.forced = key or ""
    if key:
        ui.ok(f"已指定引擎: {label}")
        ui.detail(f"之后的提取/重建都带 -e {key}（分析结果仅作参考）")
    else:
        ui.ok("已切换为自动识别")


def _warn_missing_tools() -> None:
    """启动时提示缺哪些外部工具，缺失不影响可跑的引擎。"""
    missing = [key for key, path, _desc, _exp in tool_paths.status() if not path]
    if missing:
        ui.warn(f"外部工具缺失: {', '.join(missing)}")
        ui.detail(tool_paths.repair_hint())
        ui.detail(f"全量包: {tool_paths.download_hint()}")


# ------------------------------------------------------------------ 工具
_BAD_ONLY = object()


def _parse_only(text):
    """解析 --only 参数，非法时返回哨兵值。"""
    if not text:
        return None
    cats = set()
    for part in text.replace("，", ",").split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part not in config.CATEGORY_LABEL:
            ui.error(f"未知分类: {part}（可选: "
                     f"{', '.join(config.CATEGORY_LABEL)}）")
            return _BAD_ONLY
        cats.add(part)
    return cats or None


def _prescan(argv=None):
    """解析参数之前先看有没有 --json / --cancel-file。

    事件流必须在任何人类输出之前打开，而 argparse 的 --help 和报错
    也算输出，所以只能先把 argv 扫一遍。
    """
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)
    json_mode = "--json" in argv
    cancel = None
    for i, item in enumerate(argv):
        if item == "--cancel-file" and i + 1 < len(argv):
            cancel = argv[i + 1]
        elif item.startswith("--cancel-file="):
            cancel = item.split("=", 1)[1]
    return json_mode, cancel


def _entry(argv=None) -> int:
    """真正的入口：初始化控制台 → 跑主流程 → 收尾。

    收尾放在 finally 里，这样 argparse 报错退出、用户 Ctrl+C 中断
    这些路径也能正常还原代码页、并在双击启动时把窗口留住。
    """
    json_mode, cancel = _prescan(argv)
    if json_mode:
        events.enable(cancel)
    console.setup()

    # 外部工具清单：启动就开一个守护线程去拉，3 秒超时，主流程从不等待。
    # 断网 / 上游挂了 → 静默退回缓存或内置清单，界面上只体现为来源不同。
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    if os.environ.get("ME_NO_NET") != "1":
        tool_manifest.start_warmup(
            force=("--tools-refresh" in raw_args))

    # --tools-dir：把用户指的工具目录记进 toolsdir.ini（下次仍然有效）
    if "--tools-dir" in raw_args:
        i = raw_args.index("--tools-dir")
        if i + 1 < len(raw_args):
            target = raw_args[i + 1]
            p = tool_paths.set_override_dir(target)
            print(f"[i] 工具根目录: {target}")
            print(f"[i] 配置文件: {p}")

    # 让 setup.ini 一开始就出现在程序旁边，用户不用先去别处找格式
    setup_ini.ensure()
    # 把自身位置写进 %LOCALAPPDATA%\MultiExtractor\CLIPath.ini（只放路径），
    # 把版本与署名写进 CLI 目录下的 version.ini：整包挪盘之后壳必须立刻
    # 拿到新位置，版本也一起跟过去
    cli_path_ini.write()
    code = config.EXIT_OK
    try:
        code = main(argv)
    except _ArgError as exc:
        events.begin(app=config.APP_NAME, version=config.VERSION,
                     pid=os.getpid(), target=None, engine=None,
                     only=None, out=None, cancel_file=cancel)
        ui.error(f"参数错误: {exc}")
        code = config.EXIT_ARGS
    except events.Cancelled:
        ui.warn("已取消")
        code = config.EXIT_CANCELLED
    except KeyboardInterrupt:
        print()
        ui.warn("已中断")
        code = config.EXIT_INTERRUPT
    except SystemExit as exc:
        code = int(exc.code or 0)
    except Exception as exc:  # noqa: BLE001
        ui.error(f"内部异常: {type(exc).__name__}: {exc}")
        code = config.EXIT_INTERNAL
    finally:
        events.finish(code)
        console.pause_if_needed()
        console.restore()
    return code


if __name__ == "__main__":
    raise SystemExit(_entry())
