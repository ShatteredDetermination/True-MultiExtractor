# -*- coding: utf-8 -*-
"""跑一遍 selftest/ 下的所有自测脚本。

用法：
    python selftest/run_all.py            # 全部
    python selftest/run_all.py samples    # 只跑真实样本回归（名字匹配）
    python selftest/run_all.py -v         # 显示每个脚本的完整输出

每个测试都是独立的脚本，用子进程跑，某个挂了不影响其他的。
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def discover(pattern: str = "") -> list:
    """找出所有 test_*.py，按名字排序。"""
    out = []
    for p in sorted(HERE.glob("test_*.py")):
        if pattern and pattern.lower() not in p.stem.lower():
            continue
        out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="跑 selftest 下的全部测试")
    ap.add_argument("pattern", nargs="?", default="", help="只跑名字匹配的测试")
    ap.add_argument("-v", "--verbose", action="store_true", help="显示完整输出")
    args = ap.parse_args()

    tests = discover(args.pattern)
    if not tests:
        print(f"没找到测试脚本（{HERE}）")
        return 1

    print("=" * 70)
    print(f"  运行 {len(tests)} 个自测脚本")
    print("=" * 70)

    results = []
    for path in tests:
        print(f"\n---- {path.name} " + "-" * max(0, 50 - len(path.name)))
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        out = proc.stdout.decode("utf-8", "replace")
        dt = time.time() - t0

        if args.verbose or proc.returncode != 0:
            print(out)
        else:
            # 只显示最后几行，避免刷屏
            lines = [ln for ln in out.splitlines() if ln.strip()]
            for ln in lines[-6:]:
                print("   " + ln)

        ok = proc.returncode == 0
        results.append((path.name, ok, dt))
        print(f"   -> {'通过' if ok else '失败'}  ({dt:.1f}s)")

    print("\n" + "=" * 70)
    print("  汇总")
    print("=" * 70)
    for name, ok, dt in results:
        print(f"  [{'OK' if ok else '!!'}] {name:<32} {dt:6.1f}s")
    failed = sum(1 for _n, ok, _d in results if not ok)
    print(f"\n  {len(results) - failed}/{len(results)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
