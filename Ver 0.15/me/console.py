# -*- coding: utf-8 -*-
"""控制台细节：代码页、以及「双击启动时别让窗口一闪而过」。

打包成 exe 后有两个只有冻结版才会遇到、源码版被 run.bat 挡掉的问题：

1. **双击 exe 时控制台是新建的**，程序一退出窗口立刻消失，用户什么都来不及看
   （run.bat 里那句 `pause >nul` 就是干这个的）。这里用 GetConsoleProcessList
   判断「这个控制台是不是只有我们自己」——只有双击/独立启动才会是 1，
   从已有的 cmd 窗口里跑、或者管道调用都会 ≥2，那种情况不该暂停。

2. **控制台代码页**。Python 3.6+ 往真控制台写字走的是 WriteConsoleW，
   本身不受代码页影响；但控制台里跑的其他程序（以及用户自己的后续命令）
   仍然按当前代码页解释字节，所以这里只在「输出确实是控制台」时把它切成
   65001 与 UTF-8 对齐，并在退出时**还原**，免得污染用户那个窗口。

非 Windows 平台上一律是空操作。
"""

import os
import sys

_IS_WIN = sys.platform == "win32"

# 保存进入时控制台的代码页，退出时还原。None 表示没改过。
_SAVED_CP = None

# 设成 1 就完全不要暂停（自动化调用用得上）
_NO_PAUSE_ENV = "ME_NO_PAUSE"

_CODEPAGE_UTF8 = 65001


def _kernel32():
    import ctypes
    return ctypes.windll.kernel32


def _console_stream() -> bool:
    """stdout 是不是真的连着控制台（而不是被重定向到文件/管道）。"""
    try:
        return bool(sys.stdout.isatty())
    except Exception:  # noqa: BLE001
        return False


def setup() -> None:
    """进入时调用：需要的话把控制台代码页切到 UTF-8。"""
    global _SAVED_CP
    if not _IS_WIN or _SAVED_CP is not None:
        return
    # 输出被重定向时不要动代码页，那是用户 shell 的设置
    if not _console_stream():
        return
    try:
        k32 = _kernel32()
        in_cp = k32.GetConsoleCP()
        out_cp = k32.GetConsoleOutputCP()
        if in_cp == _CODEPAGE_UTF8 and out_cp == _CODEPAGE_UTF8:
            return
        _SAVED_CP = (in_cp, out_cp)
        k32.SetConsoleCP(_CODEPAGE_UTF8)
        k32.SetConsoleOutputCP(_CODEPAGE_UTF8)
    except Exception:  # noqa: BLE001 - 改不动就算了，不影响主流程
        _SAVED_CP = None


def restore() -> None:
    """退出时调用：把代码页还原成我们进来时的样子。"""
    global _SAVED_CP
    if _SAVED_CP is None:
        return
    try:
        in_cp, out_cp = _SAVED_CP
        k32 = _kernel32()
        k32.SetConsoleCP(in_cp)
        k32.SetConsoleOutputCP(out_cp)
    except Exception:  # noqa: BLE001
        pass
    finally:
        _SAVED_CP = None


def owns_console() -> bool:
    """这个控制台是不是「只挂了我们一个进程」。

    是的话说明窗口是为我们新建的（双击 exe、或拖文件到 exe 上），
    我们一退出它就没了，所以需要等一下用户。
    """
    if not _IS_WIN:
        return False
    try:
        import ctypes
        k32 = _kernel32()
        k32.GetConsoleProcessList.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k32.GetConsoleProcessList.restype = ctypes.c_uint32
        # 缓冲区传 NULL / 长度 0 时，返回值就是所需长度，也就是进程数
        return k32.GetConsoleProcessList(None, 0) == 1
    except Exception:  # noqa: BLE001
        return False


def pause_if_needed() -> None:
    """双击启动时停一下，让用户看完输出（等价于 run.bat 的 pause）。"""
    if os.environ.get(_NO_PAUSE_ENV):
        return
    if not _console_stream() or not owns_console():
        return

    _write("\n按任意键关闭窗口 ...")
    try:
        import msvcrt
        msvcrt.getch()
    except KeyboardInterrupt:
        pass
    except Exception:  # noqa: BLE001 - 退化成需要回车
        try:
            input()
        except Exception:  # noqa: BLE001
            pass


def _write(text: str) -> None:
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
