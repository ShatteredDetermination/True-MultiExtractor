# -*- coding: utf-8 -*-
"""交互菜单自测：渐进式状态机 + 索引到参数的映射。

盯的是四件事：

  A. 渐进式：没分析之前只给「分析 / 更换路径 / 退出」，不把提取/重建糊一脸；
  B. 菜单项按引擎能力生成：GameMaker 才有修复和通道切换，CTF/Godot 没有；
  C. **索引 ↔ 参数一一对应，不允许错位**：修复子菜单
     0→--rebuild、1→--repair、2→--fix；提取子菜单 0→image … 4→全部（不带 --only）；
  D. 菜单拼出来的 argv 能被**同一条** argparse 解析回来（菜单与命令行同路），
     并且真起子进程喂 stdin，确认渲染顺序、状态保持、正常退出。

用法：
    python selftest/test_cli_menu.py
"""

import contextlib
import io
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main as cli                              # noqa: E402
from me import menu_flow                        # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

SAMPLE = Path(r"C:\拆包教程素材\目标游戏\Undertale True Genocide")


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {msg}")
    else:
        FAIL += 1
        FAILURES.append(msg)
        print(f"  [FAIL] {msg}")


def section(title):
    print("\n== " + title + " ==")


def actions(items):
    return [a for _label, a in items]


def sess_for(engine=""):
    return menu_flow.Session("TARGET", engine_key=engine, engine_name=engine,
                             analyzed=bool(engine))


# ---------------------------------------------------------------- A 渐进式
def test_progressive():
    section("A. 分析之前不给提取/重建")
    items = menu_flow.pre_analyze_options()
    check(actions(items) == [menu_flow.ANALYZE, menu_flow.PATH, menu_flow.EXIT],
          f"分析前只有 3 项（{actions(items)}）")
    labels = [label for label, _a in items]
    check(not any("提取" in x for x in labels), "分析前没有「提取资源」")
    check(not any("修复" in x or "重建" in x for x in labels), "分析前没有「修复/重建」")
    check(any("--detect" in x for x in labels), "有「分析目标（--detect）」入口")

    gm = menu_flow.main_options(sess_for("gamemaker"))
    check(actions(gm)[0] == menu_flow.EXTRACT, "分析之后第一项就是提取")
    check(any("--detect" in x for x, _a in gm) is False, "分析之后不再要求分析")


# ---------------------------------------------------------------- B 按能力生成
def test_capabilities():
    section("B. 菜单项按引擎能力生成")

    gm = actions(menu_flow.main_options(sess_for("gamemaker")))
    check(gm == [menu_flow.EXTRACT, menu_flow.REPAIR_MENU, menu_flow.CHANNEL_MENU,
                 menu_flow.DETAIL, menu_flow.PATH, menu_flow.MORE, menu_flow.EXIT],
          f"GameMaker 给了提取+修复+通道（{gm}）")

    ctf = actions(menu_flow.main_options(sess_for("clickteam")))
    check(menu_flow.REPAIR_MENU not in ctf, "CTF 不给修复/重建（只对 GM 有意义）")
    check(menu_flow.CHANNEL_MENU not in ctf, "CTF 不给通道切换（没有第三方）")
    check(ctf == [menu_flow.EXTRACT, menu_flow.DETAIL, menu_flow.PATH,
                  menu_flow.MORE, menu_flow.EXIT], f"CTF 菜单 {ctf}")

    godot = actions(menu_flow.main_options(sess_for("godot")))
    check(menu_flow.CHANNEL_MENU not in godot, "Godot 不给通道切换（没有第三方）")
    check(menu_flow.REPAIR_MENU not in godot, "Godot 不给修复/重建")

    check(menu_flow.repair_supported("gamemaker"), "repair_supported(gamemaker)")
    for k in ("clickteam", "godot", "unity", "unreal", "enigma", "rpgmaker"):
        check(not menu_flow.repair_supported(k), f"repair_supported({k}) 为假")

    for k in ("gamemaker", "unity", "unreal", "enigma"):
        check(menu_flow.channel_switchable(k), f"{k} 双通道可切换")
    for k in ("clickteam", "godot", "rpgmaker", "electron", "generic"):
        check(not menu_flow.channel_switchable(k), f"{k} 不可切换")


