# -*- coding: utf-8 -*-
"""子进程调用封装。

踩过的坑：
  - 有些 .NET CLI（比如 CTFAK）启动时会访问 Console.WindowWidth，
    在没有真实控制台的环境下会直接抛 IOException 崩掉。
    解决办法是用 `conhost.exe --headless <cmd>` 给它挂一个控制台。
  - 输出编码五花八门，这里统一按 utf-8 解，失败再退回 gbk。
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from me.config import TOOL_TIMEOUT


class RunResult:
    """一次外部命令的结果。"""

    def __init__(self, argv, code: int, out: str, err: str):
        self.argv = argv
        self.code = code
        self.out = out
        self.err = err

    @property
    def ok(self) -> bool:
        return self.code == 0

    def tail(self, n: int = 20) -> str:
        lines = (self.out or "").splitlines() + (self.err or "").splitlines()
        return "\n".join(lines[-n:])


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def run(argv: Sequence, cwd=None, timeout: int = TOOL_TIMEOUT,
        console: bool = False, log: Optional[Path] = None,
        env_extra: Optional[dict] = None) -> RunResult:
    """执行外部命令。

    console=True 时用 conhost --headless 包一层，
    让那些依赖控制台的 CLI 不会崩。
    env_extra 用来给子进程加环境变量（比如给外部导出器传输出目录）。
    """
    argv = [str(a) for a in argv]
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})

    final: List[str] = list(argv)
    if console and sys.platform == "win32":
        final = ["conhost.exe", "--headless"] + final

    if log:
        log = Path(log)
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write("\n$ " + " ".join(final) + "\n")
        stdout_target = open(log, "ab")
        stderr_target = subprocess.STDOUT
        close_after = True
    else:
        stdout_target = subprocess.PIPE
        stderr_target = subprocess.PIPE
        close_after = False

    try:
        proc = subprocess.run(
            final,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=stdout_target,
            stderr=stderr_target,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return RunResult(final, 127, "", f"命令不存在: {exc}")
    except subprocess.TimeoutExpired:
        return RunResult(final, 124, "", f"命令超时（>{timeout}s）")
    finally:
        if close_after:
            stdout_target.close()

    if log:
        return RunResult(final, proc.returncode, f"(输出写入 {log})", "")

    return RunResult(final, proc.returncode,
                     _decode(proc.stdout or b""), _decode(proc.stderr or b""))
