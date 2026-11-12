# -*- coding: utf-8 -*-
"""flag=3（zlib + 加密）块的自测（V0.19 起）。

四段：

  1. 密钥推导：``key_string`` / ``make_key_combined`` / ``build_encryption``；
  2. 换表与异或流：排列性、对合性、同表可逆；
  3. 块体回环：加密再解密还原（奇偶 id、编译号 > 285 的补丁都覆盖）；
  4. 真样本取证：flag=3 块的「u32 声明长度」层已经读对（0x3337 恒为 1028）。

真样本不存在就跳过第 4 段，不失败。
"""

import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.formats.ctf25 import chunks as chunk_mod  # noqa: E402
from me.formats.ctf25 import container  # noqa: E402
from me.formats.ctf25 import crypto  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []
SKIPPED = 0

SAMPLES = [
    Path(r"C:\拆包教程素材\目标游戏\dtlg-v1.1.0.exe"),
    Path(r"C:\拆包教程素材\目标游戏\Undertale Last Breath Scrapped BETA"
         r"\Undertale Last Breath Scrapped.dat"),
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


def test_keys():
    section("1. 密钥推导")
    check(crypto.key_string("AB") == b"AB", "ASCII 逐字节")
    check(crypto.key_string("A\x00B") == b"AB", "0 字节被丢掉")
    check(len(crypto.key_string("拆")) == 2, "中文字符贡献两个字节",
          crypto.key_string("拆").hex(" "))

    empty = crypto.make_key_combined(b"")
    check(len(empty) == 256 and not any(empty), "空密钥 = 256 个 0",
          f"{len(empty)} B")

    key = crypto.make_key_combined(b"ABC")
    check(len(key) == 256, "撑到 256 B", str(len(key)))
    check(key[:3] == b"ABC" and not any(key[5:]), "原字节在前、后面补 0（校验和那格除外）",
          key[:6].hex(" "))
    check(key[4] == (0x41 + 0x42 + 0x43) * 2 % 256, "data[长度+1] = 校验和×2",
          f"{key[4]}")

    long_key = crypto.make_key_combined(b"x" * 300)
    check(len(long_key) == 256, "超长输入不炸", str(len(long_key)))

    check(crypto.build_encryption(["AB", "C"]) == crypto.make_key_combined(b"ABC"),
          "多串拼接等价于直接拼字节")
    check(isinstance(crypto.KEY_STATUS, str) and crypto.KEY_STATUS,
          "模块自报状态（密钥未定）", crypto.KEY_STATUS)


def test_table():
    section("2. 换表与异或流")
    table = crypto.init_table(crypto.DEFAULT_KEY)
    check(len(table) == 256, "换表 256 项")
    check(sorted(table) == list(range(256)), "是 0~255 的排列")
    check(crypto.init_table(crypto.DEFAULT_KEY) == table, "同密钥同表")
    check(crypto.init_table(crypto.build_encryption(["app"])) != table,
          "换密钥换表")
    short = crypto.init_table(b"\x01")
    check(len(short) == 256 and sorted(short) == list(range(256)),
          "短密钥会先补齐")

    check(crypto.transform(b"", table) == b"", "空数据不动")
    data = bytes(range(64))
    check(crypto.transform(crypto.transform(data, table), table) == data,
          "同表两遍还原")
    other = bytes((i * 7 + 3) & 0xFF for i in range(64))
    x = bytes(a ^ b for a, b in zip(crypto.transform(data, table),
                                    crypto.transform(other, table)))
    y = bytes(a ^ b for a, b in zip(data, other))
    check(x == y, "异或流：变换后仍满足 x^y 关系")


def test_roundtrip():
    section("3. 块体回环")
    plain = b"frame header payload \x00" * 3 + bytes(range(32))
    key = crypto.build_encryption(["DustTale", "Fusion"])

    for cid in (0x3334, 0x3337, 0x3341, 0x333D):
        body = crypto.encrypt_chunk_body(plain, cid, key=key)
        import struct
        declared = struct.unpack_from("<I", body, 0)[0]
        check(declared == len(plain), f"0x{cid:04X} 块体首 4 B 是明文长度",
              f"{declared}")
        got_declared, got = crypto.decrypt_chunk_body(body, cid, key=key)
        check(got == plain and got_declared == len(plain),
              f"0x{cid:04X} 解密还原", f"{len(got)} B")

    body = crypto.encrypt_chunk_body(plain, 0x3334, key=key, build=285)
    check(crypto.decrypt_chunk_body(body, 0x3334, key=key, build=285)[1] == plain,
          "编译号 ≤ 285 时不做奇偶补丁")

    try:
        crypto.decrypt_chunk_body(b"\x10\x00\x00\x00" + b"\xaa" * 20, 0x3334)
    except crypto.CryptoError:
        check(True, "乱数据解不开会抛 CryptoError")
    else:
        check(False, "乱数据解不开会抛 CryptoError", "居然没抛")

    try:
        crypto.decrypt_chunk_body(b"\x10\x00\x00\x00", 0x3334)
    except crypto.CryptoError:
        check(True, "块体太短会抛 CryptoError")
    else:
        check(False, "块体太短会抛 CryptoError", "居然没抛")


def read_flag3_chunks(path):
    """取第一个帧容器里的 flag=3 块：[(id, body)]。"""
    out = []
    mm, loc = container.open_file(str(path))
    try:
        buf = mm.mm
        for ch in chunk_mod.scan(buf, loc.chunk_start, len(buf)):
            if ch.id != 0x3333:
                continue
            for sub in chunk_mod.scan(buf, ch.data_offset,
                                      ch.data_offset + ch.size):
                if sub.flag == 3:
                    out.append((sub.id, buf[sub.data_offset:
                                            sub.data_offset + sub.size]))
            break
        return out
    finally:
        mm.close()


def test_samples():
    section("4. 真样本取证（块体布局）")
    import struct

    found = 0
    for path in SAMPLES:
        if not path.is_file():
            print(f"  [skip] 样本不在：{path}")
            continue
        found += 1
        pieces = read_flag3_chunks(path)
        check(bool(pieces), f"{path.name} 有 flag=3 块", str(len(pieces)))
        if not pieces:
            continue
        decls = {}
        for cid, body in pieces:
            if len(body) < 4:
                continue
            decls[cid] = struct.unpack_from("<I", body, 0)[0]
        check(decls.get(0x3337) == 1028,
              f"{path.name} 调色板块声明长度 = 1028", str(decls.get(0x3337)))
        check(all(0 < d < (1 << 20) for d in decls.values()),
              f"{path.name} 声明长度都在合理范围", str(decls))
        check(0x3334 in decls and decls[0x3334] > 0,
              f"{path.name} 帧头块有声明长度", str(decls.get(0x3334)))
        try:
            crypto.decrypt_chunk_body(dict(pieces)[0x3337], 0x3337)
        except crypto.CryptoError:
            print("  [info] 密钥还没定，flag=3 块仍解不开（布局已确认）")
        else:
            print("  [info] 咦？没定密钥居然解开了，去更新 crypto.KEY_STATUS")

    if not found:
        global SKIPPED
        SKIPPED += 1
        print("  [skip] 两支样本都不在，第 4 段整体跳过")


def main():
    test_keys()
    test_table()
    test_roundtrip()
    test_samples()
    print(f"\n通过 {PASS}，失败 {FAIL}，跳过 {SKIPPED}")
    if FAILURES:
        print("失败项：")
        for what in FAILURES:
            print("  - " + what)
    return 0 if FAIL == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
