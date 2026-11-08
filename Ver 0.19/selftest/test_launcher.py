# -*- coding: utf-8 -*-
"""启动器（run.bat）的静态检查。

这个测试存在的理由很具体：曾经因为 run.bat 里写了中文注释而彻底跑不起来。

cmd.exe 读 .bat 用的是 **OEM 代码页**（中文 Windows 是 GBK），不是 UTF-8。
UTF-8 的中文注释被按 GBK 解出来是一串乱码字节，cmd 会拿它们当命令执行，
更糟的是多字节序列会「吃掉」后面的 ASCII 字符 —— 实测把 `else` 吃成了 `lse`，
于是整个 if/else 结构崩掉。

换成 GBK 编码也不行：换到别的区域设置（英文 Windows、日文 Windows）同样会坏。
唯一稳的做法是 **.bat 里只写 ASCII**，所有中文提示交给 Python 端
（me/ui.py 自己处理编码）。

另外 .bat 必须用 CRLF 行尾，LF-only 在某些结构（标签、括号块）下会被 cmd 误解析。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAT = ROOT / "run.bat"

PASS = 0
FAIL = 0
FAILURES = []


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {msg}")
    else:
        FAIL += 1
        FAILURES.append(msg)
        print(f"  [FAIL] {msg}")


def main() -> int:
    print("=" * 66)
    print("  启动器静态检查 (run.bat)")
    print("=" * 66)

    check(BAT.exists(), "run.bat 存在")
    if not BAT.exists():
        return 1

    raw = BAT.read_bytes()

    # ---- 1. 必须是纯 ASCII ----
    bad = [(i, b) for i, b in enumerate(raw) if b > 127]
    check(not bad,
          f"纯 ASCII（非 ASCII 字节 {len(bad)} 个"
          + (f"，首个在偏移 {bad[0][0]}" if bad else "") + "）")
    if bad:
        # 给出上下文，方便定位是哪一行
        off = bad[0][0]
        line_no = raw[:off].count(b"\n") + 1
        print(f"        首次出现在第 {line_no} 行附近: {raw[max(0,off-40):off+40]!r}")

    # ---- 2. 必须是 CRLF 行尾 ----
    lf_total = raw.count(b"\n")
    crlf_total = raw.count(b"\r\n")
    check(lf_total > 0 and lf_total == crlf_total,
          f"全 CRLF 行尾（\\n {lf_total} 个, \\r\\n {crlf_total} 个）")

    text = raw.decode("ascii", "replace")

    # ---- 3. 关键逻辑还在 ----
    checks = [
        ("chcp 65001", "切到 UTF-8 代码页"),
        ("PYTHONIOENCODING", "让 Python 的输入输出也用 UTF-8"),
        ('cd /d "%~dp0"', "先切到脚本所在目录（从别处调用也能跑）"),
        ('"%~dp0main.py"', "用绝对路径调 main.py"),
        ("where py", "优先用 py 启动器找 Python"),
        ("where python", "回退到 PATH 里的 python"),
        ("pause", "结束后暂停，双击运行时能看到结果"),
    ]
    for needle, why in checks:
        check(needle in text, f"包含 {needle!r}（{why}）")

    # ---- 4. 不能出现会破坏解析的写法 ----
    # 只检查真正会被执行的代码行；注释（rem）里提到这些词是正常的，
    # 本文件上面就专门用 rem 解释了为什么不用 %errorlevel%。
    code_lines = [
        ln for ln in text.splitlines()
        if not ln.strip().lower().startswith("rem")
    ]
    code = "\n".join(code_lines)

    # %errorlevel% 在括号块里会按解析期展开，是经典陷阱；应该用 if not errorlevel N
    check("%errorlevel%" not in code,
          "代码里没有用 %errorlevel%（括号块里会按解析期展开，"
          "应改用 if not errorlevel 1）")

    print("\n" + "=" * 66)
    print(f"  PASS {PASS}  FAIL {FAIL}")
    print("=" * 66)
    if FAILURES:
        print("\n失败项:")
        for f in FAILURES:
            print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
