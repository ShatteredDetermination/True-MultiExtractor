# -*- coding: utf-8 -*-
"""打包版（exe）自测。

没打包过就自动跳过（退出码 0），所以源码环境下跑 run_all.py 不会因为它失败。
打包之后想单独跑：

    python build_exe.py                      # 先打包
    python selftest/test_frozen.py           # 验默认的 dist/MultiExtractor/
    python selftest/test_frozen.py "C:\\MultiExtractor\\Compiled\\Ver 0.11"

也可以设环境变量 ME_COMPILED_DIR 指定打包目录。

这个测试盯的是三件只有打包才会出问题的事：

1. **引擎模块静默丢失**。me/engines/__init__.py 用 f-string 拼模块名做动态
   导入，PyInstaller 的静态分析看不见；而那里又用 try/except 把异常吞进
   LOAD_ERRORS。结果就是 exe 能正常启动、却一个引擎都没有。光看打包日志
   发现不了，必须真的跑起来数一遍。

2. **tools/ 找不到**。冻结后 __file__ 指向 _MEIPASS 临时目录，如果不改成
   以 exe 所在目录为根，就会去临时目录里找那 170MB 的外部 CLI，永远找不到。

3. **提取结果和源码版不一致**。同样的输入必须产出同样的文件。
   manifest.json / REPORT.txt 里有生成时间戳，天然会变，单独排除。
"""

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "selftest" / "fixtures" / "Game.rgssad"


def _compiled_dir() -> Path:
    """打包产物在哪。

    优先命令行第一个参数，其次环境变量 ME_COMPILED_DIR，
    都没有就用 build_exe.py 的默认输出目录。
    版本归档会把成品放到 Compiled\\Ver x.xx\\，那时候就靠参数指定。
    """
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).resolve()
    env = os.environ.get("ME_COMPILED_DIR")
    if env:
        return Path(env).resolve()
    return ROOT / "dist" / "MultiExtractor"


COMPILED = _compiled_dir()
EXE = COMPILED / "MultiExtractorCLI.exe"
TOOLS = COMPILED / "tools"

# 这两份是「报告」，含生成时间戳，本来就每次都不一样
VOLATILE = {"manifest.json", "REPORT.txt"}

# PyInstaller 的运行时目录名。刻意不用默认的 _internal ——
# 那名字对用户没意义，还容易被误以为可以删。
CONTENTS_DIR = "resources"
SETUP_INI = "setup.ini"

EXPECT_ENGINES = (
    "electron", "gamemaker", "clickteam", "enigma", "godot",
    "rpgmaker", "unity", "unreal", "generic",
)
EXPECT_TOOLS = ("utmt", "assetstudio", "evbunpack", "repak")

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


def run_exe(args, timeout=600):
    """跑打包出来的 exe。"""
    env = dict(os.environ)
    env["ME_NO_PAUSE"] = "1"          # 自测时不要停下来等按键
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [str(EXE), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=timeout,
    )


