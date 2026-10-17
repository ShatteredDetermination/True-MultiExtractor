# -*- coding: utf-8 -*-
"""控制台输出。

只用标准库，避免给发布包增加依赖。所有界面文字集中在这里，改样式不用翻别的文件。
"""

import sys
from typing import Optional

from me import events
from me.config import APP_NAME, AUTHOR, VERSION

# Windows 控制台默认可能是 GBK，强制 UTF-8 免得中文乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

_SEP = "=" * 78


def banner() -> None:
    """打印程序横幅。"""
    print(_SEP)
    print(f"  {APP_NAME} v{VERSION}   By {AUTHOR}")
    print(_SEP)


def sep() -> None:
    print("-" * 78)


def info(msg: str) -> None:
    events.emit("note", level="info", text=msg)
    print(f"  {msg}")


def step(msg: str) -> None:
    """一个主要步骤。"""
    events.emit("note", level="step", text=msg)
    print(f"[i] {msg}")


def ok(msg: str) -> None:
    events.emit("note", level="ok", text=msg)
    print(f"[ok] {msg}")


def warn(msg: str) -> None:
    events.emit("note", level="warn", text=msg)
    print(f"[warn] {msg}")


def error(msg: str) -> None:
    events.emit("note", level="err", text=msg)
    print(f"[err] {msg}")


def detail(msg: str) -> None:
    """缩进的细节信息。"""
    events.emit("note", level="detail", text=msg)
    print(f"      {msg}")


# 用户粘贴路径时经常带上的隐形字符。
# \ufeff 是 UTF-8 BOM —— 从某些编辑器/文本文件里复制路径会带上它，
# 而 Python 的 str.strip() 不认为它是空白，结果路径就「不存在」了。
# 实测：通过管道把路径喂进来时也遇到过。
_INVISIBLE = "\ufeff\u200b\u200c\u200d\u200e\u200f\u2060"


def clean_input(text: str) -> str:
    """去掉首尾空白、引号和隐形字符。"""
    if not text:
        return ""
    text = text.strip()
    # 引号可能和隐形字符交替出现，所以循环处理
    for _ in range(4):
        before = text
        text = text.strip().strip(_INVISIBLE).strip()
        text = text.strip('"').strip("'").strip()
        if text == before:
            break
    return text


def ask(prompt: str, default: Optional[str] = None) -> str:
    """读一行输入。"""
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"{prompt}{suffix}: ")
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    return clean_input(raw) or (default or "")


# 斜杠指令的钩子：menu() 里遇到 `/xxx` 就交给它，不当作非法输入。
# 由 main.interactive 装一次；没装时 `/xxx` 仍然按非法输入处理。
_COMMAND_HOOK = None


def set_command_hook(fn) -> None:
    global _COMMAND_HOOK
    _COMMAND_HOOK = fn


def command(text: str):
    """把一行输入交给指令钩子。返回 None=继续，'quit'=退出。"""
    if _COMMAND_HOOK is None:
        return None
    return _COMMAND_HOOK(text)


def menu(title: str, options: list) -> int:
    """显示菜单并返回用户选择的索引。

    options: [(显示文字, 说明或 None), ...]
    返回的永远是 0..len(options)-1 —— 越界/非数字会重问，不会返回 -1；
    输入流结束（管道喂完）走 SystemExit(0)。
    """
    while True:
        print(f"\n{title}")
        for i, item in enumerate(options):
            label = item[0] if isinstance(item, (tuple, list)) else item
            print(f"  [{i}] {label}")
        raw = ask("请选择")
        # 斜杠指令：任何菜单里都能用，跑完重新画一遍菜单
        if raw.startswith("/"):
            if command(raw) == "quit":
                raise SystemExit(0)
            continue
        if raw.isdigit() and 0 <= int(raw) < len(options):
            return int(raw)
        error("无效输入")


# 输出被重定向（写日志、管道）时 \r 不生效，改成按百分比打行，避免刷屏
_IS_TTY = sys.stdout.isatty()
_progress_pct = {"last": -1}
# 上一次画出来的可见长度：用来把残留字符擦干净，也用来判断有没有换行
_progress_len = {"n": 0}


def _term_width() -> int:
    """当前控制台宽度。拿不到就按 80，且至少留 40 给内容。"""
    try:
        import shutil
        cols = shutil.get_terminal_size((80, 24)).columns
    except (OSError, ValueError):
        cols = 80
    return max(40, cols)


def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def _fit(text: str, width: int) -> str:
    """压到一行以内。

    这是刷屏的根源：原来固定补到 120 字符，控制台比 120 窄时每写一次就折行，
    \r 只回到**折行后**那一行的行首，于是一个进度条变成一屏幕。
    """
    limit = width - 1
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[:limit - 1] + "…"


def progress(done: int, total: int, label: str = "") -> None:
    """各层报进来的读数（引擎、子解析器都调这个）。

    不直接渲染：先过全局账本折算成单调的全局读数，见 me/progress.py。
    某一层报的是它自己那一段的 done/total，直接透出去百分比会往回跳。
    """
    from me.progress import ledger
    ledger.report(done, total, label)


def render_progress(done: int, total: int, label: str = "") -> None:
    """账本折算后的全局读数 —— 真正画进度条的地方。

    TTY 下**永远只占一行**：\r 回到行首、按终端宽度截断、用上次的长度擦残影。
    total<=0 时只显示已处理计数。
    """
    events.progress("extract", done, total, label)
    width = _term_width()

    if total <= 0:
        if _IS_TTY:
            line = _fit(f"\r      {done} {label}", width)
            _draw_line(line)
        return

    pct = done * 100 // total

    if not _IS_TTY:
        # 每 5% 打一行；完成时一定要打
        if done < total and pct - _progress_pct["last"] < 5:
            return
        _progress_pct["last"] = pct
        print(f"      {pct:3d}%  {done}/{total} {label}")
        return

    bar_len = 20 if width >= 72 else 10
    filled = bar_len * done // max(total, 1)
    bar = "#" * filled + "." * (bar_len - filled)
    size = f"{_human_bytes(done)}/{_human_bytes(total)}"
    line = f"\r      [{bar}] {pct:3d}%  {size}  {label}"
    _draw_line(_fit(line, width))
    if done >= total:
        sys.stdout.write("\n")
        sys.stdout.flush()
        _progress_len["n"] = 0


def _draw_line(line: str) -> None:
    """写一行「原地更新」的进度：补空格擦掉上次的残留，不换行。"""
    pad = max(0, _progress_len["n"] - len(line))
    sys.stdout.write(line + " " * pad)
    sys.stdout.flush()
    _progress_len["n"] = len(line)


def clear_progress() -> None:
    _progress_pct["last"] = -1
    if _IS_TTY:
        n = max(_progress_len["n"], 1)
        sys.stdout.write("\r" + " " * n + "\r")
        sys.stdout.flush()
        _progress_len["n"] = 0


# 账本只算数，渲染在这儿接上。放文件末尾，确保 render_progress 已定义。
from me.progress import ledger as _ledger      # noqa: E402

_ledger.bind(render_progress)
