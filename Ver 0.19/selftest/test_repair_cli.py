# -*- coding: utf-8 -*-
"""--repair 的端到端自测：真跑一次 CLI，检查产出、事件流、以及能被 UTMT 载入。

V0.15E 起 ``--repair`` 先走全量重建（长度会变），只有遇到还没建模的布局才
退回「等长原位补丁」。所以这里不再锁长度，改锁 UTMT 能不能完整加载。

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
    print("  --repair 端到端")
    print("=" * 74)

    if not SAMPLE.exists():
        print(f"  [跳过] 样本不存在: {SAMPLE}")
        print("\n  全部通过（跳过）")
        return 0

    with tempfile.TemporaryDirectory(prefix="me_repair_") as tmp:
        out = Path(tmp)
        res = subprocess.run(
            [sys.executable, str(ROOT / "main.py"), "--json", "--repair",
             "-o", str(out), str(SAMPLE)],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600)

        check("退出码 0", res.returncode == 0,
              f"实得 {res.returncode}  {res.stderr[-300:]}")
        dest = out / (SAMPLE.stem + "_rebuilt" + SAMPLE.suffix)
        check("产出了 <名字>_rebuilt.win", dest.exists(), str(dest))
        if dest.exists():
            # V0.15E：--repair 先走全量重建（长度会变），只有重建不可用才退回
            # 原位补丁（长度不变）。所以这里不锁长度，改锁「UTMT 能完整加载」。
            if not UTMT.exists():
                print("  [跳过] 没有 UTMT，长度不判定")
            else:
                r = subprocess.run([str(UTMT), "load", str(dest), "-v"],
                                   capture_output=True, text=True,
                                   errors="replace", encoding="utf-8")
                text = (r.stdout or "") + (r.stderr or "")
                err = [ln.strip() for ln in text.splitlines()
                       if "Exception" in ln or "bounds" in ln
                       or "never read" in ln]
                check("产出能被 UTMT 完整加载", r.returncode == 0,
                      err[-1][:100] if err else f"exit={r.returncode}")

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
        result = next((e for e in events if e.get("t") == "result"), None)
        check("发出了 result 事件", result is not None, str(kinds))
        if result:
            check("result.written == 1", result.get("written") == 1,
                  str(result.get("written")))
            check("result.ok 为真", bool(result.get("ok")))
            check("result.out_dir 指向输出目录",
                  Path(str(result.get("out_dir", ""))).resolve() == out.resolve(),
                  str(result.get("out_dir")))
        check("收尾是 end 事件", kinds[-1:] == ["end"], str(kinds[-3:]))

    print("\n" + "=" * 74)
    if FAIL:
        print(f"  失败 {len(FAIL)} 项: " + "; ".join(FAIL))
        return 1
    print("  全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
