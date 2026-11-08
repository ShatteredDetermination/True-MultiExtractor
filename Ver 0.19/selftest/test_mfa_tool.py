# -*- coding: utf-8 -*-
"""第三方 MFA 导出通道（兜底）自测：V0.19 起。

全部离线：用一段临时脚本冒充外部工具，靠它不同的输入前缀走不同分支：

    正常      → 写出 ``MFU2`` 开头的文件，必须判成功
    ``FAIL``  → 退出码 3 且 stderr 有输出，必须判失败并带上退出码
    ``JUNK``  → 退出码 0 但写出非 MFA，必须判失败（不能只看退出码）
    ``NOOUT`` → 退出码 0 但什么也不写，必须判失败
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me import config  # noqa: E402
from me.formats.ctf25 import mfa_tool  # noqa: E402

_EXIT_TOOL = config.EXIT_TOOL


def _run_cli(argv, env, timeout=180):
    """跑一遍真正的命令行，验 CLI 层的接线。"""
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "main.py")] + argv,
        capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=timeout)

PASS = 0
FAIL = 0
FAILURES = []

FAKE = '''
import pathlib
import sys
src, out = sys.argv[1], sys.argv[2]
data = pathlib.Path(src).read_bytes()
if data.startswith(b"FAIL"):
    sys.stderr.write("boom from fake tool\\n")
    sys.exit(3)
if data.startswith(b"JUNK"):
    pathlib.Path(out).write_bytes(b"NOPE" + data[4:])
    sys.exit(0)
if data.startswith(b"NOOUT"):
    sys.exit(0)
pathlib.Path(out).write_bytes(b"MFU2" + data[4:])
sys.stdout.write("wrote %s\\n" % out)
'''


def check(ok, what, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [ok] {what}")
    else:
        FAIL += 1
        FAILURES.append(what)
        print(f"  [err] {what}  {detail}")


def section(t):
    print("\n== " + t + " ==")


def main() -> int:
    print("=" * 66)
    print("  第三方 MFA 导出通道自测")
    print("=" * 66)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        script = tmp / "fake_tool.py"
        script.write_text(FAKE, encoding="utf-8")
        args = f'"{script}" {{in}} {{out}}'
        exe = sys.executable

        section("1. 找工具")
        info = mfa_tool.find_tool(exe, {"ME_MFA_TOOL": ""})
        check(info is not None and info.source == "tool", "显式路径可用",
              repr(info))
        info = mfa_tool.find_tool("", {"ME_MFA_TOOL": exe})
        check(info is not None and info.source == "env", "ME_MFA_TOOL 可用")
        info = mfa_tool.find_tool("", {"ME_MFA_TOOL": str(tmp / "nope.exe")})
        check(info is None, "环境变量指向不存在的路径时返回 None")
        check(mfa_tool.find_tool("", {}) is None
              or mfa_tool.find_tool("", {}).source == "path",
              "没给路径时只查 PATH")

        section("2. 参数模板")
        tool = mfa_tool.ToolInfo(str(exe), "tool")
        cmd = mfa_tool.build_command(tool, tmp / "a.mfa", tmp / "b.mfa", "")
        check(cmd[0] == str(exe) and len(cmd) == 3, "默认模板是「输入 输出」",
              repr(cmd))
        try:
            mfa_tool.build_command(tool, "a", "b", "{in}")
            check(False, "模板缺 {out} 要报错")
        except ValueError:
            check(True, "模板缺 {out} 要报错")
        try:
            mfa_tool.build_command(tool, "a", "b", "{out}")
            check(False, "模板缺 {in} 要报错")
        except ValueError:
            check(True, "模板缺 {in} 要报错")

        section("3. 成功路径")
        src = tmp / "game.dat"
        src.write_bytes(b"MFU2payload")
        out = tmp / "out" / "game.mfa"
        res = mfa_tool.convert(src, out, tool=exe, args=args)
        check(res.ok, "退出码 0 + MFU2 开头判成功", res.reason)
        check(res.out_size == len(b"MFU2payload"), "产物大小对得上",
              f"{res.out_size}")
        check(res.exit_code == 0 and "wrote" in res.stdout, "拿到退出码与 stdout")
        check("{in}" not in " ".join(res.command)
              and "{out}" not in " ".join(res.command), "占位符都被替换")
        check(Path(res.command[-1]).parent == out.parent.resolve()
              and out.parent.is_dir(), "输出父目录已建好并被替换")

        section("4. 失败路径")
        bad = tmp / "fail.dat"
        bad.write_bytes(b"FAILxxxx")
        res = mfa_tool.convert(bad, tmp / "f.mfa", tool=exe, args=args)
        check(not res.ok and res.exit_code == 3, "非 0 退出码要判失败",
              f"{res.exit_code} / {res.reason}")
        check("3" in res.reason and "boom" in res.stderr, "原因里带退出码，stderr 有内容")

        junk = tmp / "junk.dat"
        junk.write_bytes(b"JUNKxxxx")
        res = mfa_tool.convert(junk, tmp / "j.mfa", tool=exe, args=args)
        check(not res.ok and "不是 MFA" in res.reason,
              "退出码 0 但产物不是 MFA 也要判失败", res.reason)

        noout = tmp / "noout.dat"
        noout.write_bytes(b"NOOUTxxx")
        stale = tmp / "s.mfa"
        stale.write_bytes(b"MFU2old")
        res = mfa_tool.convert(noout, stale, tool=exe, args=args)
        check(not res.ok and "没生成输出文件" in res.reason,
              "退出码 0 但没产物要判失败", res.reason)
        check(not stale.exists(), "先清旧产物，避免把上一次的当成功")

        section("5. 没工具 / 没输入")
        res = mfa_tool.convert(src, tmp / "x.mfa", tool="",
                              env={"ME_MFA_TOOL": ""})
        check(not res.ok and "ME_MFA_TOOL" in res.reason,
              "找不到工具时说清楚（不假装能导）", res.reason)
        res = mfa_tool.convert(tmp / "nope.dat", tmp / "y.mfa", tool=exe,
                              args=args)
        check(not res.ok and "输入文件不存在" in res.reason, "输入缺失要报错",
              res.reason)

        section("6. CLI 层（--mfa）")
        game = tmp / "game.exe"
        game.write_bytes(b"PAMU fake")
        cli_out = tmp / "cli" / "game.mfa"
        env = dict(os.environ)
        env["ME_NO_NET"] = "1"
        env["ME_MFA_TOOL_ARGS"] = args
        env.pop("ME_MFA_TOOL", None)
        cp = _run_cli([str(game), "--mfa", str(cli_out),
                       "--mfa-tool", exe], env)
        check(cp.returncode == 0, "CLI 成功导出退出码 0",
              f"{cp.returncode} / {cp.stderr[-200:]}")
        check(cli_out.is_file() and cli_out.read_bytes().startswith(b"MFU2"),
              "CLI 产物是 MFU2 开头")
        check("已导出 mfa" in cp.stdout, "CLI 打印了结果")
        env2 = dict(env)
        env2.pop("ME_MFA_TOOL_ARGS", None)
        env2["ME_MFA_TOOL"] = ""
        env2["PATH"] = str(tmp / "empty_path")
        cp2 = _run_cli([str(game), "--mfa", str(tmp / "n.mfa")], env2)
        check(cp2.returncode == _EXIT_TOOL, "没工具时退出码是 4（缺少外部 CLI）",
              f"{cp2.returncode}")
        check("ME_MFA_TOOL" in cp2.stdout, "没工具时打印了怎么配")

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
