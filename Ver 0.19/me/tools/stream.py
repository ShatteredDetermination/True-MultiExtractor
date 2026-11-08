# -*- coding: utf-8 -*-
"""执行外部命令，并把它的输出**实时**转发给用户。

为什么单独做这个模块
--------------------
脱壳（evbunpack）和调用 AssetStudio / repak 的时候，工具动辄几十秒不吐一个字。
原来的实现是把子进程输出全部缓冲起来、等结束才拿，于是界面长时间完全静止，
用户会以为卡死了然后去按 Ctrl+C —— 这不是猜测，实测确实发生了。

这个模块做四件事：
  1. 子进程输出逐行实时打印（带缩进和前缀，标明是外部工具在说话）
  2. **把 \\r 也当成行分隔符**。很多工具（evbunpack、repak）用 \\r 原地刷进度条，
     只按 \\n 读的话这些进度会一直攒在缓冲区里，等结束才一股脑吐出来，
     实时性完全没了。
  3. 连续 N 秒没有新输出就打一条心跳，带上已用时间
  4. Ctrl+C 时先杀掉子进程再抛出，不留孤儿进程

非交互场景（自测、脚本调用）传 echo=False，行为退回成「只收集不打印」。
"""

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from me import events
from me.config import TOOL_TIMEOUT

# 子进程多久没输出就打一次心跳（秒）
HEARTBEAT_SECONDS = 10.0

# 非 TTY（输出被重定向到文件）时，进度式输出的最小间隔，避免刷出几千行
PROGRESS_MIN_INTERVAL = 1.0

# 队列里用这个哨兵表示「这一轮没等到东西」，好和真正的 EOF(None) 区分开
_TICK = object()

_IS_TTY = sys.stdout.isatty()

# 用来判断「这行是不是进度条式输出」的特征
_PROGRESS_HINTS = ("total=", "read=", "Writing File", "Saving PE", "\b")


class LiveResult:
    """一次实时执行的结果。"""

    def __init__(self, argv, code: int, lines: List[str], elapsed: float,
                 interrupted: bool = False):
        self.argv = argv
        self.code = code
        self.lines = lines
        self.elapsed = elapsed
        self.interrupted = interrupted
        self.out = "\n".join(lines)
        self.err = ""

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.interrupted

    def tail(self, n: int = 10) -> str:
        return "\n".join(self.lines[-n:])


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _looks_like_progress(text: str) -> bool:
    return any(h in text for h in _PROGRESS_HINTS)


def run_live(argv: Sequence, cwd=None, env_extra: Optional[dict] = None,
             timeout: int = TOOL_TIMEOUT, echo: bool = True,
             prefix: str = "  | ", heartbeat: float = HEARTBEAT_SECONDS,
             banner: str = "", on_line: Optional[Callable[[str], None]] = None,
             max_lines: int = 20000) -> LiveResult:
    """跑一条外部命令，输出实时打印。

    echo=False 时不打印任何东西，只把输出收集起来（给自测/静默模式用）。
    返回 LiveResult，字段和 me.tools.proc.RunResult 兼容（code / ok / out / tail）。
    """
    argv = [str(a) for a in argv]
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})

    if echo and banner:
        print(f"      {banner}")
    if echo:
        # 完整命令行也打出来，出问题时用户能自己复制去跑
        print(f"      $ {' '.join(_quote(a) for a in argv)}")

    t0 = time.time()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
    except FileNotFoundError as exc:
        return LiveResult(argv, 127, [f"命令不存在: {exc}"], 0.0)

    lines: List[str] = []
    last_progress = ""
    q: "queue.Queue" = queue.Queue()

    def _reader():
        """按 \\r 和 \\n 双分隔符切分，保证进度条也是实时的。"""
        buf = b""
        try:
            while True:
                chunk = proc.stdout.read(8192)
                if not chunk:
                    break
                buf += chunk
                while True:
                    i_n = buf.find(b"\n")
                    i_r = buf.find(b"\r")
                    if i_n < 0 and i_r < 0:
                        break
                    idx = min(x for x in (i_n, i_r) if x >= 0)
                    piece = buf[:idx]
                    sep = buf[idx:idx + 1]
                    buf = buf[idx + 1:]
                    q.put((piece, sep))
            if buf:
                q.put((buf, b"\n"))
        except Exception:  # noqa: BLE001 - 读流失败不该把主流程带崩
            pass
        finally:
            q.put(None)

    threading.Thread(target=_reader, daemon=True).start()

    last_output = time.time()
    last_progress_print = 0.0

    try:
        while True:
            try:
                item = q.get(timeout=0.5)
            except queue.Empty:
                item = _TICK

            if item is None:
                break

            if item is _TICK:
                if proc.poll() is not None and q.empty():
                    break
                if events.enabled():
                    events.check_cancel()
                now = time.time()
                if echo and heartbeat and now - last_output >= heartbeat:
                    last_output = now
                    _clear_line_if_tty()
                    print(f"      ... 已运行 {now - t0:.0f}s，无新输出")
                if now - t0 > timeout:
                    proc.kill()
                    lines.append(f"命令超时（>{timeout}s），已终止")
                    return LiveResult(argv, 124, lines, now - t0)
                continue

            raw, sep = item
            last_output = time.time()
            text = _decode(raw).strip("\r\n").rstrip()
            if not text:
                continue

            is_progress = sep == b"\r" or _looks_like_progress(text)

            if is_progress:
                # 进度式输出：只留最后一条进结果，免得把 lines 撑爆
                last_progress = text
                if echo:
                    now = time.time()
                    if _IS_TTY:
                        # 真控制台：原地刷新，不换行
                        line = f"{prefix}{text}"
                        sys.stdout.write("\r" + line[:200].ljust(120))
                        sys.stdout.flush()
                    elif now - last_progress_print >= PROGRESS_MIN_INTERVAL:
                        # 重定向到文件：限流，否则几千行进度会把日志淹掉
                        last_progress_print = now
                        print(f"{prefix}{text}")
                if on_line:
                    on_line(text)
                continue

            # 普通输出行
            _clear_line_if_tty()
            if len(lines) < max_lines:
                lines.append(text)
            if echo:
                show = text if len(text) <= 300 else text[:300] + " ..."
                print(f"{prefix}{show}")
            if on_line:
                on_line(text)
    except events.Cancelled:
        # 取消也要先把子进程收掉，否则会留一个孤儿进程在后台跑
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        _clear_line_if_tty()
        raise
    except KeyboardInterrupt:
        # 先把子进程收掉再往外抛，否则会留一个孤儿进程在后台跑
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        _clear_line_if_tty()
        raise
    finally:
        try:
            if proc.poll() is None:
                proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass

    _clear_line_if_tty()
    if last_progress:
        # 进度条的最终状态保留一条，排查问题时有用
        lines.append(last_progress)

    elapsed = time.time() - t0
    if echo:
        print(f"      {Path(argv[0]).name} 结束，退出码 {proc.returncode}，"
              f"耗时 {elapsed:.1f}s")
    return LiveResult(argv, proc.returncode or 0, lines, elapsed)


def _clear_line_if_tty() -> None:
    """进度条是原地刷新的，打普通输出前先把那一行擦掉。"""
    if _IS_TTY:
        sys.stdout.write("\r" + " " * 120 + "\r")
        sys.stdout.flush()


def _quote(arg: str) -> str:
    """命令行回显时给带空格的参数补引号。"""
    if " " in arg and not (arg.startswith('"') and arg.endswith('"')):
        return f'"{arg}"'
    return arg
