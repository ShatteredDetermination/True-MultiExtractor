# -*- coding: utf-8 -*-
"""版本号工具：累加编号 + 切状态后缀。

标签（用户 2026-10-17 定的，以后都用这套）：

    R     Release          发布
    Rc    Release candidate 候选发布
    E     Experimental     实验性
    WIP   Work in process  开发中
    S     Scrapped         已废弃

形态写在一起、紧贴版本线，不带空格：``0.17R Beta 00093`` / ``0.18WIP Beta 00094``。
换句话说：**开发中就是 V0.17WIP，做好了就是 V0.17R**（同一版本线，只换后缀）。

两条硬规则：

  1. **编号每轮 +1**，单调递增，换版本线、换后缀都不归零。
     动过任何一行代码或文档就跑一次 ``python bump.py``。
  2. **版本线跟着目录走**：``Ver 0.17`` 里只放 ``0.17…``，
     开新线才改版本线数字（``--line 0.18``）。

改的只有 ``me/config.py`` 里的 VERSION —— 那是唯一来源（CLI 横幅、
``--version``、事件流的 version 字段、写给 GUI 的 ``<CLI 目录>\\version.ini``）。

用法：
    python bump.py                       # 编号 +1（每轮一次）
    python bump.py --show                # 只打印当前版本
    python bump.py --tag WIP             # 换状态后缀（编号不变）
    python bump.py --tag R               # 这版做好了 → 发布
    python bump.py --line 0.18 --tag WIP # 开下一条线（0.18WIP，编号照旧累加）
    python bump.py --release 0.17        # 等价于 --line 0.17 --tag R（保留旧习惯）
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = ROOT / "me" / "config.py"

# 只认 "X.Y[后缀] Beta NNNNN"，别的形态一律不动，免得改坏。
PATTERN = re.compile(r'(VERSION\s*=\s*"(\d+\.\d+)([A-Za-z]*)\s+Beta\s+)'
                     r'(\d{5})(")')

# 规范写法 -> 含义。大小写不敏感，写 rc / wip 也认，但落盘统一成规范形态。
TAGS = {
    "R": "Release（发布）",
    "Rc": "Release candidate（候选发布）",
    "E": "Experimental（实验性）",
    "WIP": "Work in process（开发中）",
    "S": "Scrapped（已废弃）",
}
CANON = {k.upper(): k for k in TAGS}

LINE_RE = re.compile(r"^\d+\.\d+$")


def canon_tag(text: str) -> str:
    """把用户输入的后缀规范成 R / Rc / E / WIP / S；不认识返回 ""。"""
    return CANON.get((text or "").strip().upper(), "")


def parse(text: str):
    m = PATTERN.search(text)
    if not m:
        return None
    return {"line": m.group(2), "tag": m.group(3), "num": m.group(4)}


def render(t: dict) -> str:
    body = f'{t["line"]}{t["tag"]} Beta {t["num"]}'
    return f'VERSION = "{body}"'


def write(t: dict) -> None:
    text = CFG.read_text(encoding="utf-8")
    text = PATTERN.sub(render(t), text, count=1)
    CFG.write_text(text, encoding="utf-8")


def current() -> str:
    t = parse(CFG.read_text(encoding="utf-8"))
    return f'{t["line"]}{t["tag"]} Beta {t["num"]}' if t else ""


def main() -> int:
    argv = sys.argv[1:]

    if "--show" in argv or "-s" in argv:
        ver = current()
        print(ver or "config.py 里的 VERSION 不是 'X.Y[后缀] Beta NNNNN' 形态")
        return 0 if ver else 1

    text = CFG.read_text(encoding="utf-8")
    t = parse(text)
    if t is None:
        print("config.py 里的 VERSION 不是 'X.Y[后缀] Beta NNNNN' 形态，没动")
        return 1

    old = f'{t["line"]}{t["tag"]} Beta {t["num"]}'

    # 取选项值：--tag / --line / --release
    def opt(name: str):
        if name not in argv:
            return None
        i = argv.index(name)
        if i + 1 >= len(argv):
            print(f"{name} 后面要跟一个值")
            raise SystemExit(1)
        return argv[i + 1].strip()

    tag_arg = opt("--tag")
    line_arg = opt("--line")
    rel_arg = opt("--release")

    if rel_arg is not None:
        # 旧写法：--release 0.17 == 换到 0.17 并标 R
        if not LINE_RE.match(rel_arg):
            print(f"版本线形态不对: {rel_arg}（要 \"0.17\" 这种）")
            return 1
        if line_arg is not None and line_arg != rel_arg:
            print("--release 与 --line 冲突")
            return 1
        line_arg, tag_arg = rel_arg, "R"

    if line_arg is not None and not LINE_RE.match(line_arg):
        print(f"版本线形态不对: {line_arg}（要 \"0.17\" 这种）")
        return 1

    if tag_arg is not None:
        canon = canon_tag(tag_arg)
        if not canon:
            print(f"不支持的状态后缀: {tag_arg}（可选 "
                  + " / ".join(TAGS) + "）")
            return 1
        t["tag"] = canon

    if line_arg is not None:
        t["line"] = line_arg

    if tag_arg is None and line_arg is None:
        # 默认行为：编号 +1，其余不动
        t["num"] = f"{int(t['num']) + 1:05d}"

    write(t)
    new = f'{t["line"]}{t["tag"]} Beta {t["num"]}'
    note = "" if new != old else "（没变化）"
    print(f"{old} -> {new}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
