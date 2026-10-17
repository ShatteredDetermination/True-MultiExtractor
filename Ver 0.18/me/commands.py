# -*- coding: utf-8 -*-
"""斜杠指令（`/settings` 等）。

用户的用法很直接：在「请输入游戏路径」那里敲 `/settings` 就进设置。
所以指令必须**在两处都认**：

    * 路径输入那一行（还没进主菜单，甚至还没选目标）；
    * 任何菜单的「请选择」那一行（分析前 / 分析后 / 子菜单）。

设计取舍
--------
* 指令只做「交互层」的事：改设置、看状态、退出。真正的功能一律回到
  `main.py` 的 `cmd_*` 上，不另写一套逻辑 —— 否则菜单和命令行迟早分叉。
* 每个指令返回 `None`（继续）或 `"quit"`（退出菜单）。
* 设置项里能持久化的只有两样：`setup.ini` 的保存路径、`toolsdir.ini` 的
  工具目录；其余是**本次会话**的开关（与命令行参数对应）。
"""

from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from me import config, setup_ini, ui
from me.tools import paths as tool_paths

HELP_LINE = "指令："

# 指令表：名字 -> (一行说明, 处理函数的属性名)
COMMANDS: Dict[str, Tuple[str, str]] = {
    "help": ("列出指令", "cmd_help"),
    "settings": ("设置", "cmd_settings"),
    "tools": ("外部工具状态", "cmd_tools"),
    "channels": ("拆包通道", "cmd_channels"),
    "setup": ("setup.ini 位置与值", "cmd_setup"),
    "update": ("检查更新", "cmd_update"),
    "quit": ("退出", "cmd_quit"),
}

ALIASES = {
    "s": "settings", "set": "settings", "配置": "settings", "设置": "settings",
    "?": "help", "h": "help", "帮助": "help",
    "exit": "quit", "q": "quit", "退出": "quit",
}


class Context:
    """指令需要的东西：会话（可能还没建）、参数、以及一次性的重绘标记。"""

    def __init__(self, args=None, session=None):
        self.args = args
        self.session = session
        self.dirty = False          # 改过开关，调用方据此重绘菜单

    def set_session(self, session) -> None:
        self.session = session


def parse(text: str) -> Optional[Tuple[str, List[str]]]:
    """`/settings x y` -> ("settings", ["x","y"])；不是指令返回 None。"""
    if not text:
        return None
    raw = text.strip()
    if not raw.startswith("/"):
        return None
    body = raw[1:].strip()
    if not body:
        return None
    parts = body.split()
    name = parts[0].lower()
    name = ALIASES.get(name, name)
    return name, parts[1:]


def is_command(text: str) -> bool:
    return parse(text) is not None


def handle(text: str, ctx: Optional[Context] = None) -> Optional[str]:
    """跑一条指令。返回 None（继续）或 "quit"。

    认不出的指令只提示，不报错 —— 用户手滑打错一个字母不该被当成路径。
    """
    parsed = parse(text)
    if parsed is None:
        return None
    name, argv = parsed
    ctx = ctx or Context()

    entry = COMMANDS.get(name)
    if entry is None:
        ui.warn(f"没有这个指令: /{name}")
        _show_commands()
        return None

    fn = globals().get(entry[1])
    if fn is None:                      # 理论上不会发生
        ui.warn(f"指令 /{name} 还没接上")
        return None
    try:
        return fn(ctx, argv)
    except (EOFError, KeyboardInterrupt):
        # 用户按 Ctrl+C / 输入流结束：当成「退出指令」，别把菜单炸掉
        return "quit"


# ---------------------------------------------------------------- 各指令
def _show_commands() -> None:
    ui.info(HELP_LINE)
    for name, (desc, _fn) in COMMANDS.items():
        ui.detail(f"/{name:<10} {desc}")
    ui.detail("别名: /h /? /s /q /帮助 /设置 /退出")


def cmd_help(_ctx: Context, _argv: List[str]) -> None:
    _show_commands()
    return None


def cmd_quit(_ctx: Context, _argv: List[str]) -> str:
    ui.info("再见")
    return "quit"


def cmd_tools(_ctx: Context, _argv: List[str]) -> None:
    import main
    main.cmd_tools()
    return None


def cmd_channels(_ctx: Context, _argv: List[str]) -> None:
    import main
    main.cmd_channels()
    return None


def cmd_setup(_ctx: Context, _argv: List[str]) -> None:
    import main
    main.cmd_setup()
    return None


def cmd_update(ctx: Context, _argv: List[str]) -> None:
    import main
    main.cmd_update("", ctx.args)
    return None


# ---------------------------------------------------------------- 设置
def _onoff(v: bool) -> str:
    return "[开]" if v else "[关]"


def _session_flag(ctx: Context, name: str) -> Tuple[bool, str]:
    """读一个会话开关：优先会话，其次命令行参数。返回 (值, 来源)。"""
    if ctx.session is not None and hasattr(ctx.session, name):
        return bool(getattr(ctx.session, name)), "会话"
    if ctx.args is not None and hasattr(ctx.args, name):
        return bool(getattr(ctx.args, name)), "参数"
    return False, "默认"