# ---------------------------------------------------------------- C 索引错位
def test_index_mapping():
    section("C. 索引 → 参数，一一对应不错位")
    sess = sess_for("gamemaker")

    rep = menu_flow.repair_options()
    check(actions(rep) == [menu_flow.REBUILD, menu_flow.PATCH, menu_flow.FIX, "back"],
          f"修复子菜单顺序（{actions(rep)}）")

    spec = [(0, menu_flow.REBUILD, "--rebuild"),
            (1, menu_flow.PATCH, "--repair"),
            (2, menu_flow.FIX, "--fix")]
    for i, mode, flag in spec:
        check(rep[i][1] == mode, f"按 {i} 拿到的是 {mode}")
        argv = menu_flow.build_repair_argv(sess, rep[i][1])
        check(argv == ["TARGET", flag], f"按 {i} 拼出来是 {argv}")

    check(rep[3][1] == "back", "按 3 是取消返回，不会误跑修复")

    ex = menu_flow.extract_options(sess)
    check(actions(ex)[-1] == "back", "提取子菜单最后一项是返回")
    want = [{"image"}, {"audio"}, {"text"}, {"font"}, None]
    for i, cats in enumerate(want):
        key = ex[i][1]
        got = None if key == "cat:all" else set(key[4:].split(","))
        check(got == cats, f"提取第 {i} 项 → {cats}")
        argv = menu_flow.build_extract_argv(sess, got)
        if cats is None:
            check("--only" not in argv, "「提取全部」不带 --only")
        else:
            check(argv[argv.index("--only") + 1] == ",".join(sorted(cats)),
                  f"第 {i} 项 --only {sorted(cats)}")

    ch = actions(menu_flow.channel_options(sess))
    check(ch == ["switch_self", "back"],
          f"默认第三方时通道菜单只给「切到自研」（{ch}）")
    sess.want_self = True
    sess.experimental = True
    check(actions(menu_flow.channel_options(sess)) == ["switch_third", "back"],
          "已是自研时只给「切到第三方」")


# ---------------------------------------------------------------- D 同一条解析
def test_argv_roundtrip():
    section("D. 菜单 argv 走同一条 argparse")
    p = cli._build_parser()
    sess = menu_flow.Session("TARGET", engine_key="gamemaker", analyzed=True,
                             experimental=True, want_self=True)

    attrs = {menu_flow.REBUILD: "rebuild", menu_flow.PATCH: "repair",
             menu_flow.FIX: "fix"}
    for mode, attr in attrs.items():
        ns = p.parse_args(menu_flow.build_repair_argv(sess, mode))
        others = [a for a in attrs.values() if a != attr]
        check(ns.target == "TARGET", f"{mode}: target 解析正确")
        check(getattr(ns, attr) is True, f"{mode}: --{attr} 被置位")
        check(not any(getattr(ns, a) for a in others),
              f"{mode}: 另外两个开关没被误置位")
        check(ns.experimental and ns.want_self, f"{mode}: 通道参数透传")

    ns = p.parse_args(menu_flow.build_extract_argv(sess, {"image", "audio"}))
    check(ns.only == "audio,image", f"提取分类排序稳定（{ns.only}）")
    check(ns.no_deep is False and ns.keep_work is False, "默认不带 --no-deep/--keep-work")

    sess.no_deep = True
    sess.keep_work = True
    sess.forced = "unreal"
    sess.out = r"D:\out"
    ns = p.parse_args(menu_flow.build_extract_argv(sess, None))
    check(ns.no_deep and ns.keep_work, "--no-deep / --keep-work 解析到")
    check(ns.engine == "unreal", "-e 解析到 engine")
    check(ns.out == r"D:\out", "-o 解析到 out")

    sess.want_self = False
    ns = p.parse_args(menu_flow.build_extract_argv(sess, None))
    check(ns.experimental is True and ns.want_self is False,
          "只开实验性功能时 --self 不出现（仍走第三方）")

    plain = menu_flow.Session("TARGET")
    check(menu_flow.channel_flags(plain) == [], "两个开关都没开 → 不带通道参数")
    check(menu_flow.channel_flags(menu_flow.Session("T", experimental=True)) ==
          ["--experimental"], "只开实验性 → 仅 --experimental")

    # 真跑一次：菜单那条路（_run_argv）与 main() 共用 _dispatch
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli._run_argv([str(SAMPLE if SAMPLE.exists() else ROOT), "--detect"])
    out = buf.getvalue()
    check(rc == 0, f"_run_argv(--detect) 退出码 {rc}")
    check("识别结果" in out and "引擎键:" in out, "_run_argv 走到了 cmd_detect")


# ---------------------------------------------------------------- E 分支齐全
def test_handlers():
    section("E. 每个菜单动作都有处理分支")
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    for const in ("ANALYZE", "EXTRACT", "REPAIR_MENU", "CHANNEL_MENU",
                  "DETAIL", "PATH", "MORE", "EXIT"):
        check(f"menu_flow.{const}" in src, f"{const} 在主循环里有分支")

    for fn in ("build_extract_argv", "build_repair_argv", "_run_argv", "_analyze",
               "extract_options", "repair_options", "channel_options",
               "more_options", "pre_analyze_options", "main_options"):
        check(fn in src, f"main.py 用到了 {fn}")

    more_body = src[src.index("def _menu_more("):src.index("def _ask_target(")]
    for key in actions(menu_flow.more_options(sess_for("gamemaker"))):
        if key == "back":
            continue
        check(f'"{key}"' in more_body, f"更多选项「{key}」有分支")

    chan_body = src[src.index("def _menu_channel("):src.index("def _menu_more(")]
    for key in ("switch_self", "switch_third", "back"):
        check(f'"{key}"' in chan_body, f"通道菜单「{key}」有分支")

    check("_menu_engine(" in src, "「指定引擎（-e）」有入口")


