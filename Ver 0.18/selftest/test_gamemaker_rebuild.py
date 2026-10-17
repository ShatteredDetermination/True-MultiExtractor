# -*- coding: utf-8 -*-
"""FONT 重建的不变式自测。

不依赖任何真实游戏文件：自己拼一个最小的 data.win（STRG + FONT），
把 FONT 的指针表故意改坏，再走一遍重建，检查：
  1. 诊断能报出坏掉的指针；
  2. 重建不改变文件长度（绝对偏移才不会失效）；
  3. 重建后指针全部落在 chunk 内，并且指向走出来的真实起点；
  4. 重建后自检不再报 FONT 的错。
"""

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.formats import gamemaker_rebuild as rb        # noqa: E402
from me.formats.gamemaker_win import WinFile          # noqa: E402

FAIL = []

FONT_HEADER = 44
GLYPH_REC = 16


def check(name, cond, extra=""):
    if cond:
        print(f"  [ok] {name}")
    else:
        print(f"  [err] {name}  {extra}")
        FAIL.append(name)


def build_win(glyph_counts, corrupt_slots=(), bytecode=16):
    """拼一个只有 STRG + FONT 的最小 data.win。

    FONT 条目按「定长头 + G 个 u32 字形指针 + G 个 16 字节记录」紧凑排布，
    与真实文件一致。返回 (字节, FONT 数据区起点, 每项的起点)。
    """
    gen8 = struct.pack("<I", (bytecode << 8) | 1) + b"\x00" * 8
    head_len = 16 + len(gen8)              # FORM+size+GEN8+len+gen8 正文

    # ---- STRG：3 条字符串，名字供 FONT 头引用 ----
    names = [b"fnt_a", b"fnt_b", b"fnt_c"]
    strg_table = 4 + 4 * len(names)
    strg_body_len = strg_table + sum(4 + len(x) + 1 for x in names)
    strg_off = head_len                     # 第一个 chunk 的**名字**偏移
    strg_base = strg_off + 8                # 数据起点 = 名字 + 长度字段
    # 尾部补位到 128 的倍数：UTMT 读完最后一条字符串后要走到 128 边界才收手，
    # 那一步不能越过 chunk 末尾，所以真实文件的 STRG 长度是按 128 对齐的。
    strg_pad = (-(strg_base + strg_body_len)) % 128
    strg_body_len += strg_pad

    # ---- FONT：先算长度 ----
    font_sizes = [FONT_HEADER + g * (4 + GLYPH_REC) for g in glyph_counts]
    font_table = 4 + 4 * len(glyph_counts)
    font_off = strg_off + 8 + strg_body_len
    font_base = font_off + 8
    font_data_start = font_base + font_table
    starts, acc = [], font_data_start
    for sz in font_sizes:
        starts.append(acc)
        acc += sz
    font_span = font_table + sum(font_sizes)

    # ---- 拼 STRG ----
    strg = bytearray()
    strg += struct.pack("<I", len(names))
    p = strg_base + strg_table
    for nm in names:
        strg += struct.pack("<I", p)
        p += 4 + len(nm) + 1
    for nm in names:
        strg += struct.pack("<I", len(nm)) + nm + b"\x00"
    strg += b"\x00" * strg_pad
    assert len(strg) == strg_body_len

    # ---- 拼 FONT ----
    font = bytearray()
    font += struct.pack("<I", len(glyph_counts))
    slots = list(starts)
    for i in corrupt_slots:                # 故意改坏：指到别处去
        slots[i] = strg_base + 12
    for s in slots:
        font += struct.pack("<I", s)
    strg_name_ptr = strg_base + strg_table + 4
    for i, (g, s) in enumerate(zip(glyph_counts, starts)):
        header = bytearray(FONT_HEADER)
        struct.pack_into("<I", header, 0, strg_name_ptr)       # Name
        struct.pack_into("<I", header, 4, strg_name_ptr)       # DisplayName
        struct.pack_into("<I", header, 8, 12)                  # EmSize
        struct.pack_into("<I", header, 40, g)                  # 字形数
        font += header
        rec_base = s + FONT_HEADER + 4 * g
        for k in range(g):
            font += struct.pack("<I", rec_base + k * GLYPH_REC)
        for k in range(g):
            font += struct.pack("<HHHHHHH", 65 + k, 1, 2, 3, 4, 5, 6) + b"\x00" * 2
    assert len(font) == font_span, f"{len(font)} != {font_span}"

    out = bytearray()
    out += b"FORM" + b"\x00\x00\x00\x00" + b"GEN8" + struct.pack("<I", len(gen8))
    out += gen8
    out += b"STRG" + struct.pack("<I", len(strg)) + strg
    out += b"FONT" + struct.pack("<I", len(font)) + font
    struct.pack_into("<I", out, 4, len(out) - 8)
    return bytes(out), font_base, starts


