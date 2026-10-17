# -*- coding: utf-8 -*-
"""版本号规则自测。

规则（2026-10-17 起）：

  1. 编号每轮 +1，单调递增，换版本线、换后缀都不归零；
  2. 状态后缀紧贴版本线：R / Rc / E / WIP / S，
     同一版本线只换后缀 —— 开发中是 ``0.17WIP``，做好了是 ``0.17R``；
  3. 目录名只带版本线数字（``Ver 0.17``），里面放哪条后缀都行。

这个脚本把形态、目录一致性、bump 的四种动作都钉成断言，
免得哪天手滑把后缀写错或把编号写回去。

用法：
    python selftest/test_version_rule.py
"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me import config      # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

VER_RE = re.compile(r"^(\d+)\.(\d+)(R|Rc|E|WIP|S)? Beta (\d{5})$")
DIR_RE = re.compile(r"^Ver (\d+)\.(\d+)$")

TAGS = ("R", "Rc", "E", "WIP", "S")


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {msg}")
    else:
        FAIL += 1
        FAILURES.append(msg)
        print(f"  [FAIL] {msg}")
    return bool(cond)


def section(title):
    print("\n== " + title + " ==")


def test_shape():
    section("形态：X.Y<后缀> Beta NNNNN")

    m = VER_RE.match(config.VERSION)
    check(m is not None, f"VERSION 形态合法（{config.VERSION!r}）")
    if m is None:
        return

    _major, _minor, suffix, number = m.groups()
    check(len(number) == 5, f"编号是五位（{number}）")
    check(suffix in TAGS, f"后缀属于 R/Rc/E/WIP/S（{suffix!r}）")
    check(not re.search(r"\s+", config.VERSION.split(" Beta ")[0]),
          f"后缀紧贴版本线、不带空格（{config.VERSION.split(' Beta ')[0]!r}）")


def test_dir_matches():
    section("目录名与版本线一致（后缀不参与比较）")

    m = VER_RE.match(config.VERSION)
    if m is None:
        check(False, "形态不合法，跳过目录一致性检查")
        return
    major, minor, _suffix, _number = m.groups()
    dm = DIR_RE.match(ROOT.name)
    check(dm is not None, f"目录名形态 `Ver X.Y`（{ROOT.name!r}）")
    if dm is None:
        return
    check(dm.groups() == (major, minor),
          f"{config.VERSION} 放在 {ROOT.name}（版本线 {major}.{minor}）")


def test_tags_documented():
    section("五个后缀都有定义")
    text = (ROOT / "bump.py").read_text(encoding="utf-8")
    for tag in TAGS:
        check(f'"{tag}":' in text, f"bump.py 定义了 {tag}")


def test_bump():
    section("bump.py：+1 / 换后缀 / 换版本线 / 旧写法 --release")

    bump = ROOT / "bump.py"
    tmp = Path(tempfile.mkdtemp(prefix="me_ver_"))
    try:
        src = (ROOT / "me" / "config.py").read_text(encoding="utf-8")
        copy = tmp / "config.py"
        copy.write_text(src, encoding="utf-8")

        script = tmp / "run_bump.py"
        script.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, r'{ROOT}')\n"
            "sys.argv = ['bump'] + sys.argv[1:]\n"
            "import bump\n"
            f"bump.CFG = Path(r'{copy}')\n"
            "raise SystemExit(bump.main())\n",
            encoding="utf-8")

        def run(*args):
            return subprocess.run([sys.executable, str(script)] + list(args),
                                  capture_output=True,
                                  encoding="utf-8", errors="replace")

        def ver():
            return re.search(r'VERSION\s*=\s*"([^"]+)"',
                             copy.read_text(encoding="utf-8")).group(1)

        before = VER_RE.match(ver())
        p = run()
        after = VER_RE.match(ver())
        check(p.returncode == 0, f"默认 bump 退出码 {p.returncode}")
        check(before and after and int(after.group(4)) == int(before.group(4)) + 1,
              f"编号 +1（{before.group(0)} -> {after.group(0)}）")
        check(after and after.group(3) == before.group(3), "默认 bump 不动后缀")

        p = run("--tag", "R")
        m = VER_RE.match(ver())
        check(p.returncode == 0 and m and m.group(3) == "R",
              f"--tag R → {ver()}")
        check(m and int(m.group(4)) == int(after.group(4)), "--tag 不改编号")

        p = run("--tag", "wip")
        m = VER_RE.match(ver())
        check(p.returncode == 0 and m and m.group(3) == "WIP",
              f"小写 wip 规范化成 WIP（{ver()}）")

        p = run("--line", "0.18")
        m = VER_RE.match(ver())
        check(p.returncode == 0 and m and m.group(1, 2) == ("0", "18"),
              f"--line 0.18 换版本线（{ver()}）")

        p = run("--release", "0.19")
        m = VER_RE.match(ver())
        check(p.returncode == 0 and m and m.group(3) == "R",
              f"--release 0.19 == 换线 + R（{ver()}）")
        check(m and int(m.group(4)) == int(after.group(4)),
              "--release 不改编号")

        p = run("--tag", "bogus")
        check(p.returncode != 0, f"--tag bogus 被拒（退出码 {p.returncode}）")
        p = run("--release", "0.17E")
        check(p.returncode != 0, f"--release 0.17E 被拒（退出码 {p.returncode}）")
        p = run("--tag")
        check(p.returncode != 0, f"--tag 缺值被拒（退出码 {p.returncode}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("=" * 66)
    print(f"  版本号规则自测（{config.VERSION}）")
    print("=" * 66)

    test_shape()
    test_dir_matches()
    test_tags_documented()
    test_bump()

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
