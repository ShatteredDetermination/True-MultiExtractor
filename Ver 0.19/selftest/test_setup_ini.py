# -*- coding: utf-8 -*-
"""setup.ini 的读取测试。

这个测试存在的理由很具体：第一版实现用
`configparser.read(p, encoding="utf-8")` 直接读，结果**带 BOM 的文件读不了**
（configparser 抛 MissingSectionHeaderError），而异常又被吞掉退回默认值 ——
用户把保存路径改了，程序当没看见，还一句提示都没有。

BOM 不是刁钻情况：Windows 记事本、以及不少编辑器「另存为 UTF-8」默认就带
BOM；中文 Windows 上还可能存成 ANSI(GBK)。所以这里把几种编码都测一遍。

测试手法：把 setup_ini.ROOT 指到临时目录（模块内的路径函数是在调用时
读这个全局量的，所以能这样替换），跑完再还原。
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me import setup_ini  # noqa: E402

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


def write_ini(path: Path, text: str, encoding: str) -> None:
    path.write_bytes(text.encode(encoding))


def main() -> int:
    print("=" * 66)
    print("  setup.ini 读取测试")
    print("=" * 66)

    tmp = Path(tempfile.mkdtemp(prefix="me_setup_"))
    saved_root = setup_ini.ROOT
    setup_ini.ROOT = tmp
    try:
        ini = tmp / setup_ini.SETUP_NAME

        # ---- 1. 文件不存在时自动生成 ----
        check(not ini.exists(), "初始状态下没有 setup.ini")
        cfg = setup_ini.load()
        check(ini.exists(), "load() 会自动生成 setup.ini")
        check(cfg[setup_ini.KEY_PATH] == setup_ini.DEFAULT_OUTPUT,
              f"默认值 = {setup_ini.DEFAULT_OUTPUT!r}")
        check(not cfg["error"], "自动生成的文件读起来没有报错")
        check(";" in ini.read_text(encoding="utf-8"),
              "生成的文件带注释（用户能照着改）")

        # ---- 2. 各种编码都要能读 ----
        # 这是本测试的重点：UTF-8 带 BOM 是最容易踩的
        body = ("[Output]\n"
                "; 注释里带点中文，确保编码真的被正确解出来\n"
                "path = 我的 输出目录\n")
        for enc, label in (("utf-8", "UTF-8（无 BOM）"),
                           ("utf-8-sig", "UTF-8 带 BOM"),
                           ("gbk", "ANSI/GBK"),
                           ("utf-16", "UTF-16")):
            write_ini(ini, body, enc)
            cfg = setup_ini.load()
            check(cfg[setup_ini.KEY_PATH] == "我的 输出目录",
                  f"{label} 能正确读出路径")
            check(not cfg["error"], f"{label} 没有报错")

        # ---- 3. 相对路径按程序根目录解析 ----
        write_ini(ini, "[Output]\npath = Extract Resources\n", "utf-8")
        check(setup_ini.output_root() == tmp / "Extract Resources",
              "相对路径挂到程序根目录下")

        # ---- 4. 绝对路径原样使用 ----
        write_ini(ini, "[Output]\npath = D:\\拆包输出\n", "utf-8")
        check(setup_ini.output_root() == Path("D:/拆包输出"),
              "绝对路径原样使用")

        # ---- 5. 带引号的路径要去掉引号 ----
        write_ini(ini, '[Output]\npath = "D:\\带 空格"\n', "utf-8")
        check(setup_ini.output_root() == Path("D:/带 空格"),
              "路径两端的引号会被去掉")

        # ---- 6. 路径里的 % 不能被当成插值 ----
        write_ini(ini, "[Output]\npath = D:\\100%\\out\n", "utf-8")
        cfg = setup_ini.load()
        check(cfg[setup_ini.KEY_PATH] == "D:\\100%\\out",
              "% 不会被 configparser 当插值吃掉")
        check(not cfg["error"], "含 % 的路径不报错")

        # ---- 7. 坏文件要报错，而不是默默用默认值 ----
        ini.write_bytes(b"\x00\x01\x02 this is not an ini at all\n")
        cfg = setup_ini.load()
        check(cfg[setup_ini.KEY_PATH] == setup_ini.DEFAULT_OUTPUT,
              "坏文件退回默认保存路径")
        check(bool(cfg["error"]), "坏文件会给出 error 说明（不再静默）")

        # 缺 [Output] 段
        write_ini(ini, "[SomethingElse]\nx = 1\n", "utf-8")
        cfg = setup_ini.load()
        check(bool(cfg["error"]), "缺少 [Output] 段会报错")

        # path 是空的
        write_ini(ini, "[Output]\npath =\n", "utf-8")
        cfg = setup_ini.load()
        check(cfg[setup_ini.KEY_PATH] == setup_ini.DEFAULT_OUTPUT,
              "空 path 退回默认值")
        check(bool(cfg["error"]), "空 path 会报错")

        # ---- 8. 打包脚本用的 render() 能被自己读回来 ----
        ini.write_text(setup_ini.render(), encoding="utf-8")
        cfg = setup_ini.load()
        check(cfg[setup_ini.KEY_PATH] == setup_ini.DEFAULT_OUTPUT
              and not cfg["error"],
              "render() 生成的默认配置能被 load() 正常读回")
    finally:
        setup_ini.ROOT = saved_root
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
