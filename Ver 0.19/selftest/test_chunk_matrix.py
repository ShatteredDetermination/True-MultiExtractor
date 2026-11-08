# -*- coding: utf-8 -*-
"""逐个 chunk 的单块重建验收矩阵。

对每个「条目带字符串指针」的 chunk：拼一份只含 GEN8 + STRG + 该 chunk 的
文件，丢给原版 UTMT，看它能不能把这个块**读完**。

单块隔离的好处：这一块如果还引用了别的 chunk（比如 SPRT 引用 TPAG），
会立刻暴露出来 —— 那种情况再补「对象槽位」，而不是先猜。

判据：UTMT 的报错不能落在该 chunk 内部。
"""

import subprocess
import sys
from pathlib import Path

# 控制台可能是 cp936：矩阵会打箭头之类的符号，先确保 stdout 能写 UTF-8，
# 否则一行诊断信息就能把整个脚本打成「失败」。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

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

# 先挑「结构最简单、只靠字符串就能自洽」的一批
TARGETS = ("SOND", "BGND", "PATH", "SCPT", "SHDR", "TMLN", "DAFL",
           "FONT", "GLOB", "SPRT", "OBJT", "ROOM", "CODE", "FUNC", "VARI", "AGRP")

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


def build_one(w, table, name: str) -> bytes:
    """GEN8 + STRG + 目标 chunk。目标 chunk 引用的块按需要一起带上。"""
    chunks = [ast.DataFile.gen8_chunk(w), table]
    objects = {}
    code = None

    if name in ("FONT",):
        model = ast.FontTable.from_win(w, strings=table)
    elif name == "GLOB":
        try:
            model = ast.StringListChunk.from_win(w, name, table=table)
        except ast.ModelError:
            model = ast.RawChunk(name, w.raw[w.chunks[name].base:w.chunks[name].end])
    else:
        if name in ("SPRT",):
            tp = ast.OpaqueListChunk.from_win(w, "TPAG")
            chunks.append(tp)
            objects["TPAG"] = {r.origin: r for r in tp.entries}
        if name in ("SCPT", "FUNC", "VARI"):
            code = ast.RebasedListChunk.from_win(w, "CODE")
            chunks.append(code)
            objects["CODE"] = {r.origin: r for r in ()}   # CODE 不再逐条引用
        if name == "FUNC" and code is not None:
            model = ast.FunctionTable.from_win(w, table=table, code=code)
        elif name == "VARI" and code is not None:
            model = ast.VariableTable.from_win(w, table=table, code=code)
        else:
            model = ast.VariableListChunk.from_win(w, name, strings=table,
                                                   objects=objects)
    chunks.append(model)
    return ser.build(ast.DataFile(chunks)).data


def utmt_error(path: Path):
    res = subprocess.run([str(UTMT), "load", str(path), "-v"],
                         capture_output=True, text=True, errors="replace",
                         encoding="utf-8")
    text = (res.stdout or "") + (res.stderr or "")
    reached = [ln.split("Reading chunk ")[-1].strip()
               for ln in text.splitlines() if "Reading chunk " in ln]
    err = [ln.strip() for ln in text.splitlines()
           if "Exception" in ln or "never read" in ln
           or "Reading out of" in ln or "Padding error" in ln
           or "at 0" in ln or "Unknown chunk" in ln]
    return res.returncode, reached, err, text


def main() -> int:
    print("=" * 74)
    print("  V0.15E 单块重建验收矩阵")
    print("=" * 74)
    if not UTMT.exists():
        print("  [skip] 本机没有 UTMT（Slim 环境下正常），矩阵不跑")
        print("=" * 74)
        return 0

    for src in SAMPLES:
        if not src.exists():
            continue
        w = WinFile.load(src)
        table = ast.StringTable.from_win(w)
        print(f"\n[{src.name}]")
        for name in TARGETS:
            if name not in w.chunks or w.chunks[name].size < 8:
                continue
            try:
                data = build_one(w, table, name)
            except Exception as exc:                # noqa: BLE001
                check(False, f"{name} 建模/写出", f"{type(exc).__name__}: {exc}")
                continue
            p = Path(r"C:\Users\Administrator\AppData\Local\Temp") / f"one_{src.stem}_{name}.win"
            p.write_bytes(data)
            code, reached, err, text = utmt_error(p)
            got = name in reached
            inside = f"while reading chunk {name}" in text
            first = err[0] if err else ""
            check(got and not inside, f"{name}: UTMT 读完",
                  f"{first[:70]}")
            if inside:
                print(f"         -> {first[:100]}")
    print("\n" + "=" * 74)
    print(f"  PASS {PASS}   FAIL {FAIL}（信息性矩阵：失败的块还没建模完）")
    print("=" * 74)
    # 这是进度矩阵，不是回归门槛：失败的块是「下一步的工作清单」。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
