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
"""

import argparse
import sys
from pathlib import Path

# 允许从任意工作目录运行：把项目根加进 sys.path。
# 打包成 exe 后代码已经在归档里、import 机制自己认得，这一步不但没用，
# 还会把 _MEIPASS 插到最前面，所以跳过。
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from me import config, console, setup_ini, ui   # noqa: E402
from me.engines import LOAD_ERRORS, names as engine_names  # noqa: E402
from me.runner import run                     # noqa: E402
from me.scanner import detect_engine          # noqa: E402
from me.tools import paths as tool_paths      # noqa: E402

# 交互菜单：显示名 -> 动作。动作是分类集合，或下面这些特殊字符串。
MENU_TARGETS = (
    ("提取图片", {"image"}),
    ("提取音频", {"audio"}),
    ("提取文本", {"text"}),
    ("提取字体", {"font"}),
    ("提取全部", None),
    ("识别详情", "detail"),
    ("指定引擎", "engine"),
    ("更换路径", "path"),
    ("退出", "exit"),
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
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
    ap.add_argument("--detect", action="store_true", help="只识别引擎，不提取")
    ap.add_argument("--tools", action="store_true", help="检查外部 CLI 是否齐全")
    ap.add_argument("--engines", action="store_true", help="列出已加载的引擎模块")
    ap.add_argument("--setup", action="store_true",
                    help="查看 setup.ini")
    ap.add_argument("-y", "--yes", action="store_true", help="不询问，直接执行")
    args = ap.parse_args(argv)

    ui.banner()

    # setup.ini 坏掉时提醒一句。以前这里会静默退回默认值，
    # 用户改了保存路径却没生效、还不知道为什么。
    _setup_err = setup_ini.load()["error"]
    if _setup_err and not args.setup:
        ui.warn(_setup_err)
        ui.detail("详情见 --setup")

    if args.tools:
        return cmd_tools()
    if args.engines:
        return cmd_engines()
    if args.setup:
        return cmd_setup()
    if args.detect:
        return cmd_detect(args.target)

    categories = _parse_only(args.only)
    if categories is _BAD_ONLY:
        return 2

    if args.target:
        return cmd_extract(Path(args.target), args, categories)

    return interactive(args)


# ------------------------------------------------------------------ 子命令
def cmd_tools() -> int:
    ui.step("外部工具自检")
    missing = 0
    for key, path, desc, expect in tool_paths.status():
        if path:
            ui.ok(f"{key:<12} {path}")
        else:
            missing += 1
            ui.warn(f"{key:<12} 缺失（期望 {expect}）")
            ui.detail(desc)
    if missing:
        ui.sep()
        ui.info(f"缺失 {missing} 个工具。{tool_paths.repair_hint()}")
    else:
        ui.ok("外部工具齐全")
    ui.sep()
    ui.info(f"运行环境: {_env_desc()}")
    return 0


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
    if LOAD_ERRORS:
        ui.sep()
        ui.warn("以下模块加载失败:")
        for name, err in LOAD_ERRORS.items():
            ui.detail(f"{name}: {err}")
    return 0


def cmd_setup() -> int:
    """显示 setup.ini 的位置和当前设置。"""
    ui.step("配置（setup.ini）")
    path = setup_ini.setup_path()
    cfg = setup_ini.load()

    ui.ok(f"文件位置: {path}")
    if not path.exists():
        ui.warn("文件不存在，写入失败，使用内置默认值")
    if cfg["error"]:
        ui.warn(f"配置异常: {cfg['error']}")

    ui.sep()
    ui.info(f"保存路径: {cfg[setup_ini.KEY_PATH]}")
    ui.detail(f"实际位置: {setup_ini.output_root()}")
    return 0


def cmd_detect(target) -> int:
    """只识别不提取。"""
    if not target:
        ui.error("--detect 需要指定路径")
        return 2
    path = Path(target).expanduser()
    if not path.exists():
        ui.error(f"路径不存在: {path}")
        return 1
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
    return 0


def cmd_extract(target: Path, args, categories) -> int:
    if not target.exists():
        ui.error(f"路径不存在: {target}")
        return 1
    res = run(target, out_dir=args.out, categories=categories,
              deep=not args.no_deep, engine_key=args.engine,
              keep_work=args.keep_work, progress=ui.progress)
    if res.written == 0:
        return 3
    return 0


# ------------------------------------------------------------------ 交互
def interactive(args) -> int:
    _warn_missing_tools()

    target = _ask_target()
    if target is None:
        return 1
    det = _detect_and_show(target)
    forced = args.engine

    while True:
        ui.sep()
        if forced:
            ui.info(f"当前指定引擎: {forced}")
        elif det.is_known:
            ui.info(f"当前引擎: {det.name}")
        else:
            ui.info("当前引擎: 未识别")

        ui.info(f"保存到: {setup_ini.describe()}")

        idx = ui.menu("请选择功能:", MENU_TARGETS)
        _label, action = MENU_TARGETS[idx]

        if action == "exit":
            return 0

        if action == "detail":
            _show_detail(det)
            continue

        if action == "engine":
            forced = _ask_engine(forced)
            continue

        if action == "path":
            new_target = _ask_target()
            if new_target is None:
                continue
            target = new_target
            forced = args.engine
            det = _detect_and_show(target)
            continue

        engine_key = forced or (det.engine if det.is_known else None)
        res = run(target, out_dir=args.out, categories=action,
                  deep=not args.no_deep, engine_key=engine_key,
                  keep_work=args.keep_work, progress=ui.progress)
        if res.written == 0:
            ui.warn("未提取到内容。")


def _ask_target():
    """问用户要路径，返回 Path 或 None。"""
    # ui.ask 已经去过引号/空白/BOM，这里再兜一次底（用户可能从别处粘进来）
    raw = ui.clean_input(ui.ask("请输入游戏路径（文件或目录）"))
    if not raw:
        ui.error("未输入路径")
        return None
    target = Path(raw)
    if not target.exists():
        ui.error(f"路径不存在: {target}")
        return None
    return target


def _detect_and_show(target):
    """识别并打印结果。"""
    ui.sep()
    ui.step("识别引擎 ...")
    det = detect_engine(target)
    ui.ok(f"识别结果: {det.describe()}")
    for ev in det.evidence[:6]:
        ui.detail(ev)
    if det.truncated:
        ui.warn("扫描条目超上限，已截断；特征可能漏检")
    if not det.is_known:
        ui.warn("未能识别引擎，退回通用收集。")
    return det


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


def _ask_engine(current):
    """让用户手动指定引擎。"""
    from me.engines.menu import build_engine_menu
    items = build_engine_menu()
    ui.sep()
    idx = ui.menu("选择引擎:", [label for label, _key in items])
    label, key = items[idx]
    if key:
        ui.ok(f"已指定引擎: {label}")
    else:
        ui.ok("已切换为自动识别")
    return key


def _warn_missing_tools() -> None:
    """启动时提示缺哪些外部工具，缺失不影响可跑的引擎。"""
    missing = [key for key, path, _desc, _exp in tool_paths.status() if not path]
    if missing:
        ui.warn(f"外部工具缺失: {', '.join(missing)}")
        ui.detail(tool_paths.repair_hint())


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


def _entry() -> int:
    """真正的入口：初始化控制台 → 跑主流程 → 收尾。

    收尾放在 finally 里，这样 argparse 报错退出、用户 Ctrl+C 中断
    这些路径也能正常还原代码页、并在双击启动时把窗口留住。
    """
    console.setup()
    # 让 setup.ini 一开始就出现在程序旁边，用户不用先去别处找格式
    setup_ini.ensure()
    try:
        return main()
    except KeyboardInterrupt:
        print()
        ui.warn("已中断")
        return 130
    finally:
        console.pause_if_needed()
        console.restore()


if __name__ == "__main__":
    raise SystemExit(_entry())
