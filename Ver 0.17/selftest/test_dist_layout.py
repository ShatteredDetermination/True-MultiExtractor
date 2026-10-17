# -*- coding: utf-8 -*-
"""发布目录组装自测（make_dist.py）。

壳和 CLI 分处两个仓库，靠手拷迟早漏文件。这个脚本用临时目录伪造一份
「CLI 产物 + 壳」，验证：

  A. 默认 flat 布局：CLI 与壳**同级**（壳优先看同级目录）；
     可选 subdir 布局：CLI 进 cli\\ 子目录；
  B. check() 认这两种布局；
  C. 坏形态必须被拦下：缺壳、缺 resources\\、缺 setup.ini、version.ini 版本
     不符、根目录多一个 exe、flat 包里 CLI 跑到 cli\\ 里（同级找不到）。

用法：
    python selftest/test_dist_layout.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_dist                                # noqa: E402
from me import config                           # noqa: E402

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


def section(title):
    print("\n== " + title + " ==")


def write(p: Path, text: str = "x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def fake_cli(base: Path) -> Path:
    cli = base / "cli-src"
    write(cli / make_dist.CLI_EXE, "MZ")
    write(cli / "resources" / "python314.dll", "dll")
    write(cli / "setup.ini", "[Output]\npath = Extract Resources\n")
    write(cli / "version.ini", f"[CLIVersion]\nVersion={config.VERSION}\n")
    write(cli / "tools" / "repak.exe", "MZ")
    return cli


def expect_die(fn, msg):
    """die() 会 SystemExit，这里把它当断言用。"""
    try:
        fn()
    except SystemExit:
        check(True, msg)
        return
    check(False, msg)


def main() -> int:
    print("=" * 66)
    print("  发布目录组装自测")
    print("=" * 66)

    tmp = Path(tempfile.mkdtemp(prefix="me_dist_"))
    try:
        cli = fake_cli(tmp)
        gui = tmp / "gui-src" / "MultiExtractorGUI.exe"
        write(gui, "MZ")

        section("A. 默认 flat：CLI 与壳同级")
        pkg = make_dist.assemble(cli, gui, tmp / "dist")
        check((pkg / "MultiExtractorGUI.exe").exists(), "壳在根目录")
        check((pkg / make_dist.CLI_EXE).exists(), "CLI 与壳同级（根目录）")
        check((pkg / "resources" / "python314.dll").exists(), "resources\\ 在根目录")
        check((pkg / "tools" / "repak.exe").exists(), "tools\\ 在根目录")
        check((pkg / "setup.ini").exists(), "setup.ini 在根目录")
        check(not (pkg / "cli").exists(), "flat 布局不再有 cli\\ 子目录")
        check(pkg.name.startswith("MultiExtractor-")
              and pkg.name.endswith("-win64"), f"包名 {pkg.name}")

        section("A2. 可选 subdir：CLI 进 cli\\ 子目录")
        pkg_s = make_dist.assemble(cli, gui, tmp / "dist2", layout="subdir")
        check((pkg_s / "cli" / make_dist.CLI_EXE).exists(), "CLI 在 cli\\ 下")
        check(not (pkg_s / make_dist.CLI_EXE).exists(), "根目录没有 CLI")
        try:
            make_dist.check(pkg_s, "subdir")
            check(True, "subdir 布局通过校验")
        except SystemExit:
            check(False, "subdir 布局通过校验")

        section("B. check 认 flat 布局")
        try:
            make_dist.check(pkg, "flat")
            check(True, "flat 布局通过校验")
        except SystemExit:
            check(False, "flat 布局通过校验")

        section("C. 坏形态必须被拦下")
        expect_die(lambda: make_dist.assemble(cli, tmp / "nope.exe", tmp / "d2"),
                   "壳缺失 → assemble 报错")

        p2 = make_dist.assemble(cli, gui, tmp / "d3")
        (p2 / "MultiExtractorGUI.exe").unlink()
        expect_die(lambda: make_dist.check(p2, "flat"), "缺壳 → check 拦下")

        p3 = make_dist.assemble(cli, gui, tmp / "d4")
        shutil.rmtree(p3 / "resources")
        expect_die(lambda: make_dist.check(p3, "flat"), "缺 resources\\ → check 拦下")

        p4 = make_dist.assemble(cli, gui, tmp / "d5")
        (p4 / "setup.ini").unlink()
        expect_die(lambda: make_dist.check(p4, "flat"), "缺 setup.ini → check 拦下")

        p5 = make_dist.assemble(cli, gui, tmp / "d6")
        write(p5 / "version.ini", "[CLIVersion]\nVersion=0.0.0 Beta 00001\n")
        expect_die(lambda: make_dist.check(p5, "flat"),
                   "version.ini 版本不符 → check 拦下")

        p6 = make_dist.assemble(cli, gui, tmp / "d7")
        write(p6 / "Extra.exe", "MZ")
        expect_die(lambda: make_dist.check(p6, "flat"), "根目录多一个 exe → check 拦下")

        # flat 包但 CLI 塞进了 cli\：同级找不到，必须报错而不是静默通过
        p7 = make_dist.assemble(cli, gui, tmp / "d8")
        (p7 / "cli").mkdir()
        for name in (make_dist.CLI_EXE, "setup.ini", "version.ini"):
            (p7 / name).rename(p7 / "cli" / name)
        (p7 / "resources").rename(p7 / "cli" / "resources")
        expect_die(lambda: make_dist.check(p7, "flat"),
                   "flat 包把 CLI 放进了 cli\\ → check 拦下")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

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
