# -*- coding: utf-8 -*-
"""unity.py / unreal.py 的自测。

本机没有 Unity / Unreal 的真实游戏样本，所以这里的策略是：
  A. 造假目录结构（<名字>_Data\\globalgamemanagers、Content\\Paks\\xxx.pak），
     验证「能不能正确找到目标路径」；
  B. 把 me.tools.proc.run 换成假的，截下真正的 argv，
     验证「拼出来的命令行对不对」（真 CLI 跑一次要几分钟，没法在单测里跑）；
  C. 把 me.tools.paths.TOOLS_DIR 指到空的临时目录，
     验证「缺 CLI 时抛的 EngineError 里有没有可执行的修复命令」；
  D. 真的跑一次 repak：pack 一个小目录成合法 .pak，再用 unreal.extract 解回来，
     做往返验证（这是唯一一个有真实外部工具参与的端到端验证）；
  E. 真的跑一次 AssetStudio.CLI（喂假目录），验证我们注释里写的
     「它永远退出 0」这个坑是真的。

运行：
    python selftest/test_unity_unreal.py
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

# 项目根从本文件位置推，别写死绝对路径：
# 版本归档会把整个目录搬到 Source Code\Ver x.xx\ 下，写死就立刻失效。
ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

from me.collect.writer import OutputWriter                      # noqa: E402
from me.engines import unity, unreal                            # noqa: E402
from me.engines.base import Context, EngineError                # noqa: E402
from me.scanner.detect import Detection                         # noqa: E402
from me.tools import paths as tool_paths                        # noqa: E402
from me.tools import proc                                       # noqa: E402
from me.tools import stream                                     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def section(t):
    print("\n" + "=" * 72 + f"\n{t}\n" + "=" * 72)


def make_ctx(target, out_root, work, categories=None):
    return Context(target=Path(target), detection=Detection(engine="x", name="x"),
                   writer=OutputWriter(out_root, categories=categories,
                                       engine="test", source=str(target)),
                   work_dir=Path(work), deep=True, categories=categories)


# ---------------------------------------------------------------- 造样本
def make_fake_unity(root: Path, with_assets: bool = True,
                    extra_loose=None) -> Path:
    """造一个标准 Unity 布局：<游戏>\\<游戏>_Data\\{globalgamemanagers,level0,...}

    注意 with_assets 有讲究：目录里只要有 .assets 文件，
    unity.find_input 就认它是数据目录（不需要清单文件）。而 .assets
    会被我们的散落资源兜底跳过，所以「AssetStudio 导出几个」这个计数
    不会被兜底污染，测试才能算准数。

    extra_loose: 额外裸放的资源文件名列表（放 StreamingAssets 下），
    用来测兜底收集。
    """
    game = root / "FakeUnityGame"
    data = game / "FakeUnityGame_Data"
    (data / "StreamingAssets").mkdir(parents=True, exist_ok=True)
    (data / "Managed").mkdir(parents=True, exist_ok=True)
    for name in ("globalgamemanagers", "globalgamemanagers.assets",
                 "level0", "mainData"):
        (data / name).write_bytes(b"\x00fake unity marker\x00")
    if with_assets:
        for name in ("resources.assets", "sharedassets0.assets"):
            (data / name).write_bytes(b"\x00fake unity asset\x00")
    for name in (extra_loose or ()):
        (data / "StreamingAssets" / name).write_bytes(b"loose payload")
    (game / "FakeUnityGame.exe").write_bytes(b"MZ fake")
    (game / "UnityPlayer.dll").write_bytes(b"MZ fake dll")
    return game


def make_fake_unreal(root: Path) -> Path:
    """造一个 Unreal 布局：<游戏>\\Content\\Paks\\*.pak + IoStore 文件。"""
    game = root / "FakeUnrealGame"
    paks = game / "Content" / "Paks"
    paks.mkdir(parents=True, exist_ok=True)
    (paks / "FakeUnrealGame-WindowsNoEditor.pak").write_bytes(b"not a real pak")
    (paks / "FakeUnrealGame-Windows.pak").write_bytes(b"not a real pak")
    (paks / "global.utoc").write_bytes(b"fake iostore toc")
    (paks / "global.ucas").write_bytes(b"fake iostore ucas")
    return game


# ---------------------------------------------------------------- A 路径定位
def test_unity_locate(tmp: Path):
    section("A1. Unity: 从游戏目录找到 *_Data")
    game = make_fake_unity(tmp / "unity_case")
    found = unity.find_data_dir(game)
    check("找到 FakeUnityGame_Data", found == game / "FakeUnityGame_Data", str(found))
    src, how = unity.find_input(game)
    check("find_input 指向 _Data 目录",
          src == game / "FakeUnityGame_Data", f"{how} -> {src}")

    section("A2. Unity: 直接把 _Data 目录当输入")
    src2, how2 = unity.find_input(game / "FakeUnityGame_Data")
    check("直接给 _Data 也能认",
          src2 == game / "FakeUnityGame_Data", f"{how2} -> {src2}")

    section("A3. Unity: 没有 Unity 特征的目录不该被误认")
    junk = tmp / "unity_case" / "JustAFolder"
    junk.mkdir(parents=True, exist_ok=True)
    (junk / "readme.txt").write_text("nope")
    check("普通目录 find_data_dir 返回 None", unity.find_data_dir(junk) is None)
    check("普通目录 find_input 返回 None", unity.find_input(junk)[0] is None)

    section("A4. Unity: 只有 .assets 没有清单文件时用容器兜底")
    loose = tmp / "unity_case" / "LooseGame"
    loose.mkdir(parents=True, exist_ok=True)
    (loose / "sharedassets0.assets").write_bytes(b"\x00")
    src3, how3 = unity.find_input(loose)
    check("兜底到 .assets 容器", src3 == loose / "sharedassets0.assets",
          f"{how3} -> {src3}")


def test_unreal_locate(tmp: Path):
    section("A5. Unreal: 找到所有 .pak")
    game = make_fake_unreal(tmp / "unreal_case")
    paks = unreal.find_paks(game)
    names = sorted(p.name for p in paks)
    check("找到 2 个 .pak", len(paks) == 2, str(names))
    check(".utoc/.ucas 没被当成 pak",
          all(p.suffix.lower() == ".pak" for p in paks))

    section("A6. Unreal: 找到 IoStore 文件")
    io = unreal.find_iostore(game)
    check("找到 2 个 IoStore 文件", len(io) == 2,
          str(sorted(p.name for p in io)))

    section("A7. Unreal: 单个 .pak 文件当输入")
    one = paks[0]
    check("文件输入只返回它自己", unreal.find_paks(one) == [one])
    check(".utoc 文件不会被当 pak",
          unreal.find_paks(game / "Content" / "Paks" / "global.utoc") == [])


# ---------------------------------------------------------------- B 命令行
def test_argv():
    section("B1. Unity: 命令行拼装")
    cli = Path(r"C:\tools\assetstudio\AssetStudio.CLI.exe")
    src = Path(r"C:\games\G\G_Data")
    outd = Path(r"C:\work\unity\images")
    argv = unity.build_argv(cli, src, outd, "Normal", "Texture2D Sprite")
    print("      " + " ".join(argv))
    check("input 在前、output 在后、--game 必填",
          argv[0] == str(cli) and argv[1] == str(src) and argv[2] == str(outd)
          and argv[3] == "--game" and argv[4] == "Normal")
    check("--types 拆成两个类型名",
          argv[5] == "--types" and argv[6] == "Texture2D" and argv[7] == "Sprite")
    check("--silent 收尾", argv[-1] == "--silent")

    argv2 = unity.build_argv(cli, src, outd, "GI", "AudioClip")
    check("音频趟只带 AudioClip",
          "--types" in argv2 and argv2[argv2.index("--types") + 1] == "AudioClip"
          and "Texture2D" not in argv2, " ".join(argv2[5:]))

    argv3 = unity.build_argv(cli, src, outd, "Normal",
                             "Texture2D:Sprite:Both")
    print("      " + " ".join(argv3))
    check("冒号形式也吃得下，且把 Both 这个导出选项摘掉",
          argv3[argv3.index("--types") + 1:argv3.index("--types") + 3]
          == ["Texture2D", "Sprite"] and "Both" not in argv3, " ".join(argv3))

    argv4 = unity.build_argv(cli, src, outd, "Normal", None)
    check("types=None 时不传 --types", "--types" not in argv4, " ".join(argv4))

    section("B2. Unreal: 命令行拼装（AES key 必须在子命令前）")
    rcli = Path(r"C:\tools\repak\repak.exe")
    pak = Path(r"C:\games\U\Content\Paks\U.pak")
    dst = Path(r"C:\work\unreal\00_U")
    a1 = unreal.build_argv(rcli, "list", pak, aes_key="AABBCC")
    print("      " + " ".join(a1))
    check("list: --aes-key 在子命令前面",
          a1[1] == "--aes-key" and a1[2] == "AABBCC" and a1[3] == "list",
          " ".join(a1))
    a2 = unreal.build_argv(rcli, "unpack", pak, out_dir=dst,
                           aes_key="AABBCC", force=True)
    print("      " + " ".join(a2))
    check("unpack: --output/--force 都带上",
          "--output" in a2 and str(dst) in a2 and "--force" in a2)
    check("unpack: --aes-key 仍在子命令前面",
          a2.index("--aes-key") < a2.index("unpack"), " ".join(a2))
    a3 = unreal.build_argv(rcli, "list", pak)
    check("没 key 时不传 --aes-key", "--aes-key" not in a3, " ".join(a3))


# ---------------------------------------------------------------- C 缺工具
def test_missing_cli(tmp: Path):
    section("C1. 缺 CLI 时的 EngineError（把 TOOLS_DIR 指到空目录）")
    empty = tmp / "empty_tools"
    empty.mkdir(parents=True, exist_ok=True)
    real_tools = tool_paths.TOOLS_DIR
    try:
        tool_paths.TOOLS_DIR = empty
        check("确实找不到 assetstudio", tool_paths.find("assetstudio") is None)
        check("确实找不到 repak", tool_paths.find("repak") is None)

        game = make_fake_unity(tmp / "unity_case")
        ctx = make_ctx(game, tmp / "o1", tmp / "w1")
        try:
            unity.extract(ctx)
            check("Unity 缺 CLI 抛 EngineError", False, "居然没抛")
        except EngineError as exc:
            msg = str(exc)
            print("      EngineError: " + msg.replace("\n", "\n                   "))
            check("Unity 错误信息含修复命令",
                  "setup_tools.py assetstudio" in msg)
            check("Unity 错误信息说明缺什么", "AssetStudio" in msg)

        uctx = make_ctx(make_fake_unreal(tmp / "unreal_case"),
                        tmp / "o2", tmp / "w2")
        try:
            unreal.extract(uctx)
            check("Unreal 缺 CLI 抛 EngineError", False, "居然没抛")
        except EngineError as exc:
            msg = str(exc)
            print("      EngineError: " + msg.replace("\n", "\n                   "))
            check("Unreal 错误信息含修复命令",
                  "setup_tools.py repak" in msg)
    finally:
        tool_paths.TOOLS_DIR = real_tools


# ---------------------------------------------------------------- 测试辅助
def _fake_result(argv, code=0, out=""):
    """构造一个喂给引擎的 LiveResult。

    引擎改成用 me.tools.stream.run_live 之后，返回值要带 .lines
    （unreal 的 count_entries 会数 res.lines），所以不能再直接用 proc.RunResult。
    """
    lines = [ln for ln in (out or "").splitlines() if ln.strip()]
    return stream.LiveResult([str(a) for a in argv], code, lines, 0.0)


# ---------------------------------------------------------------- D 假 CLI 跑全流程
def test_unity_with_fake_cli(tmp: Path):
    section("D1. Unity: 用假 CLI 跑完整 extract（验证两趟调用 + 收集）")
    called = []

    def fake_run(argv, cwd=None, timeout=0, console=False, **kw):
        called.append([str(a) for a in argv])
        # 模拟 AssetStudio：argv[1]=input argv[2]=output
        outd = Path(argv[2])
        types = argv[argv.index("--types") + 1:argv.index("--silent")]
        if "Texture2D" in types or "Sprite" in types:
            (outd / "Texture2D").mkdir(parents=True, exist_ok=True)
            (outd / "Texture2D" / "hero.png").write_bytes(b"\x89PNG fake")
            (outd / "Texture2D" / "foe.png").write_bytes(b"\x89PNG fake2")
            (outd / "Sprite").mkdir(parents=True, exist_ok=True)
            (outd / "Sprite" / "icon.png").write_bytes(b"\x89PNG fake3")
        if "AudioClip" in types:
            (outd / "AudioClip").mkdir(parents=True, exist_ok=True)
            (outd / "AudioClip" / "bgm.ogg").write_bytes(b"OggS fake")
        return _fake_result(argv)

    game = make_fake_unity(tmp / "unity_case", with_assets=False)
    out_root = tmp / "out_unity_fake"
    real_run = stream.run_live
    stream.run_live = fake_run
    try:
        ctx = make_ctx(game, out_root, tmp / "w_unity_fake")
        res = unity.extract(ctx)
    finally:
        stream.run_live = real_run

    check("调用了 2 次 AssetStudio", len(called) == 2, f"实际 {len(called)} 次")
    check("两次的输出目录不同",
          called[0][2] != called[1][2], f"{called[0][2]} / {called[1][2]}")
    check("两次都指向同一个 _Data 输入",
          called[0][1] == called[1][1] == str(game / "FakeUnityGame_Data"))
    check("两次都带 --game Normal",
          all(a[a.index("--game") + 1] == "Normal" for a in called))
    check("第一次导图片、第二次导音频",
          "Texture2D" in called[0] and "AudioClip" in called[1],
          f"{called[0][-3:]} / {called[1][-3:]}")
    check("写出 4 个文件（3 图 + 1 音频，.assets 被兜底跳过）",
          res.written == 4, f"written={res.written}")
    check("图片分类 3 个", ctx.writer.count("image") == 3,
          f"image={ctx.writer.count('image')}")
    check("音频分类 1 个", ctx.writer.count("audio") == 1,
          f"audio={ctx.writer.count('audio')}")
    check("不筛分类时图片/音频两趟都跑", len(called) == 2)

    section("D2. Unity: 只要图片时不该跑音频那趟")
    called.clear()
    game_img = make_fake_unity(tmp / "unity_case_img", with_assets=False)
    stream.run_live = fake_run
    try:
        ctx2 = make_ctx(game_img, tmp / "out_unity_img", tmp / "w_unity_img",
                        categories={"image"})
        res2 = unity.extract(ctx2)
    finally:
        stream.run_live = real_run
    check("只调用 1 次", len(called) == 1, f"实际 {len(called)} 次")
    check("跑的是图片趟", "Texture2D" in called[0], " ".join(called[0][-3:]))
    check("写出 3 个图片", res2.written == 3, f"written={res2.written}")

    section("D3. Unity: 两趟都 0 文件时给加密提示")
    def fake_run_empty(argv, cwd=None, timeout=0, console=False, **kw):
        Path(argv[2]).mkdir(parents=True, exist_ok=True)
        return _fake_result(argv)

    # 这个样本带 .assets（所以能定位到输入），但不带任何图片/音频；
    # 兜底收集又会跳过 .assets，所以 written 必然是 0，正好测这条分支
    game_empty = make_fake_unity(tmp / "unity_case_empty")
    stream.run_live = fake_run_empty
    try:
        ctx3 = make_ctx(game_empty, tmp / "out_unity_none", tmp / "w_unity_none")
        res3 = unity.extract(ctx3)
    finally:
        stream.run_live = real_run
    joined = "\n".join(res3.notes)
    check("written == 0", res3.written == 0)
    check("要求兜底 fallback", res3.fallback is True)
    check("提示可能是加密/特殊渠道", "加密" in joined and "特殊渠道" in joined)
    check("列出了 --game 候选值",
          "UnityCN" in joined and "GI_Pack" in joined and "FakeHeader" in joined)
    check("提示可以换 --types", "ME_UNITY_IMAGE_TYPES" in joined)
    check("提示退出码 0 不可信 / 给了输入路径线索", "输入路径" in joined)

    section("D3b. Unity: AssetStudio 一无所获时，散落资源兜底收集仍要工作")
    game_loose = make_fake_unity(tmp / "unity_case_loose",
                                 extra_loose=["bgm.ogg", "art.png"])
    stream.run_live = fake_run_empty
    try:
        ctx3b = make_ctx(game_loose, tmp / "out_unity_loose", tmp / "w_unity_loose")
        res3b = unity.extract(ctx3b)
    finally:
        stream.run_live = real_run
    stats = ctx3b.writer.summary()
    print(f"      classified: {stats}")
    # _Data 里有 2 个裸放资源 + 2 个 .assets（容器会被兜底跳过）
    check("兜底收走散落的 2 个资源", res3b.written == 2, f"written={res3b.written}")
    check("图片 1 个 + 音频 1 个",
          ctx3b.writer.count("image") == 1 and ctx3b.writer.count("audio") == 1,
          str(stats))
    check("无扩展名的 Unity 元数据没被当成资源收进来",
          "other" not in stats, str(stats))
    check("有收获时不该再报加密提示",
          not any("加密" in n for n in res3b.notes))


def test_unity_types_env(tmp: Path):
    section("D4. Unity: 环境变量覆盖 --types 与 --game")
    called = []

    def fake_run(argv, cwd=None, timeout=0, console=False, **kw):
        called.append([str(a) for a in argv])
        Path(argv[2]).mkdir(parents=True, exist_ok=True)
        return _fake_result(argv)

    game = make_fake_unity(tmp / "unity_case_env", with_assets=False)
    real_run, old = stream.run_live, {k: os.environ.get(k) for k in
                               ("ME_UNITY_GAME", "ME_UNITY_IMAGE_TYPES",
                                "ME_UNITY_AUDIO_TYPES")}
    stream.run_live = fake_run
    try:
        os.environ["ME_UNITY_GAME"] = "UnityCN"
        # 空串 = 不限定类型（全量导出）
        os.environ["ME_UNITY_IMAGE_TYPES"] = ""
        os.environ["ME_UNITY_AUDIO_TYPES"] = "AudioClip"
        ctx = make_ctx(game, tmp / "out_unity_env", tmp / "w_unity_env")
        unity.extract(ctx)
    finally:
        stream.run_live = real_run
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    check("--game 取自 ME_UNITY_GAME",
          called[0][called[0].index("--game") + 1] == "UnityCN")
    # 空的 ME_UNITY_IMAGE_TYPES 表示图片趟不限定类型，
    # 这一趟要么不跑（jobs 里只有 audio），要么跑但不带 --types；
    # 两种情况都不该出现 Texture2D/Sprite 字样。
    img_args = [a for a in called if "Texture2D" in a or "Sprite" in a]
    check("图片趟空串时完全不出现 --types Texture2D/Sprite",
          not img_args, str(img_args))
    check("音频趟带 AudioClip",
          len(called) == 1 and "AudioClip" in called[0], " ".join(called[-1]))


# ---------------------------------------------------------------- E 真 repak 往返
def test_unreal_roundtrip(tmp: Path):
    section("E1. Unreal: 真 repak 往返（pack 后用 extract 解回来）")
    repak = tool_paths.find("repak")
    if repak is None:
        check("repak 存在", False, "没找到 repak.exe，跳过往返测试")
        return
    print(f"      repak: {repak}")

    # 1) 造一个待打包的源目录，故意包含中文名和大文件
    src = tmp / "pak_src" / "Game" / "Content" / "Paks"
    (src / "sub").mkdir(parents=True)
    payloads = {
        "hello.txt": b"hello unreal pak\n",
        "sub/tiny.uasset": b"\x00\x01\x02fake uasset payload",
        "sub/tiny.uexp": b"fake uexp payload",
        "sub/tiny.ubulk": b"B" * 4096,
        "\u4e2d\u6587\u540d.txt": "中文内容测试\n".encode("utf-8"),
    }
    for rel, data in payloads.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    # 2) 真跑 repak pack
    pak_path = tmp / "built" / "FakeGame-WindowsNoEditor.pak"
    pak_path.parent.mkdir(parents=True, exist_ok=True)
    res = proc.run([str(repak), "pack", str(src), str(pak_path)], timeout=120)
    print(f"      pack 退出码={res.code}  out={res.out.strip()!r}  err={res.err.strip()!r}")
    check("repak pack 成功", res.ok and pak_path.exists(),
          f"size={pak_path.stat().st_size if pak_path.exists() else 0}")
    if not pak_path.exists():
        return
    # 实测：pak 的魔数 E1126F5A 不在文件开头（开头全是 0），
    # 它在索引/尾部结构里。项目 scanner/signatures.py 的 MAGIC_SIGS
    # 也正是用 bytes.fromhex("E1126F5A") 做「这个文件是 pak」的判据。
    blob = pak_path.read_bytes()
    magic = bytes.fromhex("E1126F5A")
    off = blob.find(magic)
    print(f"      魔数 E1126F5A 出现在偏移 {off}（文件共 {len(blob)} 字节）")
    check("产出的确实是合法 pak（含 pak 魔数 E1126F5A）", off >= 0,
          f"offset={off}")

    # 3) list 数条目
    n, err = unreal.count_entries(repak, pak_path, None)
    print(f"      repak list -> 条目数={n} {err}")
    check("list 数出 5 个条目", n == 5, f"n={n}")

    # 4) 用真的 unreal.extract 走完整流程
    #    目录布局要像游戏：<游戏>\\Content\\Paks\\xxx.pak
    game = tmp / "roundtrip_game"
    paks_dir = game / "Content" / "Paks"
    paks_dir.mkdir(parents=True)
    shutil.copy2(pak_path, paks_dir / pak_path.name)

    out_root = tmp / "out_unreal_rt"
    ctx = make_ctx(game, out_root, tmp / "w_unreal_rt")
    out = unreal.extract(ctx)
    print("      detail: " + out.detail)
    for nt in out.notes:
        print("      note: " + nt.replace("\n", "\n            "))

    check("extract 报出的文件数 == 5", out.written == 5, f"written={out.written}")
    check("notes 里有条目数统计", any("5 个条目" in n for n in out.notes))
    check("notes 里提醒需要 FModel/UE Viewer 转换",
          any("FModel" in n and "UE Viewer" in n for n in out.notes))
    # .txt 会被 kinds.classify 正常归到 text，.uasset/.uexp/.ubulk 才是 other：
    # 这说明「按扩展名照常收集」是对的，.uasset 这些确实没有专门分类。
    print(f"      分类统计: {ctx.writer.summary()}")
    check("引擎专有格式(.uasset/.uexp/.ubulk)都进「其他」",
          ctx.writer.count("other") == 3, str(ctx.writer.summary()))
    check(".txt 仍按扩展名归到「文本」", ctx.writer.count("text") == 2,
          str(ctx.writer.summary()))

    # 5) 逐字节比对往返内容（在所有分类目录下找，别只盯着 other/）
    ok_all, checked = True, []
    for rel, data in payloads.items():
        hit = list(out_root.rglob(Path(rel).name))
        if not hit:
            ok_all = False
            checked.append(f"{rel}: 没找到")
            continue
        got = hit[0].read_bytes()
        same = got == data
        ok_all = ok_all and same
        checked.append(f"{rel}: {'一致' if same else '不一致'}")
    print("      " + " | ".join(checked))
    check("5 个文件逐字节往返一致", ok_all)

    # 6) 加密 pak 不给 key：repak 会明确报错（用来验证我们的 notes 文案）
    enc = tmp / "enc_probe.pak"
    res_enc = proc.run([str(repak), "pack", str(src), str(enc),
                        "--version", "V11"], timeout=120)
    print(f"      顺带：pack --version V11 退出码={res_enc.code}")


def test_unreal_extract_with_fake_cli(tmp: Path):
    section("E2. Unreal: 用假 CLI 跑 extract（验证 AES key 传递 + IoStore 提示）")
    called = []

    def fake_run(argv, cwd=None, timeout=0, console=False, **kw):
        a = [str(x) for x in argv]
        called.append(a)
        if "list" in a:
            return _fake_result(argv, 0, "Game/A.uasset\nGame/B.uexp")
        # unpack：写到 --output 指定的目录
        outd = Path(a[a.index("--output") + 1])
        (outd / "Game").mkdir(parents=True, exist_ok=True)
        (outd / "Game" / "A.uasset").write_bytes(b"uasset")
        (outd / "Game" / "B.uexp").write_bytes(b"uexp")
        return _fake_result(argv)

    game = make_fake_unreal(tmp / "unreal_case")
    real_run = stream.run_live
    stream.run_live = fake_run
    old = os.environ.get("ME_AES_KEY")
    try:
        os.environ["ME_AES_KEY"] = "0123456789abcdef" * 4
        ctx = make_ctx(game, tmp / "out_unreal_fake", tmp / "w_unreal_fake")
        res = unreal.extract(ctx)
    finally:
        stream.run_live = real_run
        if old is None:
            os.environ.pop("ME_AES_KEY", None)
        else:
            os.environ["ME_AES_KEY"] = old

    print("      detail: " + res.detail)
    for nt in res.notes:
        print("      note: " + nt.replace("\n", "\n            "))

    check("2 个 pak 各跑了 list + unpack（共 4 次调用）",
          len(called) == 4, f"实际 {len(called)} 次")
    check("每次调用 --aes-key 都排在子命令之前",
          all(a.index("--aes-key") < a.index("list" if "list" in a else "unpack")
              for a in called))
    check("AES key 值透传正确",
          all(a[a.index("--aes-key") + 1] == "0123456789abcdef" * 4 for a in called))
    check("notes 记录了条目数", any("2 个条目" in n for n in res.notes),
          str([n for n in res.notes if "条目" in n]))
    check("写出 4 个文件（2 pak x 2）", res.written == 4, f"written={res.written}")
    check("notes 提示 IoStore 不支持",
          any(".utoc" in n and "repak 只认 .pak" in n for n in res.notes))
    check("notes 提示需要 FModel / UE Viewer",
          any("FModel" in n and "UE Viewer" in n for n in res.notes))
    check("uasset/uexp 归到其他分类", ctx.writer.count("other") == 4,
          str(ctx.writer.summary()))


# ---------------------------------------------------------------- F 真 CLI 冒烟
def test_real_assetstudio_smoke(tmp: Path):
    section("F1. 真 AssetStudio.CLI：验证「永远退出 0」这个坑")
    cli = tool_paths.find("assetstudio")
    if cli is None:
        check("assetstudio 存在", False, "没找到 CLI，跳过")
        return
    print(f"      cli: {cli}")

    # 喂一个空壳假 _Data：AssetStudio 认这是输入，但里面没有合法资源
    game = make_fake_unity(tmp / "unity_case")
    outd = tmp / "as_smoke_out"
    outd.mkdir(parents=True, exist_ok=True)
    argv = unity.build_argv(cli, game / "FakeUnityGame_Data", outd,
                            "Normal", "Texture2D Sprite AudioClip")
    print("      实际执行的命令: " + " ".join(argv))
    res = proc.run(argv, timeout=600, console=False)
    print(f"      退出码={res.code}")
    print(f"      stdout 前 3 行: {res.out.strip().splitlines()[:3]}")
    print(f"      stderr 前 3 行: {res.err.strip().splitlines()[:3]}")

    check("退出码是 0（说明不能靠退出码判成败）", res.code == 0, f"code={res.code}")
    check("输出目录里确实没有文件（靠文件数判成败）",
          not unity._has_files(outd))


def test_real_repak_cli(tmp: Path):
    section("F2. 真 repak：--help 与 .utoc 报错文案")
    repak = tool_paths.find("repak")
    if repak is None:
        check("repak 存在", False, "跳过")
        return
    h = proc.run([str(repak), "--help"], timeout=60)
    print("      " + "\n      ".join(h.out.strip().splitlines()[:8]))
    check("help 里有 unpack / pack / list", all(
        k in h.out for k in ("unpack", "pack", "list")))
    check("help 里没有任何 utoc/iostore 字样（=不支持 IoStore）",
          "utoc" not in h.out.lower() and "iostore" not in h.out.lower())

    fake_utoc = tmp / "fake.utoc"
    fake_utoc.write_bytes(bytes(range(256)))
    r = proc.run([str(repak), "info", str(fake_utoc)], timeout=60)
    blob = (r.out or "") + (r.err or "")
    print(f"      utoc 退出码={r.code}；末尾: {blob.strip().splitlines()[-1] if blob.strip() else ''}")
    check("repak 读不了 .utoc（退出码非 0）", r.code != 0, f"code={r.code}")
    check("报的是 version unsupported",
          "version unsupported" in blob, blob.strip()[:120])


# ---------------------------------------------------------------- main
def main():
    print("项目根: " + ROOT)
    print("python : " + sys.version.split()[0] + "  " + sys.executable)
    base = Path(tempfile.mkdtemp(prefix="me_selftest_"))
    print("临时目录: " + str(base))
    try:
        test_unity_locate(base)
        test_unreal_locate(base)
        test_argv()
        test_missing_cli(base)
        test_unity_with_fake_cli(base)
        test_unity_types_env(base)
        test_unreal_roundtrip(base)
        test_unreal_extract_with_fake_cli(base)
        test_real_assetstudio_smoke(base)
        test_real_repak_cli(base)
    finally:
        print("\n(临时目录保留供检查: " + str(base) + ")")

    section("结果")
    print(f"  PASS {len(PASS)}  FAIL {len(FAIL)}")
    for f in FAIL:
        print("   FAILED: " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
