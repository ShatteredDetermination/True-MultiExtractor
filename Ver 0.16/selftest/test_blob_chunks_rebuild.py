# -*- coding: utf-8 -*-
"""V0.15E 第二块：TXTR + AUDO 的重建自测。

TXTR 和 AUDO 是「被指针指着的数据块」：

* TXTR 每条数据要落在 128 的倍数上（UTMT 读完上一条后按 0x80 补位）；
* AUDO 每条要落在 4 的倍数上，且最后一条不补。

这两条在 V0.15 的「原 span 里缝补」路线下是死结（data2.win 差 427 字节），
在长度可变的重建里不存在：排不下就让 chunk 长一点。

验收方式与 STRG/FONT 一样：只取几个块拼一个**残缺但自洽**的文件，
让原版 UTMT 去读，报错必须停在「缺后续 chunk」，不能停在 TXTR/AUDO 里。
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


def build_partial(src: Path) -> bytes:
    """GEN8 + FONT + TPAG(原字节) + STRG + TXTR + AUDO —— 按 GM 的块序。"""
    w = WinFile.load(src)
    table = ast.StringTable.from_win(w)
    chunks = [ast.DataFile.gen8_chunk(w)]
    for name in ("FONT", "TPAG", "STRG", "TXTR", "AUDO"):
        if name not in w.chunks:
            continue
        ch = w.chunks[name]
        if name == "FONT":
            chunks.append(ast.FontTable.from_win(w, strings=table))
        elif name == "STRG":
            chunks.append(table)
        elif name == "TXTR":
            chunks.append(ast.TextureTable.from_win(w))
        elif name == "AUDO":
            chunks.append(ast.AudioTable.from_win(w))
        else:
            chunks.append(ast.OpaqueListChunk.from_win(w, name))
    return ser.build(ast.DataFile(chunks)).data


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
    n_tex = w.u32(w.chunks["TXTR"].base)
    n_aud = w.u32(w.chunks["AUDO"].base) if "AUDO" in w.chunks else 0

    out = build_partial(src)
    path = Path(r"C:\Users\Administrator\AppData\Local\Temp") / f"v15e_{src.stem}.win"
    path.write_bytes(out)
    n = WinFile(out)

    check(n.order == [c for c in ("FONT", "TPAG", "STRG", "TXTR", "AUDO")
                      if c in n.chunks], "chunk 顺序", str(n.order))
    check(n.chain_ok, "chunk 链完整")

    # TXTR：条数、每条数据 128 对齐、末尾不越过 chunk
    ct = n.chunks["TXTR"]
    cnt = n.u32(ct.base)
    check(cnt == n_tex, f"TXTR 条数仍是 {n_tex}", str(cnt))
    ptrs = [n.u32(ct.base + 4 + 4 * i) for i in range(cnt)]
    check(all(ct.base <= p < ct.end for p in ptrs), "TXTR 条目指针都在 chunk 内")
    starts = []
    for i in range(cnt):
        e = ptrs[i]
        # 条目里找指向 PNG 的字段
        val = next((n.u32(e + off) for off in range(0, 32, 4)
                    if n.raw[n.u32(e + off):n.u32(e + off) + 8] == ast.PNG_SIG), 0)
        starts.append(val)
    check(all(v % 128 == 0 for v in starts), "TXTR 每条数据都从 128 边界开始",
          str([v % 128 for v in starts][:6]))
    check(all(a < b for a, b in zip(starts, starts[1:])), "TXTR 数据按条目顺序排")

    # AUDO：条数、条目 4 对齐、长度字段自洽
    ca = n.chunks["AUDO"]
    acnt = n.u32(ca.base)
    check(acnt == n_aud, f"AUDO 条数仍是 {n_aud}", str(acnt))
    aptrs = [n.u32(ca.base + 4 + 4 * i) for i in range(acnt)]
    ok = all(ca.base <= p < ca.end for p in aptrs)
    ok = ok and all(p % 4 == 0 for p in aptrs[1:])
    for p in aptrs:
        ln = n.u32(p)
        ok = ok and p + 4 + ln <= ca.end
    check(ok, "AUDO 条目都在 chunk 内、4 对齐、长度自洽")

    if UTMT.exists():
        code, text, reached = utmt_report(path)
        for name in ("TXTR", "AUDO"):
            check(name in reached, f"UTMT 读到了 {name}", str(reached[-5:]))
        bad = any(f"while reading chunk {x}" in text for x in ("TXTR", "AUDO"))
        first_err = next((ln.strip() for ln in text.splitlines()
                          if "Exception" in ln or "never read" in ln), "")
        check(not bad, "UTMT 报错不在 TXTR/AUDO 之内", first_err[:90])
        print(f"       UTMT 退出码 {code}，报错: {first_err[:80]}")


def main() -> int:
    print("=" * 74)
    print("  V0.15E TXTR / AUDO 全量重建")
    print("=" * 74)
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
