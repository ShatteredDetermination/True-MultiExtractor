# -*- coding: utf-8 -*-
"""进度账本（me/progress.py）的不变式自测。

账本要保证四条：
  1. 全局 done 只增不减；
  2. 全局 total 只增不减；
  3. 任何一条读数 total >= done；
  4. finish() 之后 done == total。

下面的合成序列照抄实测见过的几种形态（包括 0.13 那种 3990 -> 3 的回跳）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.progress import Ledger        # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print(f"  [ok] {name}")
    else:
        print(f"  [err] {name}  {extra}")
        FAIL.append(name)


def feed(seq, do_finish=True):
    """喂一串 (done, total, label)，收集账本送出去的全局读数。"""
    seen = []
    led = Ledger(sink=lambda d, t, l="": seen.append((d, t)))
    for item in seq:
        led.report(*item)
    if do_finish:
        led.finish()
    return led, seen


def invariants(seen):
    """返回四条不变式的判定结果。"""
    mon_done = all(seen[i][0] >= seen[i - 1][0] for i in range(1, len(seen)))
    mon_total = all(seen[i][1] >= seen[i - 1][1] for i in range(1, len(seen)))
    covers = all(t >= d for d, t in seen)
    closed = bool(seen) and seen[-1][0] == seen[-1][1]
    return mon_done, mon_total, covers, closed


CASES = (
    # 名字, 喂进去的序列, 期望的最后一条读数
    ("单层恒定 (GameMaker 603)", [(0, 603, "开始"), (80, 603, "spr_a"),
                                  (603, 603, "spr_z")], (603, 603)),
    ("两层累加 (603 + 17 散落文件)", [(0, 603, "开始"), (603, 603, "完"),
                                      (1, 17, "a.txt"), (17, 17, "z.txt")],
     (620, 620)),
    ("回跳序列 (asar 3990 -> 散落 3)",
     [(0, 3990, "开始"), (3990, 3990, "完"), (1, 3, "a"), (3, 3, "c")],
     (3993, 3993)),
    ("子解析器不闭合 (Clickteam 停在 2188/2211)",
     [(0, 2211, "开始"), (2188, 2211, "图 2188")], (2211, 2211)),
    ("总数未知 (total=0)", [(0, 0, "?"), (5, 0, "x"), (9, 0, "y")], (9, 9)),
    ("层内 done 超出 total", [(0, 10, "开始"), (12, 10, "多了")], (12, 12)),
)


def main() -> int:
    print("=" * 74)
    print("  进度账本不变式")
    print("=" * 74)

    for name, seq, want_last in CASES:
        print(f"\n[{name}]")
        led, seen = feed(seq)
        for d, t in seen:
            print(f"      -> {d}/{t}")
        mon_done, mon_total, covers, closed = invariants(seen)
        check("done 单调", mon_done, f"{seen}")
        check("total 单调", mon_total, f"{seen}")
        check("total >= done", covers, f"{seen}")
        check("收工 done == total", closed, f"末条 {seen[-1] if seen else None}")
        check(f"末条读数 == {want_last}", seen and seen[-1] == want_last,
              f"实得 {seen[-1] if seen else None}")

    print("\n[段计数]")
    led, seen = feed([(0, 603, ""), (603, 603, ""), (1, 17, ""), (17, 17, "")])
    check("两层 = 2 段", led.stages == 2, f"实得 {led.stages}")

    print("\n[reset 清零]")
    led, _ = feed([(1, 10, "")])
    led.reset()
    check("reset 后 stages 归零", led.stages == 0)
    check("reset 后 last 归零", led.last == (0, 0, ""))
    led.report(1, 4, "x")
    check("reset 后重新起算", led.last[:2] == (1, 4), f"实得 {led.last}")

    print("\n[空账本 finish 不炸]")
    led = Ledger()
    check("空账本 finish 返回 0/0", led.finish() == (0, 0))

    print("\n" + "=" * 74)
    if FAIL:
        print(f"  失败 {len(FAIL)} 项: " + "; ".join(FAIL))
        return 1
    print("  全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
