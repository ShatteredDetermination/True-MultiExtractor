# -*- coding: utf-8 -*-
"""两份 CLI 自述 ini 的自测：一份只放路径，一份只放版本。

为什么要拆
----------
``CLIPath.ini`` 在用户目录里，是壳「去哪儿找 CLI」的唯一线索 —— 里面只该有
路径；版本和署名跟着 CLI 目录走（``<CLI 目录>\\version.ini``），整包挪盘时
属性自然跟过去，不会出现「路径指向新目录、版本还写着旧的」。

这条测试把「拆开」这件事钉死：路径 ini 里出现 ``[CLIVersion]`` 就算失败。

运行：
    python selftest/test_cli_ini.py
"""

import configparser
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me import cli_path_ini, config                            # noqa: E402

PASS = 0
FAIL = 0


def check(ok, what, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [ok] {what}")
    else:
        FAIL += 1
        print(f"  [err] {what}  {detail}")


def read_ini(p: Path) -> configparser.ConfigParser:
    """按 GUI 的读法解析：跳过 BOM，UTF-8 优先。"""
    raw = p.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(raw.decode("utf-8"))
    return parser


def main() -> int:
    print("=" * 74)
    print("  CLI 自述 ini：路径 / 版本拆开")
    print("=" * 74)

    p = cli_path_ini.write()
    v = cli_path_ini.version_ini_path()

    check(p.exists(), "路径 ini 写出来了", str(p))
    check(v.exists(), "版本 ini 写出来了", str(v))
    check(p.parent != v.parent, "两份不在同一个目录",
          f"{p.parent} / {v.parent}")
    check(v.parent == Path(config.ROOT), "版本 ini 落在 CLI 目录",
          f"{v.parent} != {config.ROOT}")

    if not (p.exists() and v.exists()):
        print("\n  失败项: 必需文件没生成")
        return 1

    # ---- 路径 ini：只有路径 ----
    a = read_ini(p)
    check(a.has_section(cli_path_ini.SECTION_DIR), "路径 ini 有 [CLIDirPath]")
    check(not a.has_section(cli_path_ini.SECTION_VER),
          "路径 ini 里没有 [CLIVersion]（只存路径）",
          str(a.sections()))
    if a.has_section(cli_path_ini.SECTION_DIR):
        d = a.get(cli_path_ini.SECTION_DIR, "Dir").strip().strip('"')
        check(d == str(config.ROOT), "Dir 指向 CLI 目录本身", d)

    # ---- 版本 ini：只有版本与署名 ----
    b = read_ini(v)
    check(b.has_section(cli_path_ini.SECTION_VER), "版本 ini 有 [CLIVersion]")
    check(not b.has_section(cli_path_ini.SECTION_DIR),
          "版本 ini 里没有 [CLIDirPath]",
          str(b.sections()))
    if b.has_section(cli_path_ini.SECTION_VER):
        ver = b.get(cli_path_ini.SECTION_VER, "Version").strip().strip('"')
        by = b.get(cli_path_ini.SECTION_VER, "By").strip().strip('"')
        check(ver == config.VERSION, "Version 与 config.VERSION 一致",
              f"{ver} != {config.VERSION}")
        check(by == config.AUTHOR, "By 与 config.AUTHOR 一致",
              f"{by} != {config.AUTHOR}")

    # ---- BOM：让记事本能认出 UTF-8 ----
    check(p.read_bytes()[:3] == b"\xef\xbb\xbf", "路径 ini 带 UTF-8 BOM")
    check(v.read_bytes()[:3] == b"\xef\xbb\xbf", "版本 ini 带 UTF-8 BOM")

    print()
    if FAIL:
        print(f"  失败 {FAIL} 项")
        return 1
    print(f"  全部通过（{PASS} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
