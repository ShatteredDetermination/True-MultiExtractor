# -*- coding: utf-8 -*-
"""GameMaker 兜底逻辑自测：容器识别、修补、抓取。

全部用合成样本，不依赖真实游戏文件，离线可复现。

这里测的都是「不做就会静默出错」的东西：
  * 抓取会不会收垃圾（盲扫 SOI/EOI 的教训：假阳性体积能超过文件本身）
  * 修补会不会改变长度（长度一变，文件里的绝对偏移全部错位）
  * UTMT 报错文本里的 chunk 名能不能正确抠出来
"""

import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.collect import pngwrite                              # noqa: E402
from me.formats import gamemaker_carve as carve              # noqa: E402
from me.formats import gamemaker_repair as repair            # noqa: E402

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


# ------------------------------------------------------------------ 样本
def insert_chunk(png: bytes, typ: bytes, body: bytes) -> bytes:
    """在 IHDR 之后插入一个 chunk（测试用）。"""
    ihdr_len = struct.unpack_from(">I", png, 8)[0]
    pos = 8 + 12 + ihdr_len
    chunk = (struct.pack(">I", len(body)) + typ + body
             + struct.pack(">I", zlib.crc32(typ + body) & 0xFFFFFFFF))
    return png[:pos] + chunk + png[pos:]


