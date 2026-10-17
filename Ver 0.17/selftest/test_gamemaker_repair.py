# -*- coding: utf-8 -*-
"""GameMaker「直接读 data.win」相关逻辑的自测：识别 + 修补。

覆盖两件事：
  1. **裸的 data.win / game.droid 粘进来要能识别成 GameMaker**
     （靠 FORM/GEN8 容器头，不靠文件名）
  2. **某个 chunk 读不动时，用原长占位把它清空**
     （长度绝对不能变 —— 文件里存的是绝对偏移）

本来这里还有一组「按字节抓取内嵌 PNG / WAV」的测试，那层已经删掉了：
它捞到的是整张纹理页（atlas）而不是切好的精灵，实用性差又容易被误当成
最终素材，所以资源一律按结构取，不做字节级抓取。
"""

import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.formats import gamemaker_repair as repair            # noqa: E402
from me.scanner import detect as detect_mod                  # noqa: E402

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
    return bool(cond)


def make_container(chunks):
    """按 GameMaker 的样子拼一个 FORM/GEN8 容器。chunks=[(4字母名字, 数据)]"""
    gen8 = b"\x00" * 64
    out = bytearray(b"FORM\x00\x00\x00\x00GEN8" + struct.pack("<I", len(gen8)) + gen8)
    for name, body in chunks:
        assert len(name) == 4, f"chunk 名必须是 4 字节：{name!r}"
        out += name.encode() + struct.pack("<I", len(body)) + body
    struct.pack_into("<I", out, 4, len(out) - 8)
    return bytes(out)


# ------------------------------------------------------------------ 容器
def test_walk_and_verify():
    print("\n---- 容器：走 chunk 链 ----")
    data = make_container([("SPRT", b"\x01" * 40), ("FONT", b"\x02" * 24),
                           ("AUDO", b"\x03" * 100)])
    ok, why = repair.verify(data)
    check(ok, f"自造容器链完整（{why}）")
    names = [c[0] for c in repair.walk_chunks(data)]
    check(names == ["SPRT", "FONT", "AUDO"], f"chunk 名读对：{names}")

    broken = bytearray(data)
    first = repair.walk_chunks(data)[0]
    struct.pack_into("<I", broken, first[1] + 4, 999999)
    check(not repair.verify(bytes(broken))[0], "chunk 长度越界时 verify 报不完整")

    check(not repair.is_container(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8),
          "PNG 开头不会被当成 GameMaker 容器")


def test_neutralize_keeps_length():
    print("\n---- 修补：长度必须严格不变 ----")
    data = make_container([("SPRT", b"\x01" * 40), ("FONT", b"\x02" * 24),
                           ("AUDO", b"\x03" * 100)])
    fixed, done = repair.neutralize(data, {"FONT"})
    check(done == ["FONT"], f"清空了 FONT（{done}）")
    check(len(fixed) == len(data),
          f"长度不变（{len(fixed)} == {len(data)}）—— 绝对偏移才不会错位")
    chunks = {c[0]: c for c in repair.walk_chunks(fixed)}
    check(chunks["FONT"][2] == 24, "FONT 长度字段保持不变")
    body = fixed[chunks["FONT"][1] + 8:chunks["FONT"][3]]
    check(struct.unpack_from("<I", body, 0)[0] == 0, "FONT 变成空列表 count=0")
    check(body[4:] == b"\x00" * 20, "剩余字节填 0")
    for name in ("SPRT", "AUDO"):
        a = data[chunks[name][1]:chunks[name][3]]
        b = fixed[chunks[name][1]:chunks[name][3]]
        check(a == b, f"{name} 内容没被动过")
    check(repair.verify(fixed)[0], "修补后 chunk 链依然完整")


def test_pick_target():
    print("\n---- 修补：只清能安全清的 ----")
    data = make_container([("OPTN", b"\x01" * 8), ("SPRT", b"\x02" * 40),
                           ("DAFL", b"")])
    check(repair.pick_target(data, {"OPTN"}) is None,
          "非列表型 chunk 不会被清（OPTN 不在白名单）")
    check(repair.pick_target(data, {"SPRT"}) == "SPRT", "列表型 chunk 可以清")
    check(repair.pick_target(data, {"DAFL"}) is None,
          "本来就是空的 chunk 不用清")


def test_failing_chunks():
    print("\n---- 修补：从报错文本里抠 chunk 名 ----")
    samples = [
        ("at 00F4933F while reading object UndertaleFont\n"
         "while reading item 2 of 20 in a list of UndertaleFont in chunk FONT",
         ["FONT"]),
        ("Padding error\nat 00F8D30C while reading chunk TXTR", ["TXTR"]),
        ("Padding error in STRG\nat 00F491BE while reading chunk STRG", ["STRG"]),
        ("something totally unrelated", []),
    ]
    for text, expect in samples:
        got = repair.failing_chunks(text)
        check(got == expect, f"{expect} <- 从报错里抠出 {got}")
    multi = repair.failing_chunks("Padding error in STRG\nin chunk FONT")
    check(multi == ["STRG", "FONT"], f"多个 chunk 都能抠出来：{multi}")


# ------------------------------------------------------------------ 识别
def test_detect_bare_file():
    print("\n---- 识别：裸的 data.win 要判成 GameMaker ----")
    data = make_container([("SPRT", b"\x00" * 16)])
    tmp = Path(tempfile.mkdtemp(prefix="me_gm_"))
    try:
        p = tmp / "data.win"
        p.write_bytes(data)
        det = detect_mod.scan_file(p)
        check(det.engine == "gamemaker", f"FORM/GEN8 判成 {det.engine}")
        check("GameMaker" in det.name, f"显示名 {det.name!r}")

        p2 = tmp / "mystery.bin"          # 名字无关
        p2.write_bytes(data)
        check(detect_mod.scan_file(p2).engine == "gamemaker",
              "名字无关，靠容器头也能认出来")

        p3 = tmp / "game.droid"           # 内容不标准时靠文件名兜底
        p3.write_bytes(b"\x00" * 256)
        check(detect_mod.scan_file(p3).engine == "gamemaker",
              "内容不标准时，靠文件名 game.droid 兜底")

        p4 = tmp / "random.bin"
        p4.write_bytes(bytes((i * 7) % 256 for i in range(4096)))
        check(detect_mod.scan_file(p4).engine != "gamemaker",
              "普通二进制文件不会误判成 GameMaker")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("=" * 66)
    print("  GameMaker 识别 + 修补自测")
    print("=" * 66)
    test_walk_and_verify()
    test_neutralize_keeps_length()
    test_pick_target()
    test_failing_chunks()
    test_detect_bare_file()

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
