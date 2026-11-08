# -*- coding: utf-8 -*-
"""``--rebuild`` 的端到端自测：真跑一次 CLI，检查产出、事件流、UTMT 可载入。

为什么单独一条测试
------------------
``--repair`` 是「在原字节上缝补、长度不变」的老路线；``--rebuild`` 是
「读成语义模型 → 重新排布 → 整份写出」的新路线，长度会变。两条路线对产物的
保证不一样，所以分开测：

* ``--rebuild``：长度**允许**变化，但必须能被原版 UTMT 完整加载且零警告；
* ``--repair``：见 test_repair_cli.py。

没有真实样本时跳过（别人拿到这个项目时不会有 Downloads\\data.win）。
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UTMT = ROOT / "tools" / "utmt" / "UndertaleModCli.exe"
SAMPLE = Path(r"C:\Users\Administrator\Downloads\data.win")

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print(f"  [ok] {name}")
    else:
        print(f"  [err] {name}  {extra}")
        FAIL.append(name)


def main() -> int:
    print("=" * 74)
    print("  --rebuild 端到端")
    print("=" * 74)

    if not SAMPLE.exists():
        print(f"  [跳过] 样本不存在: {SAMPLE}")
        print("\n  全部通过（跳过）")
        return 0

    with tempfile.TemporaryDirectory(prefix="me_rebuild_") as tmp:
        out = Path(tmp)
        res = subprocess.run(
            [sys.executable, str(ROOT / "main.py"), "--json", "--rebuild",
             "-o", str(out), str(SAMPLE)],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900)

        check("退出码 0", res.returncode == 0,
              f"实得 {res.returncode}  {res.stderr[-300:]}")
        dest = out / (SAMPLE.stem + "_rebuilt" + SAMPLE.suffix)
        check("产出了 <名字>_rebuilt.win", dest.exists(), str(dest))
        if not dest.exists():
            print("\n  失败项: " + ", ".join(FAIL))
            return 1

        # ---- 产物 ----
        check("长度变了（整份重排，不再等长）",
              dest.stat().st_size != SAMPLE.stat().st_size,
              "长度与原文件相同，说明没走重建")
        check("文件头还是 FORM",
              dest.read_bytes()[:4] == b"FORM")

        # ---- 事件流 ----
        events = []
        for line in (res.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
        kinds = [e.get("t") for e in events]
        check("事件流以 start 开头", kinds[:1] == ["start"], str(kinds[:3]))
        check("end 只有一条", kinds.count("end") == 1,
              f"实得 {kinds.count('end')} 条")
        result = next((e for e in events if e.get("t") == "result"), None)
        check("发出了 result 事件", result is not None, str(kinds))
        if result:
            check("result.written == 1", result.get("written") == 1,
                  str(result.get("written")))
            check("result.ok 为真", bool(result.get("ok")))
            check("result.out_dir 指向输出目录",
                  Path(result.get("out_dir", "")) == out,
                  str(result.get("out_dir")))
            notes = result.get("notes") or []
            check("notes 里写明建模块数",
                  any("建模" in str(n) for n in notes), str(notes[:3]))

        # ---- UTMT ----
        if not UTMT.exists():
            print("  [跳过] 没有 UTMT")
        else:
            r = subprocess.run([str(UTMT), "load", str(dest), "-v"],
                               capture_output=True, text=True, errors="replace",
                               encoding="utf-8")
            text = (r.stdout or "") + (r.stderr or "")
            err = [ln.strip() for ln in text.splitlines()
                   if "Exception" in ln or "bounds" in ln or "never read" in ln]
            warns = [ln.strip() for ln in text.splitlines() if "WARNING" in ln]
            check("UTMT 完整加载（exit 0）", r.returncode == 0,
                  err[-1][:100] if err else f"exit={r.returncode}")
            check("UTMT 零警告", not warns,
                  f"{len(warns)} 条，例如 {warns[0][:100]}" if warns else "")

    print()
    if FAIL:
        print("  失败项: " + ", ".join(FAIL))
        return 1
    print("  全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
