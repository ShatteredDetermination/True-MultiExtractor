# -*- coding: utf-8 -*-
"""真实游戏样本的端到端回归测试。

样本目录如果不存在会自动跳过（别人拿到这个项目时不会有这些文件）。

V0.17 起每条通道都要跑：默认第三方；GameMaker 再额外跑一遍自研
（``--experimental --self``），两边的最小文件数分别标定 —— 不同的通道
覆盖的东西不一样（UTMT 不出音频/字体，自研把图集按页切），
拿一个数字卡两条通道只会互相打架。

用法：
    python selftest/test_samples.py            # 全部类型
    python selftest/test_samples.py --quick    # 只导图片，跑得快
    python selftest/test_samples.py --no-self  # 不跑自研对照
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLE_ROOT = Path(r"C:\拆包教程素材\目标游戏")

# (显示名, 相对路径, 期望引擎键, 全量时最少文件数, 仅图片时最少文件数)
# 数字取自实测结果，留一点余量（引擎/工具版本变化时不要一上来就红）。
# 这里的阈值对应**默认通道**（第三方；没有第三方的引擎就是自研）。
SAMPLES = (
    ("PSOutertale", "PSOutertale", "electron", 3900, 2400),
    ("Undertale True Genocide", "Undertale True Genocide", "gamemaker", 1600, 1600),
    ("Undertale Last Breath", "Undertale Last Breath Scrapped BETA",
     "clickteam", 2150, 2100),
    ("dtlg (单文件 CTF)", "dtlg-v1.1.0.exe", "clickteam", 1650, 1450),
    ("cage-of-abyss (Enigma 壳)", "cage-of-abyss-full-game-v0.2.2.exe",
     "enigma", 2100, 1950),
)

# 自研通道对照：只对同时具备两条通道的引擎跑。实测（0.17）：
#   Undertale True Genocide  data.win 7.8MB bytecode 16
#       第三方 1690 个文件 / 4.3s      自研 620 个文件 / 1.2s
# 自研的 620 = 248 精灵（517 帧切片）+ 65 音频 + 文本/字体等，数量少但覆盖更全。
SELF_MIN = {"gamemaker": 600}

OUT_ROOT = Path(__file__).resolve().parent / "_out"


def once(label: str, target: Path, cats, min_files: int, tag: str,
         self_channel: bool, keep: bool) -> bool:
    """跑一遍并检查。返回是否通过。"""
    from me.runner import run

    out_dir = OUT_ROOT / (label.replace(" ", "_").replace("(", "").replace(")", "")
                          + ("_self" if self_channel else ""))
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)

    t0 = time.time()
    res = run(target, out_dir=out_dir, categories=cats, quiet=True,
              experimental=self_channel, want_self=self_channel)
    dt = time.time() - t0
    print(f"  导出: {res.written} 个文件, {dt:.1f}s  通道={res.channel}")

    count_ok = res.written >= min_files
    if not count_ok:
        print(f"  !! 文件数少于预期（期望 >= {min_files}）")
    manifest_ok = (out_dir / "manifest.json").exists() if res.written else True
    if res.written and not manifest_ok:
        print("  !! manifest.json 没有生成")

    nonempty = False
    if res.written:
        for p in out_dir.rglob("*"):
            if p.is_file() and p.stat().st_size > 0:
                nonempty = True
                break
    if res.written and not nonempty:
        print("  !! 所有导出文件都是 0 字节")

    ok = count_ok and manifest_ok and nonempty
    if ok and not keep:
        shutil.rmtree(out_dir, ignore_errors=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="只导图片，跑得快")
    ap.add_argument("--keep", action="store_true", help="保留输出目录")
    ap.add_argument("--no-self", action="store_true", help="不跑自研对照")
    args = ap.parse_args()

    if not SAMPLE_ROOT.exists():
        print(f"[跳过] 样本目录不存在: {SAMPLE_ROOT}")
        return 0

    from me.scanner import detect_engine

    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    cats = {"image"} if args.quick else None
    failures = []

    print("=" * 74)
    print(f"  真实样本回归测试  ({'仅图片' if args.quick else '全部类型'})")
    print("=" * 74)

    for label, rel, want_engine, min_full, min_images in SAMPLES:
        target = SAMPLE_ROOT / rel
        if not target.exists():
            print(f"\n[跳过] {label}: 找不到 {target}")
            continue

        min_files = min_images if args.quick else min_full
        print(f"\n---- {label} " + "-" * max(0, 50 - len(label)))

        det = detect_engine(target)
        engine_ok = det.engine == want_engine
        print(f"  识别: {det.engine} ({det.describe()})"
              f"  {'OK' if engine_ok else '!! 期望 ' + want_engine}")

        ok = once(label, target, cats, min_files, "默认通道", False, args.keep)

        # 自研对照：同一份样本换通道再跑一遍，确认两条路都还活着
        if not args.no_self and want_engine in SELF_MIN:
            need = SELF_MIN[want_engine]
            if args.quick:
                need = min(need, 200)      # 只要图片时按图片量放宽
            print("  自研通道对照:")
            ok = once(label, target, cats, need, "自研", True, args.keep) and ok

        if engine_ok and ok:
            print("   -> 通过")
        else:
            failures.append(label)
            print("   -> 失败")

    print("\n" + "=" * 74)
    if failures:
        print(f"  失败: {', '.join(failures)}")
        return 1
    print("  全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
