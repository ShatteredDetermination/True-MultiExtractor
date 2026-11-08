# -*- coding: utf-8 -*-
"""LZ4 块解压自测（me/formats/lz4_block.py）。

LZ4 块格式自校验很弱，所以测试按「一个序列一种情况」逐个手写压缩数据，
再断言解出来的字节；外加几个坏数据必须报错。

用法：
    python selftest/test_lz4_block.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.formats import lz4_block                    # noqa: E402

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


def test_basic():
    section("A. 基本序列")

    # 只有字面量："abc" -> token 0x30（字面量 3，无匹配），然后 abc
    blob = bytes([0x30]) + b"abc"
    check(lz4_block.decompress_block(blob) == b"abc", "纯字面量 3 字节")

    # 15 字节字面量：token 0xF0 + 扩展 0（表示恰好 15）
    blob = bytes([0xF0, 0x00]) + b"x" * 15
    check(lz4_block.decompress_block(blob) == b"x" * 15, "15 字节字面量（扩展为 0）")

    # 20 字节字面量：token 0xF0 + 扩展 5
    blob = bytes([0xF0, 0x05]) + b"y" * 20
    check(lz4_block.decompress_block(blob) == b"y" * 20, "20 字节字面量（扩展 5）")

    # 空输入
    check(lz4_block.decompress_block(b"") == b"", "空块解出空")


def test_match():
    section("B. 匹配段（含重叠复制）")

    # "ab" 之后匹配 offset=2 len=4：token 高4=2 字面量, 低4=0 -> 匹配长度 4
    # 期望输出 ababab
    blob = bytes([0x20]) + b"ab" + bytes([0x02, 0x00])
    check(lz4_block.decompress_block(blob) == b"ababab",
          "offset=2 匹配 4 字节 -> ababab")

    # 重叠复制：字面量 "a" + offset=1 len=7 -> 8 个 a
    blob = bytes([0x13]) + b"a" + bytes([0x01, 0x00])
    check(lz4_block.decompress_block(blob) == b"a" * 8,
          "offset=1 重叠复制 -> 8 个 a")

    # 长匹配：字面量 "z"，匹配长度 15+扩展 10+4 = 29
    blob = bytes([0x1F]) + b"z" + bytes([0x01, 0x00]) + bytes([0x0A])
    got = lz4_block.decompress_block(blob)
    check(got == b"z" * 30, f"长匹配 30 字节（实际 {len(got)}）")

    # expected_size 正确/错误两种
    blob = bytes([0x20]) + b"ab" + bytes([0x02, 0x00])
    check(lz4_block.decompress_block(blob, 6) == b"ababab",
          "expected_size 对得上就通过")
    try:
        lz4_block.decompress_block(blob, 7)
        check(False, "expected_size 对不上应该报错")
    except lz4_block.Lz4Error:
        check(True, "expected_size 对不上报 Lz4Error")


def test_bad():
    section("C. 坏数据必须报错")

    cases = {
        "字面量越界": bytes([0x50]) + b"ab",
        "匹配偏移为 0": bytes([0x10]) + b"a" + bytes([0x00, 0x00]),
        "匹配偏移超出已输出": bytes([0x10]) + b"a" + bytes([0x05, 0x00]),
        "扩展长度越界": bytes([0xF0, 0xFF]),
        "偏移字段只有 1 字节": bytes([0x10]) + b"a" + bytes([0x01]),
    }
    for name, blob in cases.items():
        try:
            lz4_block.decompress_block(blob)
            check(False, f"{name}: 应该报错")
        except lz4_block.Lz4Error:
            check(True, f"{name}: Lz4Error")
        except Exception as exc:                       # noqa: BLE001
            check(False, f"{name}: 抛了 {type(exc).__name__}")


def main() -> int:
    print("=" * 66)
    print("  LZ4 块解压自测")
    print("=" * 66)

    test_basic()
    test_match()
    test_bad()

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