def make_png(w=64, h=64, ancillary=False):
    # 像素要有变化，不然压得太小会低于抓取的下限
    rgba = bytes(((x * 7 + x // 3) % 256) for x in range(w * h * 4))
    png = pngwrite.encode_rgba(w, h, rgba)
    if ancillary:
        # 插一个首字母小写的合法块（PNG 的 ancillary 位就在这里）。
        # 这个用例存在的理由很具体：曾经在校验里要求 chunk 类型全大写，
        # 结果真实的纹理页因为带 sRGB 块被全部拒掉，一张图都抓不出来。
        png = insert_chunk(png, b"sRGB", b"\x00")
    return png


def blob_span_ok(buf, found, expected) -> bool:
    return buf[found.start:found.end] == expected


def make_wav(samples=64, rate=22050, ch=1):
    data = bytes(samples)
    fmt = struct.pack("<HHIIHH", 1, ch, rate, rate * ch, ch, 8)
    body = (b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(data)) + data)
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body


def make_ogg(pages=3, payload=400):
    out = bytearray()
    for i in range(pages):
        seg = [255] * (payload // 255) + [payload % 255]
        body = bytes(payload)
        hdr = (b"OggS" + bytes([0, 0 if i else 2]) +
               struct.pack("<q", i) + struct.pack("<I", 1) +
               struct.pack("<I", i) + b"\x00\x00\x00\x00" +
               bytes([len(seg)]) + bytes(seg))
        out += hdr + body
    return bytes(out)


def make_container(chunks):
    """按 GameMaker 的样子拼一个 FORM/GEN8 容器。chunks=[(名字, 数据)]"""
    gen8 = b"\x00" * 64
    out = bytearray(b"FORM\x00\x00\x00\x00GEN8" + struct.pack("<I", len(gen8)) + gen8)
    for name, body in chunks:
        out += name.encode() + struct.pack("<I", len(body)) + body
    struct.pack_into("<I", out, 4, len(out) - 8)
    return bytes(out)


# ------------------------------------------------------------------ 1. 抓取
def test_carve():
    print("\n---- 抓取：只收结构完整的 ----")
    png, wav, ogg = make_png(), make_wav(), make_ogg()
    noise = bytes((i * 37 + 11) % 256 for i in range(4096))

    blob = noise + png + noise + wav + noise + ogg + noise
    found = carve.carve_bytes(blob)
    kinds = sorted(f.kind for f in found)
    check(kinds == ["ogg", "png", "wav"], f"三种格式都抓到了：{kinds}")

    for f in found:
        if f.kind == "png":
            check(blob[f.start:f.end] == png, "PNG 边界精确")
            check(f.meta[:2] == (64, 64), f"PNG 尺寸读对：{f.meta[:2]}")
        elif f.kind == "wav":
            check(blob[f.start:f.end] == wav, "WAV 边界精确")
        elif f.kind == "ogg":
            check(blob[f.start:f.end] == ogg, "OGG 边界精确")

    # 截断的样本必须被拒
    half = png[:len(png) // 2]
    check(not carve.carve_bytes(noise + half + noise),
          "截断的 PNG 被拒（结构不完整）")
    bad_wav = wav[:20] + b"\x00" * 40
    check(not [f for f in carve.carve_bytes(bad_wav) if f.kind == "wav"],
          "长度字段对不上的 WAV 被拒")

    # 带 ancillary 块（首字母小写）的 PNG 必须照样能抓
    anc = make_png(ancillary=True)
    got_anc = [f for f in carve.carve_bytes(noise + anc + noise) if f.kind == "png"]
    check(len(got_anc) == 1 and blob_span_ok(noise + anc + noise, got_anc[0], anc),
          "带 sRGB 等小写块的 PNG 也能抓到（不能要求 chunk 类型全大写）")

    # 纯噪声不能抓出任何东西
    check(not carve.carve_bytes(noise * 4), "纯噪声抓不出东西（没有假阳性）")

    # 完全随机的数据也不能抓出东西
    import random
    rnd = random.Random(1234)
    junk = bytes(rnd.randrange(256) for _ in range(200000))
    got = carve.carve_bytes(junk)
    check(len(got) == 0, f"20 万字节随机数据抓到 {len(got)} 个（应为 0）")


def test_carve_dedupe():
    print("\n---- 抓取：去重 ----")
    png = make_png()
    blob = png + b"\x00" * 800 + png + b"\x00" * 800 + png
    found = carve.carve_bytes(blob)
    check(len(found) == 3, f"三份相同内容都抓到了（{len(found)}）")
    uniq = carve.dedupe(blob, found)
    check(len(uniq) == 1, f"去重后剩 {len(uniq)} 个")


# ------------------------------------------------------------------ 2. 修补
def test_walk_and_verify():
    print("\n---- 容器：走 chunk 链 ----")
    data = make_container([("SPRT", b"\x01" * 40), ("FONT", b"\x02" * 24),
                           ("AUDO", b"\x03" * 100)])
    ok, why = repair.verify(data)
    check(ok, f"自造容器链完整（{why}）")
    names = [c[0] for c in repair.walk_chunks(data)]
    check(names == ["SPRT", "FONT", "AUDO"], f"chunk 名读对：{names}")

    # 长度字段被写坏 -> verify 必须发现
    broken = bytearray(data)
    first = repair.walk_chunks(data)[0]
    struct.pack_into("<I", broken, first[1] + 4, 999999)
    ok2, _why = repair.verify(bytes(broken))
    check(not ok2, "chunk 长度越界时 verify 报不完整")

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
    # 被清空的那个 chunk 变成 count=0 + 补齐
    chunks = {c[0]: c for c in repair.walk_chunks(fixed)}
    check(chunks["FONT"][2] == 24, "FONT 长度字段保持不变")
    body = fixed[chunks["FONT"][1] + 8:chunks["FONT"][3]]
    check(struct.unpack_from("<I", body, 0)[0] == 0, "FONT 变成空列表 count=0")
    check(body[4:] == b"\x00" * 20, "剩余字节填 0")
    # 其它 chunk 一个字节都不能动
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
    print("\n---- 修补：从 UTMT 报错里抠 chunk 名 ----")
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


# ------------------------------------------------------------------ 3. 识别
def test_detect():
    print("\n---- 识别：裸的 data.win 要判成 GameMaker ----")
    from me.scanner import detect as detect_mod
    import tempfile

    data = make_container([("SPRT", b"\x00" * 16)])
    tmp = Path(tempfile.mkdtemp(prefix="me_gm_"))
    try:
        p = tmp / "data.win"
        p.write_bytes(data)
        det = detect_mod.scan_file(p)
        check(det.engine == "gamemaker", f"FORM/GEN8 判成 {det.engine}")
        check("GameMaker" in det.name, f"显示名 {det.name!r}")

        # 换个名字，靠容器头一样能认
        p2 = tmp / "mystery.bin"
        p2.write_bytes(data)
        check(detect_mod.scan_file(p2).engine == "gamemaker",
              "名字无关，靠容器头也能认出来")

        # 文件名兜底：内容不是 FORM/GEN8，但文件名是 game.droid
        p3 = tmp / "game.droid"
        p3.write_bytes(b"\x00" * 256)
        check(detect_mod.scan_file(p3).engine == "gamemaker",
              "内容不标准时，靠文件名 game.droid 兜底")

        # 普通文件不能被误判
        p4 = tmp / "random.bin"
        p4.write_bytes(bytes((i * 7) % 256 for i in range(4096)))
        check(detect_mod.scan_file(p4).engine != "gamemaker",
              "普通二进制文件不会误判成 GameMaker")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("=" * 66)
    print("  GameMaker 兜底逻辑自测")
    print("=" * 66)
    test_carve()
    test_carve_dedupe()
    test_walk_and_verify()
    test_neutralize_keeps_length()
    test_pick_target()
    test_failing_chunks()
    test_detect()

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
