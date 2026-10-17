# -*- coding: utf-8 -*-
"""--repair 的端到端自测：真跑一次 CLI，检查产出、等长、以及 result 事件。

没有真实样本时跳过（别人拿到这个项目时不会有 Downloads\\data.win）。
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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
            check("长度与原文件一致",
                  dest.stat().st_size == SAMPLE.stat().st_size,
                  f"{dest.stat().st_size} != {SAMPLE.stat().st_size}")

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
