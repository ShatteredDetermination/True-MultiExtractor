# -*- coding: utf-8 -*-
"""把 me/config.py 里的 Beta 累加编号加一。

编号规则：每对话一轮 +1，单调递增，开新版本线也不归零。
这里只管加一，别的地方不要写死版本号。

把 beta 版本号加一。

约定：动过任何一行代码或文档，就跑一次这个脚本。
版本形如 ``0.14 Beta 00032``，只动末尾那段五位序号。

改的是 ``me/config.py`` 里的 VERSION —— 那是唯一来源，
CLI 横幅、``--version``、事件流的 version 字段、以及写给 GUI 的
``<CLI 目录>\\version.ini`` 全从它取。

用法：
    python bump.py          # 序号 +1
    python bump.py --show   # 只打印当前版本
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = ROOT / "me" / "config.py"

# 只认 "X.Y[M] Beta NNNNN"，别的形态一律不动，免得改坏。
# 版本线允许带一个字母后缀（如 0.16E），五位序号仍然累加。
PATTERN = re.compile(r'(VERSION\s*=\s*"(\d+\.\d+[A-Za-z]?)\s+Beta\s+)'
                     r'(\d{5})(")')


def current() -> str:
    m = PATTERN.search(CFG.read_text(encoding="utf-8"))
    return m.group(0).split('"')[1] if m else ""


def main() -> int:
    argv = sys.argv[1:]
    if "--show" in argv:
        ver = current()
        print(ver or "config.py 里的 VERSION 不是 'X.Y Beta NNNNN' 形态")
        return 0 if ver else 1

    text = CFG.read_text(encoding="utf-8")
    m = PATTERN.search(text)
    if not m:
        print("config.py 里的 VERSION 不是 'X.Y Beta NNNNN' 形态，没动")
        return 1

    old = int(m.group(3))
    new = old + 1
    text = PATTERN.sub(
        lambda mm: f"{mm.group(1)}{new:05d}{mm.group(4)}", text, count=1)
    CFG.write_text(text, encoding="utf-8")
    print(f"{m.group(2)} Beta {old:05d} -> {m.group(2)} Beta {new:05d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
