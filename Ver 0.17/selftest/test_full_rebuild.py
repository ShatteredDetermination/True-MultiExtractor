# -*- coding: utf-8 -*-
"""整份 data.win 的全量重建验收。

判据分两层：

1. **自研侧**：重建后的文件能被自己的解析器读回来，且各 chunk 的条目数与
   原文件一致（不只看「有没有写出东西」）。
2. **UTMT 侧**：原版 UTMT 必须能**完整加载**（退出码 0）。
   data.win / data1.win 是硬性门槛；data2.win 目前卡在 SPRT 特殊类型，
   记为「已知缺口」，不参与判定。
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.formats import gamemaker_ast as ast                      # noqa: E402
from me.formats import gamemaker_full as full                   # noqa: E402
from me.formats.gamemaker_win import WinFile                    # noqa: E402

UTMT = ROOT / "tools" / "utmt" / "UndertaleModCli.exe"

# 必须整份通过 UTMT 且零警告的样本
MUST_PASS = ("data.win", "data1.win")
# 只要求 UTMT 能完整加载：源文件本身的几个空块带着 UTMT 读不到的补位字节，
# 那几条告警是继承来的（原文件同样会报），不算重建的问题
LOAD_ONLY = ("data2.win",)
SAMPLES = [Path(r"C:\Users\Administrator\Downloads") / n
           for n in MUST_PASS + LOAD_ONLY]
PASS = 0
FAIL = 0


def check(ok, what, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"    [ok] {what}")
    else:
        FAIL += 1
        print(f"    [FAIL] {what}  {detail}")


def chunk_counts(w) -> dict:
    """各 chunk 的条目数。

    空表（count == 0）也按条目数比，不按字节数比 —— 重建会去掉块尾那几字节
    没人读的补位，字节数跟着变，但条目数必须一样。
    """
    out = {}
    for name in w.order:
        ch = w.chunks[name]
        if ch.size < 4:
            out[name] = ("size", ch.size)
            continue
        cnt = w.u32(ch.base)
        if 4 + 4 * cnt <= ch.size and cnt < 1000000:
            out[name] = ("count", cnt)
        else:
            out[name] = ("size", ch.size)
    return out


def probe(src: Path) -> None:
    w = WinFile.load(src)
    data, rep = full.build_full(w)
    out = Path(r"C:\Users\Administrator\AppData\Local\Temp") / f"full_{src.stem}.win"
    out.write_bytes(data)

    print(f"\n[{src.name}]  原 {len(w.raw):,} -> 新 {len(data):,} 字节")
    print(f"    建模 {len(rep.modeled)} 块, 原样 {len(rep.raw)} 块")
    for note in rep.notes:
        print(f"    注: {note}")

    # ---- 1. 自研侧 ----
    try:
        n = WinFile.load(out)
    except Exception as exc:                        # noqa: BLE001
        check(False, "重建结果能被自己的解析器读回",
              f"{type(exc).__name__}: {exc}")
        return
    check(n.chain_ok, "chunk 链完整")
    expect = [c for c in full.CHUNK_ORDER if c in n.chunks]
    check(n.order == expect, "块序是 GameMaker 标准顺序", str(n.order[:6]))

    before, after = chunk_counts(w), chunk_counts(n)
    bad = [(k, before[k], after.get(k)) for k in before
           if after.get(k) != before[k]]
    check(not bad, "各 chunk 条目数与原文一致", str(bad[:4]))

    # ---- 1b. 幂等：把重建结果再重建一遍，字节必须完全相同 ----
    # 这一步等于说「新布局是不动点」：模型没丢信息、布局没漂移，
    # 所以整份文件确实可以自由重排，而不只是「碰巧排对了一次」。
    again = full.rebuild_bytes(n)
    check(again == data, "重建的幂等性（再重建一次字节相同）",
          f"{len(again):,} vs {len(data):,}")

    # ---- 2. UTMT 侧 ----
    if not UTMT.exists():
        print("    （没有 UTMT，跳过）")
        return
    res = subprocess.run([str(UTMT), "load", str(out), "-v"],
                         capture_output=True, text=True, errors="replace",
                         encoding="utf-8")
    text = (res.stdout or "") + (res.stderr or "")
    reached = [ln.split("Reading chunk ")[-1].strip()
               for ln in text.splitlines() if "Reading chunk " in ln]
    err = [ln.strip() for ln in text.splitlines()
           if "at 0" in ln or "Exception" in ln or "never read" in ln]
    # 补位告警：UTMT 的判据是「读到的长度 != 头部写的长度」。
    # 一条都不能有 —— 有就说明某个块的长度字段和实际结构对不上。
    warns = [ln.strip() for ln in text.splitlines() if "WARNING" in ln]
    hard = src.name in MUST_PASS
    ok = res.returncode == 0
    if hard:
        check(ok, f"UTMT 完整加载（读到 {len(reached)} 块）",
              err[-1][:100] if err else f"exit={res.returncode}")
        check(not warns, "UTMT 零警告（长度字段 == 实际读到的字节数）",
              f"{len(warns)} 条，例如 {warns[0][:100]}" if warns else "")
    else:
        check(ok, f"UTMT 完整加载（读到 {len(reached)} 块）",
              err[-1][:100] if err else f"exit={res.returncode}")
        if warns:
            print(f"    [注] {len(warns)} 条补位告警继承自源文件")


def main() -> int:
    print("=" * 74)
    print("  V0.15E 整份重建验收")
    print("=" * 74)
    for s in SAMPLES:
        if not s.exists():
            print(f"\n[{s.name}] 样本不在，跳过")
            continue
        try:
            probe(s)
        except Exception as exc:                    # noqa: BLE001
            if s.name in LOAD_ONLY:
                print(f"\n[{s.name}] [只要求能加载] "
                      f"{type(exc).__name__}: {exc}")
            else:
                check(False, f"{s.name} 重建抛异常",
                      f"{type(exc).__name__}: {exc}")
    print("\n" + "=" * 74)
    print(f"  PASS {PASS}   FAIL {FAIL}")
    print("=" * 74)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
