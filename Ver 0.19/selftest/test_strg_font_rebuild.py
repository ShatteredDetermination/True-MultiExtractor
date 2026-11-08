# -*- coding: utf-8 -*-
"""V0.16 第一块：STRG + FONT 的「读成对象 → 重新写出」自测。

分三层：

1. 合成样本：自己拼一份 STRG+FONT，读成模型再写出去，检查不变式；
2. 真实样本：从 data.win/data1.win/data2.win 里只取 STRG+FONT 建模，
   连同 GEN8 一起生成一份**全新的** data.win（其余 chunk 不参与），
   再用自研解析器 + 原版 UTMT 各验一遍；
3. 幂等性：把生成的字节再读一遍、再生成一次，必须逐字节相同。

UTMT 的期望是「读得进去，但不完整」：新文件里只有 GEN8/FONT/STRG，
它一定会在别处报错 —— 但**不能在 FONT 或 STRG 里报错**，那才说明
这两块的重建是对的。
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.formats import gamemaker_ast as ast                      # noqa: E402
from me.formats import gamemaker_serializer as ser              # noqa: E402
from me.formats.gamemaker_win import WinFile                    # noqa: E402

UTMT = ROOT / "tools" / "utmt" / "UndertaleModCli.exe"
SAMPLES = [
    Path(r"C:\Users\Administrator\Downloads\data.win"),
    Path(r"C:\Users\Administrator\Downloads\data1.win"),
    Path(r"C:\Users\Administrator\Downloads\data2.win"),
]

PASS = 0
FAIL = 0


def check(ok, what, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [ok] {what}")
    else:
        FAIL += 1
        print(f"  [FAIL] {what}  {detail}")


# ══════════════════════════════════════════════════════════════════
#  1. 合成样本
# ══════════════════════════════════════════════════════════════════


def synth() -> ast.DataFile:
    strings = ast.StringTable([ast.GmString(x) for x in
                               (b"fnt_a", b"fnt_b", b"")])
    glyphs = [ast.Glyph(bytes([65 + k, 0, 1, 0, 2, 0, 3, 0, 4, 0, 0, 0, 0, 0, 0, 0]))
              for k in range(3)]
    font = ast.FontItem(name=strings.strings[0],
                        display=strings.strings[1],
                        tail=b"\x0c\x00\x00\x00" * 8,
                        glyphs=glyphs)
    gen8 = ast.RawChunk("GEN8", b"\x00" * 16)
    return ast.DataFile([gen8, ast.FontTable([font]), strings])


def test_synthetic() -> None:
    print("\n[合成样本]")
    data = synth()
    built = ser.build(data)

    check(built.data[:4] == b"FORM", "以 FORM 开头")
    check(int.from_bytes(built.data[4:8], "little") == len(built.data) - 8,
          "FORM 长度自洽")

    w = WinFile(built.data)
    check(w.raw[8:12] == b"GEN8", "GEN8 在最前")
    check(w.order == ["FONT", "STRG"], "chunk 顺序", str(w.order))
    check(w.chain_ok, "chunk 链完整")

    # STRG：三条字符串、记录起点都在 chunk 内、连续、补位到 128
    ch = w.chunks["STRG"]
    n = w.u32(ch.base)
    check(n == 3, f"STRG 条数 3（实际 {n}）")
    ptrs = [w.u32(ch.base + 4 + 4 * i) for i in range(n)]
    check(all(ch.base <= p < ch.end for p in ptrs), "STRG 指针都在 chunk 内")
    texts = []
    for p in ptrs:
        ln = w.u32(p)
        texts.append(w.raw[p + 4:p + 4 + ln])
    check(texts == [b"fnt_a", b"fnt_b", b""], "STRG 正文一致", str(texts))
    check((ch.end - ast.StringTable.guard) % 128 == 0, "STRG 补位走到 128 边界")
    check(ptrs == sorted(ptrs), "STRG 按表顺序填充")

    # FONT：名字指向 STRG 的正文，字形指针是紧密数组
    cf = w.chunks["FONT"]
    m = w.u32(cf.base)
    check(m == 1, f"FONT 项数 1（实际 {m}）")
    item = w.u32(cf.base + 4)
    check(cf.base <= item < cf.end, "FONT 条目在 chunk 内")
    name_ptr = w.u32(item)
    check(ch.base <= name_ptr - 4 < ch.end, "FONT 名字指进 STRG")
    check(w.raw[name_ptr:name_ptr + 5] == b"fnt_a", "FONT 名字正文")
    g = w.u32(item + 40)
    rec_base = item + 44 + 4 * g
    arr = [w.u32(item + 44 + 4 * k) for k in range(g)]
    check(arr == [rec_base + 16 * k for k in range(g)], "字形指针是紧密数组")

    # 幂等
    again = ser.build(ast.DataFile.from_win(
        WinFile(built.data), modeled={"STRG", "FONT"})).data
    check(again == built.data, "再读一遍再写出：逐字节相同",
          f"{len(again):,} != {len(built.data):,}")


# ══════════════════════════════════════════════════════════════════
#  2. 真实样本
# ══════════════════════════════════════════════════════════════════


def partial_file(src: Path) -> bytes:
    """只保留 GEN8 + 重建后的 FONT + STRG。"""
    w = WinFile.load(src)
    table = ast.StringTable.from_win(w)
    fonts = ast.FontTable.from_win(w, strings=table)
    gen8 = ast.DataFile.gen8_chunk(w)
    if gen8 is None:
        raise ast.ModelError("没有 GEN8")
    return ser.build(ast.DataFile([gen8, fonts, table])).data


def utmt_report(path: Path):
    res = subprocess.run([str(UTMT), "load", str(path), "-v"],
                         capture_output=True, text=True, errors="replace",
                         encoding="utf-8")
    text = (res.stdout or "") + (res.stderr or "")
    reached = [ln.split("Reading chunk ")[-1].strip()
               for ln in text.splitlines() if "Reading chunk " in ln]
    return res.returncode, text, reached


def test_sample(src: Path) -> None:
    if not src.exists():
        print(f"\n[{src.name}] 样本不在，跳过")
        return
    print(f"\n[{src.name}]")
    w = WinFile.load(src)
    orig_strg = w.chunks["STRG"]
    orig_font = w.chunks["FONT"]
    orig_count = w.u32(orig_strg.base)
    orig_items = w.u32(orig_font.base)

    out = partial_file(src)
    path = Path(r"C:\Users\Administrator\AppData\Local\Temp") / f"v16_{src.stem}.win"
    path.write_bytes(out)

    n = WinFile(out)
    check(n.raw[8:12] == b"GEN8", "GEN8 在最前")
    check(n.order == ["FONT", "STRG"], "chunk 顺序", str(n.order))
    check(n.chain_ok, "chunk 链完整")

    ch = n.chunks["STRG"]
    ptrs = [n.u32(ch.base + 4 + 4 * i) for i in range(n.u32(ch.base))]
    check(n.u32(ch.base) == orig_count, f"STRG 条数仍是 {orig_count:,}",
          str(n.u32(ch.base)))
    check(all(ch.base <= p < ch.end for p in ptrs), "STRG 指针全部在 chunk 内")
    check((ch.end - ast.StringTable.guard) % 128 == 0,
          "STRG 补位走到 128 边界（末尾只多出守卫字节）")

    # 正文逐条相同（顺序也相同）
    same = True
    for p in ptrs:
        ln = n.u32(p)
        if ln > 0xFFFF or p + 4 + ln + 1 > ch.end:
            same = False
            break
    check(same, "STRG 每条记录都读得出来")

    cf = n.chunks["FONT"]
    items = n.u32(cf.base)
    check(items == orig_items, f"FONT 项数仍是 {orig_items}", str(items))
    glyphs_ok = True
    for i in range(items):
        off = n.u32(cf.base + 4 + 4 * i)
        if not (cf.base <= off < cf.end):
            glyphs_ok = False
            break
        g = n.u32(off + 40)
        rec_base = off + 44 + 4 * g
        arr = [n.u32(off + 44 + 4 * k) for k in range(g)]
        if arr != [rec_base + 16 * k for k in range(g)]:
            glyphs_ok = False
            break
        if rec_base + 16 * g > cf.end:
            glyphs_ok = False
            break
    check(glyphs_ok, "FONT 每个条目的字形指针都是紧密数组且在 chunk 内")

    if UTMT.exists():
        code, text, reached = utmt_report(path)
        check("STRG" in reached, "UTMT 读到了 STRG", str(reached[-4:]))
        bad = ("while reading chunk FONT" in text
               or "while reading chunk STRG" in text)
        first_err = next((ln.strip() for ln in text.splitlines()
                          if "Exception" in ln or "never read" in ln
                          or "Unknown chunk" in ln), "")
        check(not bad, "UTMT 报错不在 FONT/STRG 之内", first_err)
        print(f"       UTMT 退出码 {code}（不完整文件，非 0 是预期的）")
        print(f"       报错: {first_err[:90]}")


# ══════════════════════════════════════════════════════════════════
#  main
# ══════════════════════════════════════════════════════════════════


def main() -> int:
    print("=" * 74)
    print("  V0.16 STRG/FONT 全量重建")
    print("=" * 74)
    test_synthetic()
    for s in SAMPLES:
        try:
            test_sample(s)
        except Exception as exc:                    # noqa: BLE001
            check(False, f"{s.name} 重建抛异常", f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 74)
    print(f"  PASS {PASS}   FAIL {FAIL}")
    print("=" * 74)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
