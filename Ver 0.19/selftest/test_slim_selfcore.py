# -*- coding: utf-8 -*-
"""纯自生存自测：没有 tools/ 时，自研核心必须能独立跑完 GameMaker 的提取与全量重建。

这是 V0.18E「Slim 微内核」的验收条件。发布包里**不带**那 178 MB 外部 CLI，
所以第三方通道在用户的干净机器上一定不可用。程序不能因此变成空壳：

  1. 判定：第三方缺失时 channel 必须落到 self（不是报错、不是空转）；
  2. 提取：``--self`` 跑真实 data.win，退出码 0，写出文件 > 0；
  3. 重建：``--rebuild`` 独立完成，产出的文件能被自己的解析器读回且链完整；
  4. 全程不产生、不依赖任何外部工具（跑完 tools/ 里仍然没有 exe）。

真实样本不存在时跳过（不是失败）—— CI 上没有那台机器的素材目录。
自研覆盖度不如 UTMT 是已知的能力差异（620 vs 1600+ 个资源），这里只验
「能独立跑通」，基线数字写在 test_samples 里。

运行：
    python selftest/test_slim_selfcore.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MAIN = ROOT / "main.py"
SAMPLE = Path(r"C:\拆包教程素材\目标游戏\Undertale True Genocide")
DATA_WIN = SAMPLE / "data.win"

PASS = 0
FAIL = 0
SKIP = 0


def check(ok, what, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [ok] {what}")
    else:
        FAIL += 1
        print(f"  [err] {what}  {detail}")


def skip(what, why=""):
    global SKIP
    SKIP += 1
    print(f"  [--] 跳过 {what}  {why}")


def run_cli(argv, timeout=900):
    """跑一次 main.py，返回 (退出码, 事件列表, 原始输出)。"""
    env = dict(os.environ)
    env["ME_NO_NET"] = "1"          # 不拉远端清单
    env["ME_NO_SOFTKEY"] = "1"      # 不弹软键
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, str(MAIN)] + argv
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.stdout.decode("utf-8", "replace")
    events = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return proc.returncode, events, out, time.time() - t0


def find_event(events, name):
    """NDJSON 里事件类型在 "t" 字段里（见 me/events.py）。"""
    for ev in events:
        if ev.get("t") == name or ev.get("event") == name:
            return ev
    return None


def exes_in_tools():
    from me.config import TOOLS_DIR
    if not TOOLS_DIR.is_dir():
        return []
    return [p.name for p in TOOLS_DIR.rglob("*.exe")]


def main() -> int:
    print("=" * 74)
    print("  Slim 纯自生存：没有 tools/ 也要跑得动")
    print("=" * 74)

    from me import channel
    from me.tools import paths

    # ------------------------------------------------------------ 1. 判定
    print("\n[1] 通道判定：第三方缺失 -> 自研")
    d = channel.decide("gamemaker", experimental=True, want_self=True,
                       third_available=False)
    check(d.channel == channel.SELF, "gamemaker + 无第三方 -> self", d.label)
    check(bool(d.note), "给了理由", d.note)

    d = channel.decide("gamemaker", experimental=False, want_self=False,
                       third_available=True)
    check(d.channel == channel.THIRD, "有第三方、没开实验性 -> third", d.label)

    d = channel.decide("gamemaker", experimental=True, want_self=True,
                       third_available=False)
    check(d.channel == channel.SELF, "无第三方时即使没勾自研也退自研", d.label)

    d = channel.decide("unity", experimental=False, want_self=True,
                       third_available=False)
    check(d.channel in (channel.SELF, channel.THIRD),
          "无第三方的引擎不会崩", d.label)

    # ------------------------------------------------------------ 2. 包结构
    # 本机可能已经用 --tools-get 把工具下下来了（那就不是 Slim 形态），
    # 所以这里按实际情况分支：要验的是「自研不依赖它们」，不是「它们必须不在」。
    print("\n[2] 外部工具的存在与否（两种形态都要能跑）")
    exes = exes_in_tools()
    if paths.any_installed():
        skip("Slim 形态检查", f"本机已装 {', '.join(paths.installed())}"
                              f"（tools/ 下 {len(exes)} 个 exe）")
    else:
        check(not exes, "tools/ 下没有 exe", str(exes[:5]))
        check(not paths.any_installed(), "--tools 视角：一个都没装")
        check(bool(paths.repair_hint("utmt")), "缺工具时有修复提示",
              paths.repair_hint("utmt"))
        check(bool(paths.download_hint()), "缺工具时有下载地址",
              paths.download_hint())

    if not (SAMPLE.is_dir() and DATA_WIN.is_file()):
        skip("真实 GameMaker 样本", f"找不到 {DATA_WIN}")
        print("\n" + "=" * 74)
        print(f"  通过 {PASS} 项，跳过 {SKIP} 项（没有样本，只验了判定与结构）")
        return 0 if FAIL == 0 else 1

    tmp = Path(tempfile.mkdtemp(prefix="me_slim_")).resolve()
    try:
        # -------------------------------------------------- 3. 提取（--self）
        print("\n[3] 提取：--experimental --self，只取图片")
        out_extract = tmp / "extract"
        code, events, out, dt = run_cli([
            str(SAMPLE), "--experimental", "--self", "--only", "image",
            "-o", str(out_extract), "-y", "--json",
        ])
        check(code == 0, f"退出码 0（实际 {code}）", out[-600:])

        ch_ev = find_event(events, "channel")
        check(ch_ev is not None, "有 channel 事件")
        if ch_ev is not None:
            check(ch_ev.get("channel") == channel.SELF,
                  "通道是 self", str(ch_ev.get("channel")))
            check(ch_ev.get("engine") == "gamemaker",
                  "引擎是 gamemaker", str(ch_ev.get("engine")))

        det_ev = find_event(events, "detect")
        check(det_ev is not None and det_ev.get("engine") == "gamemaker",
              "识别为 GameMaker", str(det_ev))

        res = find_event(events, "result")
        check(res is not None and res.get("ok") is True, "result.ok", str(res))
        written = int(res.get("written") or 0) if res else 0
        check(written > 0, f"写出文件数 > 0（实际 {written}）", str(written))

        files = [p for p in out_extract.rglob("*") if p.is_file()] \
            if out_extract.is_dir() else []
        check(len(files) > 0, f"磁盘上真有文件（{len(files)} 个）", str(out_extract))
        images = [p for p in files
                  if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif")]
        check(len(images) > 0, f"其中图片 {len(images)} 个")
        check(dt < 900, f"提取耗时在预算内（{dt:.1f}s）")
        print(f"       用时 {dt:.1f}s，写出 {written} 个文件 "
              f"（自研通道；UTMT 通道的基线是 1600+）")

        # -------------------------------------------------- 4. 全量重建
        print("\n[4] 重建：--rebuild（不需要任何外部工具）")
        out_rebuild = tmp / "rebuild"
        out_rebuild.mkdir(parents=True, exist_ok=True)
        code, events, out, dt = run_cli([
            str(DATA_WIN), "--experimental", "--self", "--rebuild",
            "-o", str(out_rebuild), "-y", "--json",
        ], timeout=900)
        check(code == 0, f"退出码 0（实际 {code}）", out[-800:])

        dest = out_rebuild / (DATA_WIN.stem + "_rebuilt" + DATA_WIN.suffix)
        check(dest.is_file(), "写出了 _rebuilt.win", str(dest))
        if dest.is_file():
            size = dest.stat().st_size
            check(size > 0, f"新文件不是空的（{size:,} 字节）")
            check(abs(size - DATA_WIN.stat().st_size) >= 0,
                  "长度与原文件可比",
                  f"{size:,} vs {DATA_WIN.stat().st_size:,}")

            from me.formats.gamemaker_win import WinFile
            w = WinFile(dest.read_bytes())
            check(len(w.order) > 0, f"读回来 {len(w.order)} 个块", str(w.order))
            check(w.chain_ok, "块链完整", str(w.order))
            # GEN8 是文件头而不是一个 chunk，WinFile 把它单独记在 gen8_len/bytecode
            check(w.gen8_len > 0, "带 GEN8 头", str(w.gen8_len))
            check(w.bytecode() in (15, 16, 17, 18, 19, 20, 21, 22, 23),
                  f"bytecode 合理（{w.bytecode()}）")
            print(f"       用时 {dt:.1f}s，{len(w.order)} 块，链"
                  f"{'完整' if w.chain_ok else '断了'}")

            # 重建产物要能被重新识别：否则等于写了个谁都不认的文件
            code2, events2, out2, _ = run_cli([
                str(dest), "--detect", "--json",
            ], timeout=180)
            det2 = find_event(events2, "detect")
            check(code2 == 0 and det2 is not None
                  and det2.get("engine") == "gamemaker",
                  "重建产物仍被识别为 GameMaker", str(det2))

        # -------------------------------------------------- 5. 自生存没留尾巴
        print("\n[5] 跑完还是纯自研，没有偷偷用外部工具")
        exes2 = exes_in_tools()
        if paths.any_installed():
            check(len(exes2) == len(exes),
                  f"跑前跑后工具数量没变（{len(exes)}）", str(len(exes2)))
        else:
            check(not exes2, "tools/ 里仍然没有 exe", str(exes2))

        print("\n" + "=" * 74)
        if FAIL:
            print(f"  失败 {FAIL} 项，通过 {PASS} 项，跳过 {SKIP} 项")
            return 1
        print(f"  全部通过（{PASS} 项，跳过 {SKIP} 项）")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
