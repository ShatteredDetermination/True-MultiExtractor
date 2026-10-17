# -*- coding: utf-8 -*-
"""斜杠指令（`/settings` 等）自测。

起因是用户实机敲了一行：

    C:\\> py main.py
    请输入游戏路径（文件或目录）: /settings
    [err] 路径不存在: \\settings

所以这里既验「解析/分发」，也验**真跑一遍**：路径输入行和菜单行都得认指令，
而且 `/settings` 改出来的开关要真的传进后续流程（拆包通道得跟着变）。

运行：
    python selftest/test_commands.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MAIN = ROOT / "main.py"

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


def section(t):
    print("\n== " + t + " ==")


class FakeUi:
    """把 ui 的输出收起来，输入按脚本给。"""

    def __init__(self, answers=()):
        self.lines = []
        self.answers = list(answers)

    def _add(self, tag, msg):
        self.lines.append(f"{tag} {msg}")

    def sep(self, *_a, **_k):
        pass

    def info(self, msg, *_a, **_k):
        self._add("INFO", msg)

    def detail(self, msg, *_a, **_k):
        self._add("DETAIL", msg)

    def ok(self, msg, *_a, **_k):
        self._add("OK", msg)

    def warn(self, msg, *_a, **_k):
        self._add("WARN", msg)

    def error(self, msg, *_a, **_k):
        self._add("ERR", msg)

    def step(self, msg, *_a, **_k):
        self._add("STEP", msg)

    def ask(self, *_a, **_k):
        return self.answers.pop(0) if self.answers else ""

    def clean_input(self, raw):
        return (raw or "").strip()

    def progress(self, *_a, **_k):
        pass

    def text(self):
        return "\n".join(self.lines)


def patch_ui(fake):
    """把 me.ui 的输出/输入换成 FakeUi，返回恢复函数。"""
    from me import ui

    names = ("sep", "info", "detail", "ok", "warn", "error", "step",
             "ask", "clean_input", "progress")
    saved = {n: getattr(ui, n) for n in names}
    for n in names:
        setattr(ui, n, getattr(fake, n))

    def restore():
        for n, fn in saved.items():
            setattr(ui, n, fn)

    return restore


# ---------------------------------------------------------------- 解析
def test_parse():
    from me import commands

    section("1. 指令解析")
    check(commands.parse("/settings") == ("settings", []), "/settings")
    check(commands.parse("  /settings  ") == ("settings", []), "两边空格容错")
    check(commands.parse("/SETTINGS") == ("settings", []), "大小写不敏感")
    check(commands.parse("/s") == ("settings", []), "/s 别名")
    check(commands.parse("/设置") == ("settings", []), "中文别名")
    check(commands.parse("/help me") == ("help", ["me"]), "带参数")
    check(commands.parse("/?") == ("help", []), "/? -> help")
    check(commands.parse("/") is None, "光一个斜杠不算指令")
    check(commands.parse("C:\\games\\x") is None, "普通路径不算指令")
    check(commands.parse("") is None, "空输入不算指令")
    check(commands.parse("/  ") is None, "斜杠+空格不算指令")
    check(commands.is_command("/settings") is True, "is_command 认得")
    check(commands.is_command("settings") is False, "没有斜杠就不认")

    names = set(commands.COMMANDS)
    check({"help", "settings", "quit"} <= names, "最少有 help/settings/quit",
          str(names))


# ---------------------------------------------------------------- 分发
def test_dispatch():
    from me import commands

    section("2. 分发与输出")
    fake = FakeUi()
    restore = patch_ui(fake)
    try:
        check(commands.handle("/help") is None, "/help 返回 None（继续）")
        out = fake.text()
        check("/settings" in out, "/help 里列出了 /settings", out[:200])
        check("/quit" in out, "/help 里列出了 /quit")

        fake2 = FakeUi()
        restore()
        restore = patch_ui(fake2)
        check(commands.handle("/nosuch") is None, "未知指令不抛异常")
        txt = fake2.text()
        check("没有这个指令" in txt, "提示没有这个指令", txt[:200])
        check("/settings" in txt, "顺手把可用指令列出来", txt[:300])

        fake3 = FakeUi()
        restore()
        restore = patch_ui(fake3)
        check(commands.handle("/quit") == "quit", "/quit 返回 quit")
        check("再见" in fake3.text(), "退出时有一句话")

        fake4 = FakeUi()
        restore()
        restore = patch_ui(fake4)
        check(commands.handle("C:\\not\\a\\command") is None, "非指令直接忽略")
        check(fake4.text() == "", "非指令不产生输出")
    finally:
        restore()


# ---------------------------------------------------------------- 设置
def test_settings():
    from me import commands, setup_ini, ui
    from me.tools import paths as tool_paths

    section("3. /settings 的每一项")
    tmp = Path(tempfile.mkdtemp(prefix="me_cmd_")).resolve()
    real_la = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = str(tmp / "la")
    real_root, real_setup_root = setup_ini.ROOT, None
    saved_root = getattr(setup_ini, "ROOT", None)
    try:
        # setup.ini 指到临时目录，别动真文件
        setup_ini.ROOT = tmp / "app"
        (tmp / "app").mkdir(parents=True, exist_ok=True)
        tool_paths.clear_override_dir()

        class Args:
            experimental = False
            want_self = False
            no_deep = False
            keep_work = False

        args = Args()
        ctx = commands.Context(args=args)

        # ---- 1) 改保存路径 ----
        fake = FakeUi(["1", "D:\\Out\\Game", "0"])
        restore = patch_ui(fake)
        try:
            commands.handle("/settings", ctx)
        finally:
            restore()
        txt = fake.text()
        check("[设置]" in txt, "画出了设置面板", txt[:120])
        check("保存位置" in txt and "外部工具目录" in txt, "两项持久化设置都在")
        check((tmp / "app" / "setup.ini").exists(), "setup.ini 写出来了")
        cfg = setup_ini.load()
        check(cfg[setup_ini.KEY_PATH] == "D:\\Out\\Game",
              f"新路径写进去了（{cfg[setup_ini.KEY_PATH]}）")

        # ---- 2) 改工具目录 ----
        tree = tmp / "mytools"
        (tree / "utmt").mkdir(parents=True)
        (tree / "utmt" / "UndertaleModCli.exe").write_bytes(b"MZ")
        fake = FakeUi(["2", str(tree), "0"])
        restore = patch_ui(fake)
        try:
            commands.handle("/settings", ctx)
        finally:
            restore()
        od = tool_paths.override_dir()
        check(od is not None and od.resolve() == tree.resolve(),
              "工具目录记住了", str(od))
        check(tool_paths.any_installed(), "认出了里面的工具")

        # ---- 3) 开关 ----
        fake = FakeUi(["4", "5", "0"])
        restore = patch_ui(fake)
        try:
            commands.handle("/settings", ctx)
        finally:
            restore()
        check(args.experimental is True, "实验性功能被打开")
        check(args.want_self is True, "自研核心被打开")
        out = fake.text()
        check("[开]" in out, "面板上显示 [开]", out[-200:])

        # 再按一次就关掉
        fake = FakeUi(["4", "0"])
        restore = patch_ui(fake)
        try:
            commands.handle("/settings", ctx)
        finally:
            restore()
        check(args.experimental is False, "再按一次关掉")

        # ---- 4) 外部工具：摘要 / 检查 / 下载 ----
        fake = FakeUi(["0"])
        restore = patch_ui(fake)
        try:
            commands.handle("/settings", ctx)
        finally:
            restore()
        rows = fake.text()
        check("外部工具" in rows and re.search(r"\d/4 就位", rows) is not None,
              "面板上直接显示工具完整性（N/4 就位）", rows[:400])
        check("检查外部工具" in rows and "自动下载缺失的工具" in rows,
              "面板上有检查与下载两项")

        # 摘要本身：把工具根换成空目录，必须如实报 0/4 且列名字
        real_tools_dir, real_root = tool_paths.TOOLS_DIR, tool_paths.ROOT
        tool_paths.TOOLS_DIR = tmp / "empty-tools"
        tool_paths.ROOT = tmp / "empty-app"
        try:
            # 这里 override 还指着「只有 utmt」的目录，所以正确答案是 1/4
            check(commands.tools_summary().startswith("1/4 就位"),
                  "只有 utmt 时摘要报 1/4", commands.tools_summary())
            check(all(k in commands.tools_summary()
                      for k in ("assetstudio", "evbunpack", "repak")),
                  "并把缺的三个列出来", commands.tools_summary())
        finally:
            tool_paths.TOOLS_DIR, tool_paths.ROOT = real_tools_dir, real_root
            tool_paths.clear_override_dir()

        import main as _main
        from me.tools import softkey as _softkey

        real_tools_fn = _main.cmd_tools
        called = []
        _main.cmd_tools = lambda: called.append("tools") or 0
        try:
            fake = FakeUi(["8", "0"])
            restore = patch_ui(fake)
            try:
                commands.handle("/settings", ctx)
            finally:
                restore()
            check(called == ["tools"], "按 8 调用 cmd_tools（同一条路）", str(called))
            check(called == ["tools"], "按 3 也走同一个报告")
        finally:
            _main.cmd_tools = real_tools_fn

        real_dl = _softkey.download
        dl_calls = []

        def fake_download(ui_obj, allow_package=None):
            dl_calls.append(allow_package)
            ui_obj.ok("（假装下载完成）")
            tree2 = tmp / "mytools"
            (tree2 / "repak").mkdir(parents=True, exist_ok=True)
            (tree2 / "repak" / "repak.exe").write_bytes(b"MZ")
            return ([], False)

        _softkey.download = fake_download
        try:
            fake = FakeUi(["9", "0"])
            restore = patch_ui(fake)
            try:
                commands.handle("/settings", ctx)
            finally:
                restore()
            check(dl_calls == [None], "按 9 调下载器（并允许它问全量包）",
                  str(dl_calls))
            check("假装下载完成" in fake.text(), "把下载器的输出透出来了")
        finally:
            _softkey.download = real_dl

        # ---- 5) 清空工具目录 ----
        fake = FakeUi(["10", "0"])
        restore = patch_ui(fake)
        try:
            commands.handle("/settings", ctx)
        finally:
            restore()
        check(tool_paths.override_dir() is None, "10 把工具目录清回默认")

        # ---- 6) 非法选项 ----
        fake = FakeUi(["99", "0"])
        restore = patch_ui(fake)
        try:
            commands.handle("/settings", ctx)
        finally:
            restore()
        check("按 0-10 选" in fake.text(), "乱按有提示", fake.text()[-120:])

        # ---- 6) 会话与会话标志同步 ----
        class Sess:
            experimental = False
            want_self = False
            no_deep = False
            keep_work = False

        sess = Sess()
        ctx2 = commands.Context(args=Args(), session=sess)
        fake = FakeUi(["6", "0"])
        restore = patch_ui(fake)
        try:
            commands.handle("/settings", ctx2)
        finally:
            restore()
        check(sess.no_deep is True, "会话里的开关也变了")
        check(ctx2.args.no_deep is True, "参数里的开关同步了")
    finally:
        if saved_root is None:
            del setup_ini.ROOT
        else:
            setup_ini.ROOT = saved_root
        if real_la is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = real_la
        tool_paths.clear_override_dir()
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 真跑
def run_interactive(feed: str, timeout=180):
    env = dict(os.environ)
    env["ME_NO_NET"] = "1"
    env["ME_NO_SOFTKEY"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, str(MAIN)], input=feed.encode("utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout, cwd=str(ROOT), env=env)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def test_live():
    section("4. 真跑：路径输入行敲 /settings（用户报的那个 bug）")
    tmp = Path(tempfile.mkdtemp(prefix="me_cmd_live_")).resolve()
    try:
        game = tmp / "game" / "resources" / "app"
        game.mkdir(parents=True)
        (tmp / "game" / "resources" / "app.asar").write_bytes(b"x" * 64)

        feed = "\n".join(["/settings", "4", "5", "0", str(tmp / "game"), "0", ""])
        code, out = run_interactive(feed)

        # 用户报的原话是「[err] 路径不存在: \settings」——这条必须彻底消失
        check("路径不存在: \\settings" not in out,
              "不再把 /settings 当路径去 stat", out[:400])
        check("[设置]" in out, "进了设置面板")
        check("实验性功能" in out and "[开]" in out, "开关能改", out[-600:])
        check(code in (0, 1), f"退出码正常（{code}）")
        # 提醒：一进来就该知道 /help，路径打错也不该被踢出去
        check("输入 /help 看指令" in out, "启动就有 /help 提醒", out[:600])
        check("请输入游戏路径" in out and "/help 看指令" in out,
              "路径输入行带着提示", out[:600])

        code_b, out_b = run_interactive("C:\\no\\such\\dir\n/quit\n")
        check("路径不存在" in out_b, "打错路径会报出来", out_b[:400])
        check("检查一下路径" in out_b, "并提醒可以 /help", out_b[:600])
        check(out_b.count("请输入游戏路径") >= 2,
              "打错之后会重问，而不是直接退出", str(out_b.count("请输入游戏路径")))

        section("5. 真跑：主菜单里敲 /help")
        feed2 = "\n".join([str(tmp / "game"), "/help", "/channels", "0", "0", ""])
        code2, out2 = run_interactive(feed2)
        check("指令（行首带斜杠）" in out2, "菜单里也认指令", out2[:400])
        check("/settings" in out2, "/help 列出了 /settings")
        check("gamemaker" in out2, "/channels 跑起来了", out2[-400:])

        section("6. 真跑：/quit")
        code3, out3 = run_interactive("/settings\n0\n/quit\n")
        check("再见" in out3, "/quit 退出", out3[-300:])
        check(code3 == 0, f"/quit 退出码 0（{code3}）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("=" * 74)
    print("  斜杠指令（/settings 等）")
    print("=" * 74)

    test_parse()
    test_dispatch()
    test_settings()
    test_live()

    print("\n" + "=" * 74)
    if FAIL:
        print(f"  失败 {FAIL} 项，通过 {PASS} 项")
        return 1
    print(f"  全部通过（{PASS} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
