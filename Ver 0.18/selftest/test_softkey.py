# -*- coding: utf-8 -*-
"""首次无网软键菜单（4 颗键）的自测。

场景很具体：Slim 包解压到一台新机器，没有 tools/、没网、也没有缓存。
这时候不能直接崩，也不能假装有工具 —— 必须给用户四条出路：

    1  下载全量包      2  扫描本机目录      3  手动指定目录      4  仅用自研

本测试不连网、不开浏览器、不扫真实的 C 盘（扫描根被换成一个临时目录），
但每条分支都真的走了一遍：软的（提示）和硬的（写 toolsdir.ini、改判定）都验。

运行：
    python selftest/test_softkey.py
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(ok, what, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [ok] {what}")
    else:
        FAIL += 1
        print(f"  [err] {what}  {detail}")


class FakeUi:
    """把 ui 的所有输出收进一个列表，输入按脚本给。"""

    def __init__(self, answers=()):
        self.lines = []
        self.answers = list(answers)

    # -- 输出 --
    def sep(self, *_a, **_k):
        self.lines.append("---")

    def warn(self, msg, *_a, **_k):
        self.lines.append(f"WARN {msg}")

    def detail(self, msg, *_a, **_k):
        self.lines.append(f"DETAIL {msg}")

    def info(self, msg, *_a, **_k):
        self.lines.append(f"INFO {msg}")

    def ok(self, msg, *_a, **_k):
        self.lines.append(f"OK {msg}")

    def step(self, msg, *_a, **_k):
        self.lines.append(f"STEP {msg}")

    def progress(self, done, total, label="", *_a, **_k):
        self.lines.append(f"PROGRESS {done}/{total} {label}")

    def command(self, raw):
        """斜杠指令：转发给真正的 me.ui（它会调用 main 里装的钩子）。"""
        from me import ui as _ui
        return _ui.command(raw)

    # -- 输入 --
    def ask(self, *_a, **_k):
        return self.answers.pop(0) if self.answers else ""

    def clean_input(self, raw):
        return (raw or "").strip()

    def text(self):
        return "\n".join(self.lines)


def make_tool_tree(root: Path, key: str = "utmt") -> Path:
    """在 <root>/utmt/ 下放一个假的 UndertaleModCli.exe。"""
    d = root / key
    d.mkdir(parents=True, exist_ok=True)
    (d / "UndertaleModCli.exe").write_bytes(b"MZ" + b"\x00" * 62)
    return root


def main() -> int:
    print("=" * 74)
    print("  首次无网软键菜单")
    print("=" * 74)

    # resolve()：临时目录的 8.3 短名（ADMINI~1）和写进 ini 的长名不一样，
    # 不统一的话下面「ini 里记住的目录」会误判。
    tmp = Path(tempfile.mkdtemp(prefix="me_softkey_")).resolve()
    real_localappdata = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = str(tmp / "localappdata")
    os.environ["ME_NO_NET"] = "1"          # 清单只能走内置兜底
    os.environ["ME_NO_BROWSER"] = "1"      # 别真去开浏览器
    os.environ.pop("ME_NO_SOFTKEY", None)

    from me.tools import fetch
    from me.tools import manifest as mf
    from me.tools import paths, softkey

    # 开发机上 tools/ 里可能已经装了真工具（--tools-get 下的）。这里验的是
    # 「一台什么都没有的机器」，所以把工具根换到临时目录去，别让真文件干扰。
    real_tools_dir, real_root = paths.TOOLS_DIR, paths.ROOT
    paths.TOOLS_DIR = tmp / "no-tools"
    paths.ROOT = tmp / "no-app"

    try:
        (tmp / "localappdata").mkdir(parents=True, exist_ok=True)
        mf.reset_for_tests()
        paths.clear_override_dir()

        # ---------------------------------------------------------- 四颗键
        print("\n[1] 四颗软键的定义")
        check(len(softkey.KEYS) == 4, "正好 4 颗", str(len(softkey.KEYS)))
        check([k for k, _ in softkey.KEYS] == ["1", "2", "3", "4"],
              "编号是 1/2/3/4", str([k for k, _ in softkey.KEYS]))
        labels = " ".join(lb for _k, lb in softkey.KEYS)
        check("下载" in labels, "1 是下载全量包", labels)
        check("扫描" in labels, "2 是扫描", labels)
        check("指定" in labels, "3 是手动指定", labels)
        check("自研" in labels, "4 是仅用自研", labels)

        # ---------------------------------------------------------- 什么时候要弹
        print("\n[2] needed()：什么时候该弹")
        check(not paths.any_installed(), "临时环境里一个工具都没有",
              str(paths.installed()))
        check(not paths.override_dir(), "没有 toolsdir.ini")
        check(mf.snapshot().source == "builtin", "清单来自内置兜底",
              mf.snapshot().source)
        check(softkey.needed() is True, "没工具 + 内置清单 -> 要弹")

        os.environ["ME_NO_SOFTKEY"] = "1"
        check(softkey.needed() is False, "ME_NO_SOFTKEY=1 直接跳过")
        os.environ.pop("ME_NO_SOFTKEY")

        # 清单来自缓存就不弹了（说明至少拉到过一次，只是工具还没装）
        mf.save_cache("[meta]\nversion = 2\n[utmt]\npaths = utmt/a.exe\n")
        check(mf.load_cache() is not None, "缓存已写入")
        check(softkey.needed() is False, "清单来自缓存 -> 不弹")
        mf.cache_path().unlink()
        check(softkey.needed() is True, "缓存删掉 -> 又该弹了")

        # ---------------------------------------------------------- 渲染
        print("\n[3] render()：菜单长什么样")
        ui = FakeUi()
        check(softkey.render(ui) == "ask", "render 返回 ask")
        out = ui.text()
        for k in ("1", "2", "3", "4"):
            check(f"[{k}]" in out, f"列出了 [{k}]", out)
        check("内置兜底" in out, "点明清单来源", out)
        check("http" in out, "给出全量包地址", out)
        check(mf.snapshot().full_package in out, "地址取自清单")

        # ---------------------------------------------------------- 读键
        print("\n[4] ask()：数字 -> 动作")
        for key, want in (("1", "download"), ("2", "scan"),
                          ("3", "pick"), ("4", "self")):
            got = softkey.ask(FakeUi([key]))
            check(got == want, f"{key} -> {want}", got)
        check(softkey.ask(FakeUi(["9"])) == "", "9 -> 无动作")
        check(softkey.ask(FakeUi([""])) == "", "空输入 -> 无动作")
        check(softkey.ask(FakeUi([" 4 "])) == "self", "两边空格能容忍")
        check(softkey.ask(FakeUi([])) == "", "没有输入也不炸")

        # ---------------------------------------------------------- 键 1
        print("\n[5] 键 1：自动下载（程序自己下，不是给链接）")
        calls = []
        real_install = fetch.install

        def fake_install(keys=None, root=None, force=False, log=None,
                         tick=None, allow_package=True):
            calls.append({"keys": list(keys or []),
                          "allow_package": allow_package})
            if log:
                log("  （假装从网上下载）")
            if tick:
                tick(512, 1024, "fake.zip")
                tick(1024, 1024, "fake.zip")
            # 真的把工具放到临时工具根里，后面的判定才会跟着变
            tree2 = make_tool_tree(tmp / "no-tools", "utmt")
            return ([fetch.Result("utmt", ok=True, how="zip",
                                  path=tree2 / "utmt" / "UndertaleModCli.exe",
                                  size=1024)], False)

        fetch.install = fake_install
        try:
            ui = FakeUi()
            check(softkey.handle(ui, "download") == "ok", "返回 ok（可继续）")
            out1 = ui.text()
            check("下载" in out1, "先说清要下哪些", out1[:200])
            check("下载完成" in out1, "报告下载完成", out1[:300])
            check("解压" in out1 or "假装从网上" in out1 or "下载" in out1,
                  "有过程输出", out1[:300])
            check(calls and calls[0]["keys"] == ["utmt", "assetstudio",
                                                 "evbunpack", "repak"],
                  f"把缺的都交给下载器（{calls[0]['keys'] if calls else None}）")
            check(calls and calls[0]["allow_package"] is False,
                  "按键 1 不会偷偷下 140 MB 全量包")
            check(paths.any_installed() is True, "下完工具根里真的有 exe")
        finally:
            fetch.install = real_install

        # ---------------------------------------------------------- 键 2
        print("\n[6] 键 2：扫描 + 认领")
        shutil.rmtree(tmp / "no-tools", ignore_errors=True)   # 回到「什么都没有」
        check(not paths.any_installed(), "清掉后又是没工具的状态")
        real_scan_roots = softkey._scan_roots

        # 先验「扫不到」这一支
        softkey._scan_roots = lambda: [tmp / "empty"]
        (tmp / "empty").mkdir(parents=True, exist_ok=True)
        check(softkey.scan() is None, "空目录 -> 扫不到")
        ui = FakeUi()
        check(softkey.handle(ui, "scan") == "ok", "扫不到也返回 ok（不崩）")
        check("未找到工具" in ui.text(), "明说没扫到", ui.text())
        check(softkey.needed() is True, "还是该弹（工具没就位）")

        # 再验「扫到了」这一支
        tree = make_tool_tree(tmp / "somewhere" / "MultiExtractor", "utmt")
        softkey._scan_roots = lambda: [tmp / "somewhere"]
        hit = softkey.scan()
        check(hit is not None, "扫到了", str(hit))
        check(hit == tree, "扫到的是工具根（exe 的上两级）", f"{hit} != {tree}")

        ui = FakeUi()
        check(softkey.handle(ui, "scan") == "ok", "认领后返回 ok")
        check("工具根目录" in ui.text(), "提示认得这个目录", ui.text())
        check(paths.any_installed() is True, "工具就位")
        check(paths.find("utmt") is not None, "utmt 找得到", str(paths.find("utmt")))
        check(softkey.needed() is False, "工具就位后不再弹")
        od = paths.override_dir()
        check(od is not None and od.resolve() == tree.resolve(),
              "toolsdir.ini 记住了这个目录", str(od))

        ui = FakeUi()
        check(softkey.handle(ui, "scan") == "ok", "已经有工具时再扫也不炸")

        softkey._scan_roots = real_scan_roots

        # ---------------------------------------------------------- 键 3
        print("\n[7] 键 3：指定工具根目录")
        paths.clear_override_dir()
        check(not paths.any_installed(), "清掉后回到没工具")
        ui = FakeUi([str(tmp / "nosuchdir")])
        check(softkey.handle(ui, "pick") == "ok", "目录不存在也返回 ok")
        check("未找到工具" in ui.text(), "明说这个目录不行", ui.text())
        check(not paths.any_installed(), "没认领成功")

        ui = FakeUi([""])
        check(softkey.handle(ui, "pick") == "ok", "直接回车 -> ok")
        check(not paths.any_installed(), "空输入不改任何状态")

        ui = FakeUi([str(tree)])
        check(softkey.handle(ui, "pick") == "ok", "指定能用的目录 -> ok")
        check("工具根目录" in ui.text(), "提示已记住工具根目录", ui.text())
        check(paths.any_installed() is True, "这一次认领成功")

        # ---------------------------------------------------------- 键 4
        print("\n[8] 键 4：仅用自研")
        ui = FakeUi()
        check(softkey.handle(ui, "self") == "self", "返回 self")
        check("自研" in ui.text(), "提示已切自研", ui.text())

        print("\n[9] 未知动作")
        check(softkey.handle(FakeUi(), "") == "", "空动作 -> 空")
        check(softkey.handle(FakeUi(), "bogus") == "", "乱写的动作 -> 空")

        # ---------------------------------------------------------- 斜杠指令
        print("\n[9b] 软键菜单里也认斜杠指令")
        from me import ui as _ui

        seen_cmds = []
        real_cmd = _ui.command
        _ui.command = lambda raw: (seen_cmds.append(raw), None)[1]
        try:
            paths.clear_override_dir()
            mf.reset_for_tests()
            ui_c = FakeUi(["/settings", "4"])
            check(softkey.run(ui_c) == "self", "/settings 之后还能按 4")
            check(seen_cmds == ["/settings"], "指令被交给指令层", str(seen_cmds))

            seen_cmds.clear()
            _ui.command = lambda raw: "quit"
            ui_q = FakeUi(["/quit"])
            check(softkey.run(ui_q) == "quit", "/quit 让软键循环退出")
        finally:
            _ui.command = real_cmd

        # ---------------------------------------------------------- 完整循环
        print("\n[10] run()：完整循环")
        paths.clear_override_dir()
        mf.reset_for_tests()
        ui = FakeUi(["9", "4"])          # 先按错，再按 4
        check(softkey.run(ui) == "self", "先错后 4 -> self")
        check("范围 1-4" in ui.text(), "非法输入有提示", ui.text())
        check(ui.answers == [], "两次输入都被消费掉")

        paths.clear_override_dir()
        mf.reset_for_tests()
        softkey._scan_roots = lambda: [tmp / "somewhere"]
        ui = FakeUi(["2"])               # 按键 2 扫到工具，循环应当自己结束
        check(softkey.run(ui) == "ok", "扫描成功后 run 返回 ok")
        check("外部工具可用" in ui.text(), "提示外部工具可用", ui.text())
        softkey._scan_roots = real_scan_roots

        # ---------------------------------------------------------- adopt 本身
        print("\n[11] adopt()：认领与拒绝")
        paths.clear_override_dir()
        check(softkey.adopt(tmp / "nosuchdir") is False, "目录不存在 -> False")
        check(paths.override_dir() is None, "不存在的目录不写 ini")
        check(softkey.adopt(tree) is True, "真目录 -> True")
        check(paths.set_override_dir(tmp).name == "toolsdir.ini",
              "写的文件名是 toolsdir.ini")
        paths.clear_override_dir()

        print("\n" + "=" * 74)
        if FAIL:
            print(f"  失败 {FAIL} 项")
            return 1
        print(f"  全部通过（{PASS} 项）")
        return 0
    finally:
        paths.TOOLS_DIR, paths.ROOT = real_tools_dir, real_root
        mf.reset_for_tests()
        try:
            paths.clear_override_dir()
        except Exception:                              # noqa: BLE001
            pass
        os.environ.pop("ME_NO_SOFTKEY", None)
        os.environ.pop("ME_NO_BROWSER", None)
        if real_localappdata is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = real_localappdata
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