# ---------------------------------------------------------------- F 真跑
def _feed(target, engine_key, picks):
    """拼一段 stdin：路径 → 分析 → 各步。picks 是动作名，索引现算。"""
    pre = {a: i for i, (_l, a) in enumerate(menu_flow.pre_analyze_options())}
    main = {a: i for i, (_l, a) in enumerate(menu_flow.main_options(sess_for(engine_key)))}
    rep = {a: i for i, (_l, a) in enumerate(menu_flow.repair_options())}
    more = {a: i for i, (_l, a) in enumerate(menu_flow.more_options(sess_for(engine_key)))}
    chan = {a: i for i, (_l, a) in enumerate(menu_flow.channel_options(sess_for(engine_key)))}
    lines = [str(target), str(pre[menu_flow.ANALYZE])]
    for name in picks:
        if name in main:
            lines.append(str(main[name]))
        elif name in rep:
            lines.append(str(rep[name]))
        elif name in more:
            lines.append(str(more[name]))
        elif name in chan:
            lines.append(str(chan[name]))
        elif name == "back":
            lines.append(str(len(rep) - 1))
        else:
            raise KeyError(name)
    return "\n".join(lines)


def _run(feed: str, timeout=180):
    proc = subprocess.run([sys.executable, str(ROOT / "main.py")],
                          input=feed.encode("utf-8"), capture_output=True,
                          timeout=timeout, cwd=str(ROOT))
    return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", "replace")


def test_live(tmp: Path):
    section("F. 真起子进程跑交互菜单")
    target = SAMPLE if SAMPLE.exists() else tmp
    eng = cli.detect_engine(Path(str(target))).engine
    print(f"  （目标 {target}，识别为 {eng}）")

    # F1 分析 → 退出：确认「分析之后才出现提取」，且不再问分析
    rc, out = _run(_feed(target, eng, [menu_flow.EXIT]))
    check(rc == 0, f"分析→退出 退出码 {rc}")
    check("[0] 分析目标（--detect）" in out, "第一个菜单是「分析目标」")
    check("[ok] [检测结果]" in out, "分析结果打出来了")
    check("提取资源（--extract）" in out, "分析之后才出现提取")
    banner = out.index("[ok] [检测结果]")
    check(out.index("[0] 提取资源（--extract）") > banner,
          "提取项排在分析结果之后（不在分析前）")

    # F2 修复子菜单：只渲染，不执行
    if menu_flow.repair_supported(eng):
        rc, out = _run(_feed(target, eng, [menu_flow.REPAIR_MENU,
                                          "back", menu_flow.EXIT]))
        check(rc == 0, f"修复子菜单 退出码 {rc}")
        for word in ("全量重建", "原位补丁", "先重建再提取", "取消返回"):
            check(word in out, f"修复子菜单有「{word}」")
        check("执行: " not in out, "选了「取消返回」就没真跑修复")

    # F3 更多选项：开关状态要保持
    rc, out = _run(_feed(target, eng, [menu_flow.MORE, "toggle_deep",
                                      menu_flow.MORE, menu_flow.EXIT]))
    check(rc == 0, f"更多选项 退出码 {rc}")
    check("不解容器 [开]" in out, "开了不解容器后菜单显示 [开]")
    check("不解容器 [关]" in out, "开之前显示的是 [关]（状态真的变了）")
    check("执行: " not in out, "更多选项本身不触发提取")

    # F4 通道切换：切完标签要跟着变
    if menu_flow.channel_switchable(eng):
        rc, out = _run(_feed(target, eng, [menu_flow.CHANNEL_MENU, "switch_self",
                                           menu_flow.EXIT]))
        check(rc == 0, f"通道切换 退出码 {rc}")
        check("切换到" in out and "自研核心" in out, "通道菜单列出「切换到自研核心」")
        check("当前: 自研" in out, "切完之后主菜单显示当前: 自研")

    # F5 退出码：直接给退出，不该报错
    rc, out = _run(str(target) + "\n2\n", timeout=60)
    check(rc == 0, f"直接退出 退出码 {rc}")


# ---------------------------------------------------------------- main
def main() -> int:
    print("=" * 66)
    print("  交互菜单自测（渐进式状态机）")
    print("=" * 66)

    tmp = Path(tempfile.mkdtemp(prefix="me_menu_"))
    try:
        test_progressive()
        test_capabilities()
        test_index_mapping()
        test_argv_roundtrip()
        test_handlers()
        test_live(tmp)
    finally:
        import shutil
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
