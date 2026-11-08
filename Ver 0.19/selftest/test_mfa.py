# -*- coding: utf-8 -*-
"""MFA 前段读写自测（V0.19 起）。

三段：

  1. CString 编码：空串 / ASCII / 中文 / 长串，读写对称；
  2. 合成前段：写出来再读回来，字段必须一致（不依赖任何外部文件）；
  3. 真样本：本机有参考 MFA 就逐字节回环（没有就跳过，不失败）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.formats.ctf25 import mfa  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []
SKIPPED = 0

SAMPLES = [
    Path(r"C:\拆包教程素材\Clickteam MFA 样本\Template.mfa"),
    Path(r"C:\拆包教程素材\Clickteam MFA 样本\Platformer Engine.mfa"),
]


def check(ok, what, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [ok] {what}")
    else:
        FAIL += 1
        FAILURES.append(what)
        print(f"  [err] {what}  {detail}")


def section(t):
    print("\n== " + t + " ==")


def test_cstring():
    section("1. CString 编码")
    texts = ["", "Template", "拆包工具", "a" * 300]
    w = mfa.Writer()
    for t in texts:
        w.cstring(t)
    buf = w.getvalue()

    r = mfa.Reader(buf)
    got = [r.cstring() for _ in texts]
    check(got == texts, "读写对称", f"{got[:2]!r}")
    check(buf[:4] == b"\x00\x00\x00\x80", "空串就是 00 00 00 80",
          buf[:4].hex(" "))

    # 单个 ASCII 串：长度字段 = 0x80000000 | 8
    one = mfa.Writer()
    one.cstring("Template")
    head = one.getvalue()[:4]
    check(head == b"\x08\x00\x00\x80", "ASCII 长度带 Unicode 位",
          head.hex(" "))

    # 手工构造：UTF-16LE "AB" = 41 00 42 00
    hand = b"\x02\x00\x00\x80" + "AB".encode("utf-16-le")
    check(mfa.Reader(hand).cstring() == "AB", "手写串能读")

    # 长度位为 0（ANSI）时按单字节读
    ansi = b"\x02\x00\x00\x00" + b"AB"
    check(mfa.Reader(ansi).cstring() == "AB", "ANSI（无 Unicode 位）读得出来")


def test_synthetic():
    section("2. 合成前段回环")
    pre = mfa.MfaPreamble(
        version=7, editor_version=3, editor_build=292, language=1033,
        app_name="合成工程", description="",
        editor_path=r"C:\tmp\合成工程.mfa",
        thumbnail=b"\x89PNG" + b"\x00" * 32,
        banks_raw=mfa.TAG_FONTS + b"\x00\x00\x00\x00"
                  + mfa.TAG_SOUNDS + b"\x00\x00\x00\x00"
                  + mfa.TAG_MUSIC + b"\x00\x00\x00\x00"
                  + mfa.TAG_ICONS + b"\x00\x00\x00\x00"
                  + mfa.TAG_IMAGES + b"\x00\x00\x00\x00",
    )
    blob = mfa.build_preamble(pre)
    back = mfa.read_preamble(blob)
    check(back.app_name == "合成工程", "工程名回读", back.app_name)
    check(back.editor_path == pre.editor_path, "编辑器路径回读")
    check(back.thumbnail == pre.thumbnail, "缩略图回读")
    check(back.banks_raw == pre.banks_raw, "库数据回读")
    check(mfa.build_preamble(back) == blob, "再写一遍仍一致")

    # 库标记不对时必须报错，不能当没事
    bad = bytearray(blob)
    idx = blob.find(mfa.TAG_FONTS)
    bad[idx:idx + 4] = b"XXXX"
    try:
        mfa.read_preamble(bytes(bad))
        check(False, "库标记错要报错")
    except mfa.MfaError:
        check(True, "库标记错要报错")

    # 截断也要报错
    try:
        mfa.read_preamble(blob[:20])
        check(False, "截断文件要报错")
    except mfa.MfaError:
        check(True, "截断文件要报错")


def test_samples():
    global SKIPPED
    section("3. 真样本（本机有就验，没有就跳过）")
    if not any(p.exists() for p in SAMPLES):
        SKIPPED += 1
        print("  [skip] 参考 MFA 样本不在本机")
        return

    for path in SAMPLES:
        if not path.exists():
            print(f"  [skip] {path.name}")
            continue
        raw = path.read_bytes()
        pre = mfa.read_file(path)
        check(pre.version in (6, 7), f"{path.name} 版本 {pre.version}")
        check(pre.editor_build == 292, f"{path.name} 编辑器构建 {pre.editor_build}")
        check(pre.language == 1033, f"{path.name} 语言 {pre.language}")
        check(pre.app_name != "", f"{path.name} 工程名非空", pre.app_name)
        check(len(pre.thumbnail) > 0, f"{path.name} 有缩略图（{len(pre.thumbnail)} B）")
        check(mfa.build_preamble(pre) == raw, f"{path.name} 逐字节回环")


def main() -> int:
    print("=" * 66)
    print("  MFA 前段读写自测")
    print("=" * 66)
    test_cstring()
    test_synthetic()
    test_samples()

    print("\n" + "=" * 66)
    print(f"  PASS {PASS}  FAIL {FAIL}  SKIP {SKIPPED}")
    print("=" * 66)
    if FAILURES:
        print("\n失败项:")
        for f in FAILURES:
            print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
