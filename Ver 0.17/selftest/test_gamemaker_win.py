# -*- coding: utf-8 -*-
"""GameMaker 自研解析器的单元测试。

不依赖任何外部样本：这里**现场造一个最小 data.win**，全部指针都用真实的
文件绝对偏移填好，然后验证解析器能不能自己把布局量出来。

造出来的文件结构（bytecode 16 形态）：

    FORM <size> GEN8 <len=16> <body>
      STRG  2 个字符串
      TXTR  1 张纹理（blob 是一张 4x4 PNG）
      TPAG  2 条记录（都在 sheet 0）
      SPRT  2 个精灵，各 1 帧

断言的重点不是「读出了什么」，而是**布局是量出来的**：TXTR 的 blob 字段
被定到 +4，SPRT 的帧数组被定到 +60 —— 这两个偏移在真实文件里换版本就会变。

    python selftest/test_gamemaker_win.py
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from me.collect import pngwrite                      # noqa: E402
from me.formats import gamemaker_image as gimg       # noqa: E402
from me.formats.gamemaker_win import WinFile         # noqa: E402

PASS = 0
FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [ok] {msg}")
    else:
        FAIL += 1
        print(f"  [x ] {msg}")


def _strg(strings):
    """STRG chunk 主体。指针必须在知道起点后回填。"""
    body = bytearray()
    body += struct.pack("<I", len(strings))
    body += b"\x00" * (4 * len(strings))
    offs = []
    for s in strings:
        offs.append(len(body))
        raw = s.encode("utf-8")
        body += struct.pack("<I", len(raw)) + raw + b"\x00"
    return body, offs


def build_sample():
    """造一个最小但结构合法的 data.win。"""
    strings = ["spr_test", "spr_two"]

    # 4x4 RGBA，四个角四种颜色，方便验证切片没切错位置
    px = bytearray()
    for y in range(4):
        for x in range(4):
            px += bytes((x * 60, y * 60, 128, 255))
    png = pngwrite.encode_rgba(4, 4, bytes(px))

    gen8 = struct.pack("<I", (16 << 8) | 1) + b"\x00" * 12
    start = 16 + len(gen8)

    # 先把每个 chunk 的主体尺寸算出来，才能知道各自的绝对起点
    s_body, _ = _strg(strings)
    t_body_size = 4 + 4 + 8 + len(png)          # count + 1 ptr + 1 entry + blob
    p_body_size = 4 + 4 * 2 + 22 * 2            # count + 2 ptrs + 2 records
    r_entry = 68                                # 帧字段在 +60，留到 68
    r_body_size = 4 + 4 * 2 + r_entry * 2

    s_off = start
    t_off = s_off + 8 + len(s_body)
    p_off = t_off + 8 + t_body_size
    r_off = p_off + 8 + p_body_size

    out = bytearray()
    out += b"FORM" + b"\x00\x00\x00\x00" + b"GEN8" + struct.pack("<I", len(gen8))
    out += gen8

    # ---- STRG ----
    out += b"STRG" + struct.pack("<I", len(s_body))
    str_base = s_off + 8
    out += s_body

    # ---- TXTR ----
    t_body = bytearray()
    t_body += struct.pack("<I", 1)
    t_body += struct.pack("<I", 0)              # 占位，下面回填
    ent_at = len(t_body)
    blob_at = ent_at + 8
    t_body += struct.pack("<II", 0, 0)          # flags, blob（回填）
    t_body += png
    out += b"TXTR" + struct.pack("<I", len(t_body))
    out += t_body

    # ---- TPAG ----
    tp = bytearray()
    tp += struct.pack("<I", 2)
    tp += struct.pack("<II", 0, 0)
    rects = [(0, 0, 2, 2), (2, 0, 2, 2)]
    for (x, y, w, h) in rects:
        tp += struct.pack("<HHHH", x, y, w, h)
        tp += struct.pack("<HH", 0, 0)
        tp += struct.pack("<HHHH", w, h, w, h)
        tp += struct.pack("<H", 0)              # sheet 0
    out += b"TPAG" + struct.pack("<I", len(tp))
    out += tp

    # ---- SPRT ----
    sp = bytearray()
    sp += struct.pack("<I", 2)
    sp += struct.pack("<II", 0, 0)
    names = [0, 1]
    for i in range(2):
        e = bytearray(r_entry)
        struct.pack_into("<I", e, 0, str_base + 0)   # name 指针（回填）
        struct.pack_into("<I", e, 4, 2)              # width
        struct.pack_into("<I", e, 8, 2)              # height
        struct.pack_into("<I", e, 56, 1)             # 帧数
        sp += e
    out += b"SPRT" + struct.pack("<I", len(sp))
    out += sp

    # ---- 回填所有绝对偏移 ----
    str_ptrs = [str_base + o for o in _strg(strings)[1]]
    for i, v in enumerate(str_ptrs):
        struct.pack_into("<I", out, str_base + 4 + 4 * i, v)
    tex_ent = t_off + 8 + ent_at
    struct.pack_into("<I", out, t_off + 8 + 4, tex_ent)
    struct.pack_into("<I", out, tex_ent + 4, t_off + 8 + blob_at)
    for i in range(2):
        struct.pack_into("<I", out, p_off + 8 + 4 + 4 * i, p_off + 8 + 4 + 8 + 22 * i)
    for i in range(2):
        ent = r_off + 8 + 4 + 4 * 2 + r_entry * i
        struct.pack_into("<I", out, r_off + 8 + 4 + 4 * i, ent)
        struct.pack_into("<I", out, ent, str_ptrs[names[i]])
        struct.pack_into("<I", out, ent + 60, p_off + 8 + 4 + 8 + 22 * i)

    struct.pack_into("<I", out, 4, len(out) - 8)
    return bytes(out), png


def main():
    print("\n  GameMaker 自研解析器测试")
    data, png = build_sample()
    Path("work").mkdir(exist_ok=True)
    tmp = Path("work") / "selftest_sample.win"
    tmp.write_bytes(data)

    w = WinFile.load(tmp)
    check(w.chain_ok, "chunk 链走到文件末尾")
    check(w.bytecode() == 16, f"bytecode = {w.bytecode()}")
    check(w.order == ["STRG", "TXTR", "TPAG", "SPRT"],
          f"chunk 顺序 {w.order}")

    check(w.strings() == ["spr_test", "spr_two"], f"STRG: {w.strings()}")
    check(w.texture_field() == 4, f"TXTR blob 字段量到 +{w.texture_field()}")
    check(w.sprite_field() == 60, f"SPRT 帧数组量到 +{w.sprite_field()}")

    tp = w.tpag()
    check(len(tp) == 2, f"TPAG {len(tp)} 条")
    check((tp[0].x, tp[0].y, tp[0].w, tp[0].h, tp[0].sheet) == (0, 0, 2, 2, 0),
          f"TPAG[0] = {tp[0]}")
    check((tp[1].x, tp[1].w) == (2, 2), f"TPAG[1] 位置 = {tp[1].x},{tp[1].y}")

    sp = w.sprites()
    check(len(sp) == 2, f"SPRT {len(sp)} 个")
    check([s.name for s in sp] == ["spr_test", "spr_two"],
          f"精灵名 {[s.name for s in sp]}")
    check(sp[0].frames == [0] and sp[1].frames == [1],
          f"帧映射 {sp[0].frames} {sp[1].frames}")

    tx = w.textures()
    check(len(tx) == 1 and tx[0].kind == "png", f"纹理 {tx[0].kind}")
    pw, ph, rgba, kind = gimg.decode_texture(w.raw, tx[0].blob)
    check((pw, ph) == (4, 4), f"纹理页 {pw}x{ph}")
    check(rgba[:4] == bytes((0, 0, 128, 255)), f"左上角像素 {list(rgba[:4])}")

    r = tp[0]
    cut = gimg.crop(rgba, pw, ph, r.x, r.y, r.w, r.h)
    check(len(cut) == r.w * r.h * 4, f"切出 {r.w}x{r.h}")
    check(cut[:4] == bytes((0, 0, 128, 255)), "切片左上角与页一致")

    r2 = tp[1]
    cut2 = gimg.crop(rgba, pw, ph, r2.x, r2.y, r2.w, r2.h)
    check(cut2[:4] == rgba[(0 * pw + 2) * 4:(0 * pw + 2) * 4 + 4],
          "第二块的左上角取自 x=2")

    tmp.unlink(missing_ok=True)
    print(f"\n  PASS {PASS}   FAIL {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