def main() -> int:
    print("=" * 74)
    print("  FONT 重建不变式")
    print("=" * 74)

    counts = [3, 1, 5]
    raw, font_base, starts = build_win(counts)

    print("\n[干净的合成样本]")
    w = WinFile(raw)
    check("能解析", len(w.order) == 2, f"{w.order}")
    check("链完整", w.chain_ok)
    check("bytecode 16", w.bytecode() == 16, str(w.bytecode()))
    errs = [f for f in rb.diagnose(w) if f.level == "err"]
    check("干净文件诊断无 err", not errs, str(errs))
    lens, fs = rb.parse_fonts(w)
    check("FONT 条目数", len(lens) == len(counts), str(len(lens)))
    check("走出来的起点 == 构造的起点", [f.origin for f in lens] == starts)
    check("字形数解析正确", [f.glyph_count for f in lens] == counts)
    h, votes = rb.measure_header(w, w.chunks["FONT"],
                                 [struct.unpack_from("<I", raw,
                                                     font_base + 4 + 4 * i)[0]
                                  for i in range(len(counts))])
    check("量出的定长头 == 44", h == FONT_HEADER, f"{h}（{votes} 票）")

    print("\n[把 0 号和 2 号的指针改坏]")
    bad = [0, 2]
    raw2, _, _ = build_win(counts, corrupt_slots=bad)
    check("长度与干净版一致", len(raw2) == len(raw))
    w2 = WinFile(raw2)
    errs2 = [f for f in rb.diagnose(w2) if f.level == "err"]
    check("诊断报出 FONT 指针错误", any(f.chunk == "FONT" for f in errs2),
          str(errs2))

    new_raw, rep = rb.rebuild_bytes(raw2)
    check("重建后长度不变", len(new_raw) == len(raw2),
          f"{len(new_raw)} != {len(raw2)}")
    check("重建记录里有 FONT", any("FONT" in x for x in rep.rebuilt),
          str(rep.rebuilt))

    w3 = WinFile(new_raw)
    ch = w3.chunks["FONT"]
    n = w3.u32(ch.base)
    ptrs = [w3.u32(ch.base + 4 + 4 * i) for i in range(n)]
    check("重建后指针全在块内",
          all(ch.base <= p < ch.end for p in ptrs), str(ptrs))
    check("重建后指针 == 真实起点", ptrs == starts, str(ptrs))
    check("重建后指针递增", all(ptrs[i] < ptrs[i + 1] for i in range(n - 1)))
    errs3 = [f for f in rb.diagnose(w3) if f.level == "err" and f.chunk == "FONT"]
    check("重建后 FONT 无 err", not errs3, str(errs3))
    check("重建后 FONT 形状没变",
          [f.glyph_count for f, _ in zip(rb.parse_fonts(w3)[0], counts)] == counts)

    print("\n[只改指针表模式]")
    rb.POINTERS_ONLY = True
    try:
        raw4, rep4 = rb.rebuild_bytes(raw2)
    finally:
        rb.POINTERS_ONLY = False
    check("长度不变", len(raw4) == len(raw2))
    w4 = WinFile(raw4)
    ch4 = w4.chunks["FONT"]
    ptrs4 = [w4.u32(ch4.base + 4 + 4 * i) for i in range(w4.u32(ch4.base))]
    check("指针同样被修正", ptrs4 == starts, str(ptrs4))
    # 条目字节一个都不该动
    diff = sum(1 for i in range(len(raw2))
               if raw2[i] != raw4[i] and not (ch4.base + 4 <= i < ch4.base + 4 + 4 * len(counts)))
    check("条目字节未改动", diff == 0, f"差 {diff} 字节")

    print("\n[拒绝重建的情况]")
    # 第二个样本只有一个可信指针时，仍然要能走出来
    raw5, _, starts5 = build_win([2, 2], corrupt_slots=[1])
    new5, rep5 = rb.rebuild_bytes(raw5)
    w5 = WinFile(new5)
    ch5 = w5.chunks["FONT"]
    ptrs5 = [w5.u32(ch5.base + 4 + 4 * i) for i in range(w5.u32(ch5.base))]
    check("只有一个好指针也能重建", ptrs5 == starts5, str(ptrs5))

    print("\n" + "=" * 74)
    if FAIL:
        print(f"  失败 {len(FAIL)} 项: " + "; ".join(FAIL))
        return 1
    print("  全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
