# -*- coding: utf-8 -*-
"""进度账本（me/progress.py）与进度条渲染（me/ui.render_progress）的自测。

账本要保证四条：
  1. 全局 done 只增不减；
  2. 全局 total 只增不减；
  3. 任何一条读数 total >= done；
  4. finish() 之后 done == total。

渲染要保证一条（实测被用户截图骂过）：**一个进度条只占一行**。
原实现把每帧固定补到 120 字符，控制台比 120 窄时就折行，`\r` 只回到折行后
那一行的行首，于是下载 UTMT 时刷了一屏幕进度条。下面用假的控制台（宽度可调）
把这件事钉成断言：帧内不许有换行、每帧不超过终端宽度、收工时恰好一个换行。
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


class FakeConsole:
    """冒充 stdout：可设宽度、可断言「帧内无换行」。"""

    def __init__(self, width: int):
        self.width = width
        self.frames = []          # 每次 write 的内容
        self.encoding = "utf-8"
        self.errors = "replace"

    def write(self, s: str) -> int:
        self.frames.append(s)
        return len(s)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True

    def reconfigure(self, **_kw) -> None:
        pass

    def joined(self) -> str:
        return "".join(self.frames)


def render_case(width: int):
    """在假控制台上跑一段下载式进度，返回 (frames, 每帧可见长度, 换行数)。"""
    import me.ui as ui

    fake = FakeConsole(width)
    real_stdout = sys.stdout
    real_tty = ui._IS_TTY
    real_size = ui._term_width
    # 直接钉死宽度，别去问真实终端
    ui._term_width = lambda: width
    ui._IS_TTY = True
    ui._progress_len["n"] = 0
    sys.stdout = fake
    try:
        total = 62585935
        for done in (0, total // 8, total // 4, total // 2,
                     total * 3 // 4, total):
            ui.render_progress(done, total, "下载 UTMT_CLI_v0.9.2.0-Windows.zip")
    finally:
        sys.stdout = real_stdout
        ui._IS_TTY = real_tty
        ui._term_width = real_size
        ui._progress_len["n"] = 0
    widths = []
    for f in fake.frames:
        body = f[1:] if f.startswith("\r") else f
        widths.append(len(body.rstrip("\n")))
    return fake.frames, widths, fake.joined().count("\n")


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

    print("\n[进度条渲染：一个进度条只占一行]")
    for width in (60, 80, 100, 120, 200):
        frames, widths, newlines = render_case(width)
        body = [f for f in frames if f.strip("\r\n ") != ""]
        check(f"宽度 {width}: 有帧输出", len(body) > 1, f"{len(frames)} 帧")
        check(f"宽度 {width}: 每帧不超过终端宽度",
              all(w <= width - 1 for w in widths),
              f"最宽 {max(widths) if widths else 0} > {width - 1}")
        # 帧内换行只允许出现在「收工」那一帧的最后
        inner = [f for f in frames[:-1] if "\n" in f]
        check(f"宽度 {width}: 帧内没有换行（不折行）", not inner,
              f"{inner[:2]}")
        check(f"宽度 {width}: 收工时恰好换行一次", newlines == 1,
              f"实得 {newlines}")
        check(f"宽度 {width}: 每帧都从行首覆盖", 
              all(f.startswith("\r") or f.startswith("\n") for f in frames),
              f"{frames[:1]}")

    print("\n[进度条渲染：total 未知时不乱写]")
    import me.ui as ui2
    fake2 = FakeConsole(80)
    real_stdout2, real_tty2, real_size2 = sys.stdout, ui2._IS_TTY, ui2._term_width
    ui2._term_width = lambda: 80
    ui2._IS_TTY = True
    ui2._progress_len["n"] = 0
    sys.stdout = fake2
    try:
        ui2.render_progress(5, 0, "未知总数")
        ui2.render_progress(9, 0, "未知总数")
    finally:
        sys.stdout = real_stdout2
        ui2._IS_TTY = real_tty2
        ui2._term_width = real_size2
        ui2._progress_len["n"] = 0
    text2 = fake2.joined()
    check("total=0 时不画百分比", "%" not in text2, text2[:80])
    check("total=0 时只报已处理数", "9" in text2, text2[:80])
    check("total=0 时也没换行刷屏", "\n" not in text2, repr(text2[-20:]))

    print("\n" + "=" * 74)
    if FAIL:
        print(f"  失败 {len(FAIL)} 项: " + "; ".join(FAIL))
        return 1
    print("  全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
