# -*- coding: utf-8 -*-
"""命令行 / 事件流契约测试。

GUI 壳只做两件事：拼命令行、逐行读事件流。所以能把它弄坏的改动
都在这条链上：多打一行到 stdout、退出码变了、end 不出现、
--detect 顺手发了 result、取消文件没人看。这里把这些逐条跑一遍，
全部通过子进程调 main.py 来验证 —— 只有真的启动进程，才能看见
stdout/stderr 的分工和退出码，直接 import 是看不见的。

每一条用例都在临时目录里造数据，跑完清理，不碰项目里的文件。
契约正文见 GUI-API.md，退出码表见 me/config.py。
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MAIN = ROOT / "main.py"

# 子进程里跑的都是小目录，正常几秒内结束；给得宽松一点，
# 超时说明进程卡住了（比如在等按键），那就是失败。
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


def section(title: str) -> None:
    print("\n== " + title + " ==")


def run_cli(args, timeout=TIMEOUT):
    """跑一次 main.py，返回 (退出码, 事件列表, stdout 原文, stderr 原文)。"""
    proc = subprocess.run(
        [sys.executable, str(MAIN)] + [str(a) for a in args],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    seq = []
    bad = []
    for line in out.splitlines():
        if not line.strip():
            continue
        try:
            seq.append(json.loads(line))
        except ValueError:
            bad.append(line)
    return proc.returncode, seq, out, err


def first_of(events, t):
    for ev in events:
        if ev.get("t") == t:
            return ev
    return None


def count_of(events, t):
    return sum(1 for ev in events if ev.get("t") == t)


def make_dir(tmp: Path, name: str, files: dict) -> Path:
    """按 {相对路径: 内容} 造一个目录。路径以 / 结尾表示子目录。"""
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = d / rel
        if rel.endswith("/"):
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content if isinstance(content, bytes) else b"x")
    return d


# ------------------------------------------------------------------ 1/2
def test_stream_and_streams(tmp: Path):
    section("事件流与流的分工")

    code, events, out, err = run_cli(["--json", "--version"])

    check(bool(events), f"stdout 至少有一行事件（{len(out.splitlines())} 行）")
    check(not any(ln.strip() for ln in out.splitlines()
                  if ln.strip() and not ln.lstrip().startswith("{")),
          "stdout 每一行都是 JSON 对象（没有横幅 / [ok] 混进来）")

    if events:
        check(events[0].get("t") == "start",
              f"第一条事件是 start（实际 {events[0].get('t')!r}）")
        check(events[-1].get("t") == "end",
              f"最后一条事件是 end（实际 {events[-1].get('t')!r}）")
        # v 是 schema 版本，只在 start 上（见 GUI-API.md 第 5 节的 start 示例）
        check(events[0].get("v") == 1,
              f"start.v = {events[0].get('v')!r} == 1")
        seqs = [ev.get("seq") for ev in events]
        check(all(isinstance(s, int) for s in seqs),
              f"seq 全是整数（{seqs}）")
        check(all(b > a for a, b in zip(seqs, seqs[1:])),
              f"seq 严格递增（{seqs}）")
    else:
        check(False, "有事件可查（start/end/seq/v）")

    # 第 2 节：人读文本整体改道 stderr
    check("====" in err, "stderr 里有横幅（==== 分隔线）")
    check("[ok]" in err, "stderr 里有 [ok] 之类的人读前缀")
    check(not any(ln.lstrip().startswith("{") for ln in err.splitlines()),
          "stderr 里没有事件流（两边没有串线）")
    check(code == 0, f"--json --version 退出码 {code}")


# ------------------------------------------------------------------ 3/4
def test_exit_codes(tmp: Path):
    section("退出码与 end.exit")

    cases = []

    # 自描述命令：都是 0
    for flag in ("--version", "--categories", "--engines", "--setup"):
        cases.append(([flag], 0, f"{flag}"))
    # --tools 的退出码取决于本机装没装外部工具：Slim 包里没有 → 4 也算正常
    cases.append((["--tools"], None, "--tools"))

    # 未知参数：参数错误，但事件流照样要有头有尾
    cases.append((["--bogus"], 2, "--bogus（未知参数）"))

    # 目标不存在
    missing = tmp / "no_such_dir_me"
    cases.append(([str(missing), "-o", str(tmp / "out_missing"), "--json"],
                  1, "目标路径不存在"))

    # 跑完了但一个文件都没有：只有子目录、没有任何资源文件
    hollow = make_dir(tmp, "hollow", {"sub/": None, "sub/deeper/": None})
    cases.append(([str(hollow), "-o", str(tmp / "out_hollow"), "--json"],
                  3, "空目录（无任何资源文件）"))

    for args, want, label in cases:
        argv = list(args)
        if "--json" not in argv:
            argv.insert(0, "--json")
        code, events, _out, _err = run_cli(argv)
        if want is None:
            check(code in (0, 4), f"{label}: 退出码 {code}（缺工具时允许 4）")
        else:
            check(code == want, f"{label}: 进程退出码 {code} == {want}")
        end = first_of(events, "end")
        check(end is not None, f"{label}: 事件流里有 end")
        if end is not None:
            check(end.get("exit") == code,
                  f"{label}: end.exit {end.get('exit')} == 进程退出码 {code}")
        if want == 2:
            check(first_of(events, "start") is not None,
                  "--bogus: 事件流里仍有 start")
            check(first_of(events, "detect") is None
                  and first_of(events, "result") is None,
                  "--bogus: 没有 detect / result")


# ------------------------------------------------------------------ 5
def test_detect_only(tmp: Path):
    section("--detect 只识别不提取")

    game = make_dir(tmp, "fake_electron", {
        "resources/app.asar": b"x" * 32,
        "resources/app/package.json": b"{}",
    })
    out = tmp / "out_detect"

    code, events, _o, _e = run_cli([str(game), "--json", "--detect",
                                    "-o", str(out)])

    check(code == 0, f"--detect 退出码 {code}")
    check(count_of(events, "detect") == 1,
          f"恰好一条 detect（实际 {count_of(events, 'detect')}）")
    check(count_of(events, "result") == 0,
          f"没有 result（实际 {count_of(events, 'result')}）")
    det = first_of(events, "detect")
    if det is not None:
        check(isinstance(det.get("known"), bool),
              f"detect.known 是布尔（{det.get('known')!r}）")
    end = first_of(events, "end")
    check(end is not None and end.get("exit") == code,
          "end.exit 与进程退出码一致")
    check(not out.exists(), f"没往 -o 目录写东西（{out} 不存在）")


# ------------------------------------------------------------------ 6
def test_result_event(tmp: Path):
    section("result 事件与产物")

    PNG = b"\x89PNG\r\n\x1a\n"                       # 8 字节 PNG 魔数
    OGG = b"OggS" + b"\x00" * 4                       # OggS 魔数

    src = make_dir(tmp, "three_files", {
        "a.png": PNG,
        "b.txt": b"hello",
        "c.ogg": OGG,
    })
    out = tmp / "out_three"

    code, events, _o, _e = run_cli([str(src), "-o", str(out), "--json"])

    check(code == 0, f"退出码 {code}")
    res = first_of(events, "result")
    check(res is not None, "有一条 result")
    if res is None:
        return
    check(res.get("ok") is True, f"result.ok = {res.get('ok')!r}")
    check(res.get("written") == 3, f"result.written = {res.get('written')!r} == 3")

    cats = res.get("categories") or {}
    check(cats.get("image") == 1, f"categories.image = {cats.get('image')!r} == 1")
    check(cats.get("text") == 1, f"categories.text = {cats.get('text')!r} == 1")
    check(cats.get("audio") == 1, f"categories.audio = {cats.get('audio')!r} == 1")

    check(res.get("out_dir") == str(out),
          f"out_dir = {res.get('out_dir')!r} 就是传进去的 -o 目录")
    check((out / "manifest.json").is_file(), "输出目录里有 manifest.json")
    check((out / "REPORT.txt").is_file(), "输出目录里有 REPORT.txt")

    check(count_of(events, "stage") >= 1,
          f"有 stage 事件（{count_of(events, 'stage')} 条）")
    check(count_of(events, "progress") >= 1,
          f"有 progress 事件（{count_of(events, 'progress')} 条）")


# ------------------------------------------------------------------ 7
def test_cancel(tmp: Path):
    section("取消协议")

    src = make_dir(tmp, "cancel_target", {"sub/": None})
    out = tmp / "out_cancel"
    cancel = tmp / "cancel.flag"
    cancel.write_bytes(b"")          # 运行前就摆好，识别后、提取前会被看到

    code, events, _o, _e = run_cli([str(src), "-o", str(out), "--json",
                                    "--cancel-file", str(cancel)])

    check(code == 5, f"进程退出码 {code} == 5")
    end = first_of(events, "end")
    check(end is not None, "事件流里有 end")
    if end is not None:
        check(end.get("exit") == 5, f"end.exit = {end.get('exit')!r} == 5")
    notes = [ev.get("text", "") for ev in events if ev.get("t") == "note"]
    check(any("取消" in t for t in notes),
          f"有说明取消的 note（{notes}）")


# ------------------------------------------------------------------ 8
def test_json_no_pause(tmp: Path):
    section("--json 不自停")

    # --json 会自动设 ME_NO_PAUSE=1，所以进程绝不该停在「按任意键」上。
    # 这里用 timeout 间接确认：超时即失败。
    try:
        code, events, _o, _e = run_cli(["--json", "--version"], timeout=20)
        check(code == 0, f"20 秒内正常结束，退出码 {code}")
        check(first_of(events, "end") is not None, "有 end 事件")
    except subprocess.TimeoutExpired:
        check(False, "--json --version 20 秒内没结束（进程自停了）")


def main() -> int:
    print("=" * 66)
    print("  命令行 / 事件流契约测试")
    print("=" * 66)

    tmp = Path(tempfile.mkdtemp(prefix="me_cli_"))
    try:
        test_stream_and_streams(tmp)
        test_exit_codes(tmp)
        test_detect_only(tmp)
        test_result_event(tmp)
        test_cancel(tmp)
        test_json_no_pause(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

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