def run_src(args, timeout=600):
    """跑源码版，作为打包版的对照。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(ROOT / "main.py"), *args], cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=timeout,
    )


def manifest(root: Path) -> dict:
    """相对路径 -> SHA256。"""
    out = {}
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        out[str(f.relative_to(root))] = h.hexdigest()
    return out


def main() -> int:
    print("=" * 66)
    print("  打包版 (exe) 自测")
    print("=" * 66)

    if not EXE.exists():
        print(f"  未找到 {EXE}")
        print("  未打包，跳过（先执行 python build_exe.py）")
        return 0

    # ---- 1. 目录结构 ----
    check(EXE.exists(), f"exe 存在：{EXE.name}")
    check((EXE.parent / CONTENTS_DIR).is_dir(),
          f"{CONTENTS_DIR}/ 在 exe 旁边")
    check(not (EXE.parent / "_internal").exists(),
          "没有留下 _internal/（运行时目录已改名）")
    # V0.18E 起发布包默认 Slim：没有 tools/ 才是对的。装了工具的包（--full）
    # 也仍然要能通过，所以这里先判断形态，再分别断言。
    slim = not (EXE.parent / "tools").exists()
    if slim:
        check(True, "Slim 包：exe 旁边没有 tools/（外部工具靠 tools.ini 现补）")
    else:
        check((EXE.parent / "tools").is_dir(), "tools/ 在 exe 旁边")
    check((EXE.parent / SETUP_INI).exists(), f"{SETUP_INI} 在 exe 旁边")

    # 发布包里不该有任何 Python 源码/字节码 —— 代码应该全在 exe 内部的 PYZ。
    # 曾经把 tools/setup_tools.py 原样搬进过发布包，这类疏漏不会有任何
    # 功能异常，只会静悄悄漏源码，所以单独盯一条。
    py_files = sorted(
        f.relative_to(EXE.parent).as_posix()
        for f in EXE.parent.rglob("*")
        if f.is_file() and f.suffix.lower() in {".py", ".pyc", ".pyo"}
    )
    check(not py_files,
          f"发布包内无 .py/.pyc（发现 {len(py_files)} 个）"
          + (f"：{py_files[:5]}" if py_files else ""))
    check(not (TOOLS / "setup_tools.py").exists(),
          "维护者脚本 setup_tools.py 没被带进来")

    if not slim:
        for key, rel in (("utmt", "utmt/UndertaleModCli.exe"),
                         ("assetstudio", "assetstudio/AssetStudio.CLI.exe"),
                         ("evbunpack", "evbunpack/evbunpack.exe"),
                         ("repak", "repak/repak.exe")):
            check((TOOLS / rel).exists(), f"外部工具 {key} 就位（{rel}）")

    # ---- 2. 引擎一个都不能少（动态导入最容易被静默漏掉）----
    res = run_exe(["--engines"])
    out = (res.stdout or "") + (res.stderr or "")
    check(res.returncode == 0, f"--engines 退出码 {res.returncode}")
    missing = [k for k in EXPECT_ENGINES if k not in out]
    check(not missing, f"9 个引擎全部加载"
                       + (f"，缺：{', '.join(missing)}" if missing else ""))
    if missing:
        print("        ！引擎被漏掉了，检查 build_exe.py 的 "
              "--collect-submodules 是否生效")
        print("        " + out.replace("\n", "\n        ")[:2000])

    # ---- 3. 外部工具定位 + 运行环境 ----
    res = run_exe(["--tools"])
    out = (res.stdout or "") + (res.stderr or "")
    if slim:
        # Slim 包里「缺失」是预期结果：退出码 4，但清单必须完整、
        # 必须说清清单从哪来、去哪儿下全量包。
        check(res.returncode in (0, 4), f"--tools 退出码 {res.returncode}（0/4 都算对）")
        check("工具清单来源" in out, "--tools 说明了清单来源")
        check("全量包" in out, "--tools 给出了全量包地址")
        for k in EXPECT_TOOLS:
            check(k in out, f"--tools 列出了 {k}")
    else:
        check(res.returncode == 0, f"--tools 退出码 {res.returncode}")
        check("缺失" not in out, "没有工具报告缺失")
        for k in EXPECT_TOOLS:
            check(k in out, f"--tools 列出了 {k}")
    check("打包版" in out, "运行环境显示为打包版（sys.frozen 生效）")
    # numpy 是刻意排除的（实测对真实样本耗时无影响，省约 40MB）
    check("未启用" in out, "numpy 已按预期排除")

    # ---- 3b. setup.ini 定位 ----
    res = run_exe(["--setup"])
    out = (res.stdout or "") + (res.stderr or "")
    check(res.returncode == 0, f"--setup 退出码 {res.returncode}")
    check(str(EXE.parent) in out, "--setup 读到的是 exe 旁边那份 setup.ini")
    check("Extract Resources" in out, "默认保存路径为 Extract Resources")

    # ---- 4. 提取结果和源码版逐字节一致 ----
    if not FIXTURE.exists():
        print(f"  [SKIP] 找不到夹具 {FIXTURE}，跳过结果比对")
    else:
        tmp = Path(tempfile.mkdtemp(prefix="me_frozen_"))
        try:
            out_exe = tmp / "exe"
            out_src = tmp / "src"
            args = [str(FIXTURE), "-e", "rpgmaker", "-y"]
            r1 = run_exe([*args, "-o", str(out_exe)])
            r2 = run_src([*args, "-o", str(out_src)])
            check(r1.returncode == 0, f"exe 提取退出码 {r1.returncode}")
            check(r2.returncode == 0, f"源码提取退出码 {r2.returncode}")

            if out_exe.exists() and out_src.exists():
                m_exe = manifest(out_exe)
                m_src = manifest(out_src)
                check(len(m_exe) > 0, f"exe 提取到 {len(m_exe)} 个文件")
                check(set(m_exe) == set(m_src),
                      "两边文件清单一致"
                      + ("" if set(m_exe) == set(m_src) else
                         f"（仅exe: {sorted(set(m_exe)-set(m_src))[:5]}；"
                         f"仅源码: {sorted(set(m_src)-set(m_exe))[:5]}）"))
                diff = [k for k in set(m_exe) & set(m_src)
                        if m_exe[k] != m_src[k] and k not in VOLATILE]
                check(not diff,
                      f"提取内容逐字节一致（已排除时间戳文件）"
                      + (f"，差异：{diff[:5]}" if diff else ""))
                # 顺便确认被排除的确实只有那两个
                vol_diff = [k for k in set(m_exe) & set(m_src)
                            if m_exe[k] != m_src[k]]
                check(set(vol_diff) <= VOLATILE,
                      f"内容有差异的只有 {sorted(vol_diff)}")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- 5. setup.ini 里的保存路径真的生效 ----
    # 不带 -o 时，结果应该自动落到 exe 旁边的 Extract Resources/ 下。
    # 这一条验证的正是 setup.ini 存在的意义，所以哪怕会往发布目录里
    # 写东西也要测；写完马上清掉。
    if FIXTURE.exists():
        import shutil
        saved = EXE.parent / "Extract Resources"
        work = EXE.parent / "work"
        shutil.rmtree(saved, ignore_errors=True)
        try:
            r = run_exe([str(FIXTURE), "-e", "rpgmaker", "-y"])
            check(r.returncode == 0,
                  f"不带 -o 提取退出码 {r.returncode}")
            games = sorted(p.name for p in saved.glob("*")) if saved.is_dir() else []
            check(bool(games),
                  f"资源自动存到 {saved}（发现 {games}）")
            got = sum(1 for _ in saved.rglob("*") if _.is_file())
            check(got > 0, f"该目录里确实有 {got} 个文件")
        finally:
            shutil.rmtree(saved, ignore_errors=True)
            shutil.rmtree(work, ignore_errors=True)

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
