# -*- coding: utf-8 -*-
"""自研 / 第三方两条通道的判定与接线（V0.17）。

三部分：

  1. 纯逻辑：me/channel.decide 的整张真值表 —— 不碰文件、不起进程。
     「实验性功能」和「自研开关」必须同时成立才换自研，CTF 例外只有自研。
  2. 接线：Context.use_self 只认 channel 字段，引擎模块不自己判断开关。
  3. 端到端：真的跑 main.py，看事件流里的 channel 事件和退出码。

用法：
    python selftest/test_channel.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me import channel                     # noqa: E402
from me.engines import MODULE_ORDER        # noqa: E402
from me.engines.base import Context        # noqa: E402

MAIN = ROOT / "main.py"
TIMEOUT = 120

PASS = 0
FAIL = 0
FAILURES = []


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {msg}")
    else:
        FAIL += 1
        FAILURES.append(msg)
        print(f"  [FAIL] {msg}")
    return bool(cond)


def section(title):
    print("\n== " + title + " ==")


def run_cli(args, timeout=TIMEOUT):
    proc = subprocess.run(
        [sys.executable, str(MAIN)] + [str(a) for a in args],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    out = proc.stdout.decode("utf-8", "replace")
    seq = []
    for line in out.splitlines():
        if line.strip():
            try:
                seq.append(json.loads(line))
            except ValueError:
                pass
    return proc.returncode, seq


def first_of(events, t):
    for ev in events:
        if ev.get("t") == t:
            return ev
    return None


# ------------------------------------------------------------------ 1
def test_table():
    section("能力表：每个引擎都有自研实现这一栏")

    missing = [k for k in MODULE_ORDER if k not in channel.CAPS]
    check(not missing, f"所有引擎都在能力表里（缺 {missing}）")

    for key in MODULE_ORDER:
        cap = channel.CAPS[key]
        if cap.third is None:
            check(cap.self_ready, f"{key}: 没有第三方，必须自研可用")
        else:
            check(bool(cap.third_name), f"{key}: 有第三方就得写清工具名")

    # V0.17 的剩余工作量：这两个还没有自研实现。做完就从这个集合里删。
    # （unreal 已经接上自研，但只覆盖 pak v0–v9；v10/v11 由引擎逐 pak 退回 repak）
    todo = {k for k in MODULE_ORDER if not channel.CAPS[k].self_ready}
    check(todo == set(), f"没有引擎缺自研实现（缺 {sorted(todo)}）")


# ------------------------------------------------------------------ 2
def test_decide():
    section("判定真值表")

    # 有第三方、也有自研：gamemaker
    d = channel.decide("gamemaker")
    check(d.channel == channel.THIRD, "gamemaker 默认走第三方")

    d = channel.decide("gamemaker")          # 什么都不给 = 默认
    check(d.note == "", "默认情况不带多余说明")

    d = channel.decide("gamemaker", experimental=True)
    check(d.channel == channel.THIRD, "只开实验性功能仍走第三方")

    d = channel.decide("gamemaker", want_self=True)
    check(d.channel == channel.THIRD, "只勾自研（未开实验性）仍走第三方")
    check("实验性" in d.note, f"并说明原因（{d.note}）")

    d = channel.decide("gamemaker", experimental=True, want_self=True)
    check(d.channel == channel.SELF, "实验性 + 自研同时成立才走自研")

    # 各引擎都已接上自研；unity 只覆盖未压缩包，压缩包在引擎内部退回第三方
    d = channel.decide("unity", experimental=True, want_self=True)
    check(d.channel == channel.SELF, "unity 走自研（unityfs）")
    for key in ("enigma", "unreal"):
        d = channel.decide(key, experimental=True, want_self=True)
        check(d.channel == channel.SELF, f"{key} 走自研")

    # CTF 除外：只有自研
    for kw in ({}, {"experimental": True}, {"want_self": True},
               {"experimental": True, "want_self": True}):
        d = channel.decide("clickteam", **kw)
        check(d.channel == channel.SELF,
              f"CTF 任何开关组合都走自研（{kw or '默认'}）")
    d = channel.decide("clickteam")
    check("CTF" in d.note and "第三方" in d.note, f"说明 CTF 不接第三方（{d.note}）")

    # 只有自研、没有第三方的引擎
    for key in ("godot", "electron", "rpgmaker", "generic"):
        check(channel.decide(key).channel == channel.SELF,
              f"{key} 没有第三方，默认就是自研")

    # 第三方缺失时的自动退让
    d = channel.decide("gamemaker", third_available=False)
    check(d.channel == channel.SELF, "第三方工具缺失时改用自研")
    d = channel.decide("unity", third_available=False)
    check(d.channel == channel.SELF, "第三方缺失时 unity 也走自研")

    # 未知引擎按「只有自研」处理
    check(channel.decide("whatever").channel == channel.SELF,
          "未知引擎按只有自研处理")

    # 标签与属性
    check(channel.LABEL[channel.SELF] == "自研", "SELF 的显示名是「自研」")
    check(channel.LABEL[channel.THIRD] == "第三方", "THIRD 的显示名是「第三方」")
    d = channel.decide("gamemaker")
    check(d.is_self is False and d.label == "第三方", "Decision.is_self / label 一致")


# ------------------------------------------------------------------ 3
def test_context():
    section("Context.use_self 只认 channel 字段")

    tmp = Path(tempfile.mkdtemp(prefix="me_ch_ctx_"))
    try:
        def ctx(ch):
            return Context(target=tmp, detection=None, writer=None,
                           work_dir=tmp, channel=ch)

        check(ctx("").use_self is False, "channel 为空时按第三方")
        check(ctx(channel.THIRD).use_self is False, "channel=third 时为 False")
        check(ctx(channel.SELF).use_self is True, "channel=self 时为 True")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ 4
def test_cli_channels():
    section("--channels 自检")

    code, events = run_cli(["--channels", "--json"])
    check(code == 0, f"退出码 {code}")

    caps = [ev for ev in events if ev.get("t") == "channel_cap"]
    check(len(caps) == len(channel.CAPS),
          f"每个引擎一条 channel_cap（{len(caps)} == {len(channel.CAPS)}）")
    keys = {ev.get("engine") for ev in caps}
    check(keys == set(channel.CAPS), f"引擎键齐全（{sorted(keys)}）")
    check(all(isinstance(ev.get("self_ready"), bool) for ev in caps),
          "self_ready 都是布尔")


# ------------------------------------------------------------------ 5
def test_cli_extract():
    section("端到端：提取时的通道事件")

    tmp = Path(tempfile.mkdtemp(prefix="me_ch_cli_"))
    try:
        # 没有第三方通道的引擎（electron）：无论开关怎么给都是自研
        game = tmp / "el_game"
        (game / "resources" / "app").mkdir(parents=True)
        (game / "resources" / "app.asar").write_bytes(b"x" * 64)
        (game / "resources" / "app" / "package.json").write_bytes(b"{}")
        (game / "resources" / "app" / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        out = tmp / "out_el"
        code, events = run_cli([str(game), "-o", str(out), "--json"])
        ch = first_of(events, "channel")
        check(ch is not None, "有 channel 事件")
        if ch is not None:
            check(ch.get("channel") == channel.SELF,
                  f"electron 走自研（{ch.get('channel')}）")
            check(ch.get("label") == "自研", f"label = {ch.get('label')!r}")
        res = first_of(events, "result")
        if res is not None:
            check(res.get("channel") == channel.SELF,
                  f"result.channel = {res.get('channel')!r}")

        # GameMaker 的合成容器：默认第三方
        gm = tmp / "gm_game"
        (gm / "sub").mkdir(parents=True)
        code, events = run_cli([str(gm), "-o", str(tmp / "out_gm"), "--json",
                                "-e", "gamemaker"])
        ch = first_of(events, "channel")
        if ch is not None:
            check(ch.get("channel") == channel.THIRD,
                  f"gamemaker 默认第三方（{ch.get('channel')}）")

        # 只给 --self 不给 --experimental：仍然是第三方，并在 note 里说清楚
        code, events = run_cli([str(gm), "-o", str(tmp / "out_gm2"), "--json",
                                "-e", "gamemaker", "--self"])
        ch = first_of(events, "channel")
        if ch is not None:
            check(ch.get("channel") == channel.THIRD,
                  f"--self 单独给仍是第三方（{ch.get('channel')}）")
            check("实验性" in (ch.get("note") or ""),
                  f"note 说明缺实验性功能（{ch.get('note')!r}）")

        # 两个都给：自研
        code, events = run_cli([str(gm), "-o", str(tmp / "out_gm3"), "--json",
                                "-e", "gamemaker",
                                "--experimental", "--self"])
        ch = first_of(events, "channel")
        if ch is not None:
            check(ch.get("channel") == channel.SELF,
                  f"--experimental --self 走自研（{ch.get('channel')}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("=" * 66)
    print("  拆包通道自测（V0.17）")
    print("=" * 66)

    test_table()
    test_decide()
    test_context()
    test_cli_channels()
    test_cli_extract()

    print("\n" + "=" * 66)
    print(f"  PASS {PASS}  FAIL {FAIL}")
    print("=" * 66)
    if FAILURES:
        print("\n失败项:")
        for f in FAILURES:
            print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