def _set_session_flag(ctx: Context, name: str, value: bool) -> None:
    """同时改会话与参数 —— 参数是会话的种子，两边不能打架。"""
    if ctx.args is not None and hasattr(ctx.args, name):
        setattr(ctx.args, name, value)
    if ctx.session is not None and hasattr(ctx.session, name):
        setattr(ctx.session, name, value)
    ctx.dirty = True


def tools_summary() -> str:
    """外部工具的状态：`3/4 已安装（缺 assetstudio）`。"""
    try:
        installed = tool_paths.installed()
        missing = tool_paths.missing()
    except Exception:                       # noqa: BLE001 清单读不出来也不该炸
        return "状态未知"
    total = len(installed) + len(missing)
    if total == 0:
        return "清单为空"
    text = f"{len(installed)}/{total} 已安装"
    if missing:
        text += "（缺 " + ", ".join(missing) + "）"
    return text


# 标签按「占 7 个汉字宽」对齐：全角空格补位，中英混排才不会左右跳。
_LABEL_WIDTH = 7


def _row(label: str, value: str) -> str:
    pad = "　" * max(0, _LABEL_WIDTH - len(label))
    return f"{label}{pad} {value}"


def _settings_rows(ctx: Context) -> List[str]:
    exp, _ = _session_flag(ctx, "experimental")
    sel, _ = _session_flag(ctx, "want_self")
    nodeep, _ = _session_flag(ctx, "no_deep")
    keep, _ = _session_flag(ctx, "keep_work")
    od = tool_paths.override_dir()
    tools = str(od) if od is not None else str(Path(config.ROOT) / "tools")
    return [
        _row("输出目录", str(setup_ini.output_root())),
        _row("工具根目录", tools),
        _row("外部工具", tools_summary()),
        _row("实验性功能", _onoff(exp)),
        _row("自研核心", _onoff(sel)),
        _row("不解容器", _onoff(nodeep)),
        _row("保留中间产物", _onoff(keep)),
    ]


def render_settings(ctx: Context) -> None:
    rows = _settings_rows(ctx)
    ui.sep()
    ui.info("[设置]")
    for i, row in enumerate(rows, 1):
        ui.detail(f"  [{i}] {row}")
    ui.detail("  [8] 检查外部工具")
    ui.detail("  [9] 下载缺失工具")
    ui.detail("  [10] 重置工具根目录")
    ui.detail("  [0] 返回")


def cmd_settings(ctx: Context, _argv: List[str]) -> None:
    """设置菜单。0 返回，其余按提示改。"""
    while True:
        render_settings(ctx)
        raw = ui.clean_input(ui.ask("设置项"))
        if raw in ("", "0"):
            return None
        if raw == "1":
            _set_output_path()
        elif raw == "2":
            _set_tools_dir()
        elif raw == "3":
            _show_tool_report()
        elif raw == "4":
            _toggle(ctx, "experimental", "实验性功能")
        elif raw == "5":
            _toggle(ctx, "want_self", "自研核心")
        elif raw == "6":
            _toggle(ctx, "no_deep", "不解容器")
        elif raw == "7":
            _toggle(ctx, "keep_work", "保留中间产物")
        elif raw == "8":
            _show_tool_report()
        elif raw == "9":
            _download_tools(ctx)
        elif raw == "10":
            tool_paths.clear_override_dir()
            ui.ok("工具根目录已重置")
        else:
            ui.warn("范围 0-10")


def _toggle(ctx: Context, name: str, label: str) -> None:
    cur, _src = _session_flag(ctx, name)
    _set_session_flag(ctx, name, not cur)
    ui.ok(f"{label} {_onoff(not cur)}")


def _show_tool_report() -> None:
    """完整的工具自检：清单来源、每个工具的期望路径、缺了怎么补。

    直接复用 `main.cmd_tools()` —— 菜单与命令行必须是同一条路，
    否则迟早出现「菜单说齐了、命令行说缺」这种对不上的事。
    """
    import main
    main.cmd_tools()


def _download_tools(_ctx: Context) -> None:
    """自动下载缺失的工具。走软键 1 同一条路（含全量包询问）。

    下完把结果落回面板摘要 —— 用户不用再按一次 3/8 才知道成没成。
    """
    from me.tools import softkey

    before = tools_summary()
    softkey.download(ui)
    after = tools_summary()
    if after != before:
        ui.info(f"外部工具: {before} -> {after}")


def _set_output_path() -> None:
    ui.info(f"输出目录: {setup_ini.output_root()}")
    raw = ui.clean_input(ui.ask("输出目录"))
    if not raw:
        return
    try:
        p = setup_ini.set_output(raw)
    except OSError as exc:
        ui.error(f"写 setup.ini 失败: {exc}")
        return
    ui.ok(f"输出目录: {setup_ini.output_root()}")


def _set_tools_dir() -> None:
    od = tool_paths.override_dir()
    ui.info(f"工具根目录: {od if od is not None else '默认'}")
    raw = ui.clean_input(ui.ask("工具根目录"))
    if not raw:
        return
    d = Path(raw)
    if not d.is_dir():
        ui.error(f"目录不存在: {d}")
        return
    tool_paths.set_override_dir(d)
    if tool_paths.any_installed():
        ui.ok(f"工具根目录: {d}")
        ui.detail("已找到: " + ", ".join(tool_paths.installed()))
    else:
        ui.warn(f"未找到工具: {d}")
