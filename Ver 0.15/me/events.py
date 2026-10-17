# -*- coding: utf-8 -*-
"""机器可读事件流（NDJSON），给 GUI 壳调用。

命令行加 `--json` 打开。打开之后：

* stdout 只写事件流：一行一个 JSON 对象，每行都 flush，随时可读
* 程序里所有人读的文字（ui.*、进度条、外部 CLI 的实时输出）改走 stderr，
  这样 stdout 永远是干净的、可逐行 json.loads 的
* 自动设 ME_NO_PAUSE=1，双击暂停不会触发，免得 GUI 卡住

事件 schema 见 GUI-API.md；`v` 是 schema 版本，字段只增不改。
不开 `--json` 时本模块全部是空操作，CLI 行为与之前逐字节一致。
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA = 1

_enabled = False
_channel = None                 # 真正的 stdout，事件只往这里写
_cancel_file: Optional[Path] = None
_t0 = time.time()
_seq = 0
_last_progress = 0.0

# 进度事件的节流间隔（秒）。GUI 不需要每个文件一条。
PROGRESS_INTERVAL = 0.15


class Cancelled(BaseException):
    """GUI 写了取消文件。

    刻意继承 BaseException：各引擎里到处是 `except Exception`，
    取消不该被那些兜底吞掉。
    """


def enable(cancel_file: Optional[str] = None) -> None:
    """打开事件流。必须在任何人类输出之前调用。"""
    global _enabled, _channel, _cancel_file
    if _enabled:
        return
    _enabled = True
    _channel = sys.stdout
    try:
        _channel.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    # 人读的输出整体改道 stderr
    sys.stdout = sys.stderr
    # GUI 调用时不要停在那儿等按键
    os.environ["ME_NO_PAUSE"] = "1"
    if cancel_file:
        _cancel_file = Path(cancel_file).expanduser()


def enabled() -> bool:
    return _enabled


def emit(t: str, **fields: Any) -> None:
    """写一条事件。未开启时是空操作。"""
    if not _enabled:
        return
    global _seq
    _seq += 1
    obj: Dict[str, Any] = {"t": t, "seq": _seq,
                           "ms": int((time.time() - _t0) * 1000)}
    obj.update(fields)
    try:
        _channel.write(json.dumps(obj, ensure_ascii=False,
                                  separators=(",", ":")) + "\n")
        _channel.flush()
    except Exception:  # noqa: BLE001 - GUI 那边断了不该把提取带崩
        pass


def progress(phase: str, done: int, total: int, label: str = "") -> None:
    """进度事件，带节流；完成时必须发一条。"""
    if not _enabled:
        return
    global _last_progress
    done_last = total > 0 and done >= total
    now = time.time()
    if not done_last and now - _last_progress < PROGRESS_INTERVAL:
        return
    _last_progress = now
    emit("progress", phase=phase, done=done, total=total, label=label)


_started = False
_finished = False


def begin(**fields) -> None:
    """写 start 事件，整个进程只写一次。

    参数解析失败时 main() 还没跑到，由入口补一条，保证事件流
    永远以 start 开头。
    """
    global _started
    if _started:
        return
    _started = True
    emit("start", v=SCHEMA, **fields)


def finish(code: int) -> None:
    """写 end 事件，整个进程只写一次。"""
    global _finished
    if _finished:
        return
    _finished = True
    emit("end", exit=code)


def check_cancel() -> None:
    """取消文件出现就抛 Cancelled。"""
    if _cancel_file is not None and _cancel_file.exists():
        raise Cancelled()


def cancel_path() -> Optional[str]:
    return str(_cancel_file) if _cancel_file else None
