# -*- coding: utf-8 -*-
"""MFA 前段读写自测（V0.19 起）。

三段：

  1. CString 编码：空串 / ASCII / 中文 / 长串，读写对称；
  2. 合成前段：写出来再读回来，字段必须一致（不依赖任何外部文件）；
  3. 真样本：本机有参考 MFA 就逐字节回环（没有就跳过，不失败）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.formats.ctf25 import mfa  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []
SKIPPED = 0

SAMPLES = [
    Path(r"C:\拆包教程素材\Clickteam MFA 样本\Template.mfa"),
    Path(r"C:\拆包教程素材\Clickteam MFA 样本\Platformer Engine.mfa"),
]


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


def test_cstring():
    section("1. CString 编码")
    texts = ["", "Template", "拆包工具", "a" * 300]
    w = mfa.Writer()
    for t in texts:
        w.cstring(t)
    buf = w.getvalue()

    r = mfa.Reader(buf)
    got = [r.cstring() for _ in texts]
    check(got == texts, "读写对称", f"{got[:2]!r}")
    check(buf[:4] == b"\x00\x00\x00\x80", "空串就是 00 00 00 80",
          buf[:4].hex(" "))

    # 单个 ASCII 串：长度字段 = 0x80000000 | 8
    one = mfa.Writer()
    one.cstring("Template")
    head = one.getvalue()[:4]
    check(head == b"\x08\x00\x00\x80", "ASCII 长度带 Unicode 位",
          head.hex(" "))

    # 手工构造：UTF-16LE "AB" = 41 00 42 00
    hand = b"\x02\x00\x00\x80" + "AB".encode("utf-16-le")
    check(mfa.Reader(hand).cstring() == "AB", "手写串能读")

    # 长度位为 0（ANSI）时按单字节读
    ansi = b"\x02\x00\x00\x00" + b"AB"
    check(mfa.Reader(ansi).cstring() == "AB", "ANSI（无 Unicode 位）读得出来")


def test_synthetic():
    section("2. 合成前段回环")
    pre = mfa.MfaPreamble(
        version=7, editor_version=3, editor_build=292, language=1033,
        app_name="合成工程", description="",
        editor_path=r"C:\tmp\合成工程.mfa",
        thumbnail=b"\x89PNG" + b"\x00" * 32,
        banks_raw=mfa.TAG_FONTS + b"\x00\x00\x00\x00"
                  + mfa.TAG_SOUNDS + b"\x00\x00\x00\x00"
                  + mfa.TAG_MUSIC + b"\x00\x00\x00\x00"
                  + mfa.TAG_ICONS + b"\x00\x00\x00\x00"
                  + mfa.TAG_IMAGES + b"\x00\x00\x00\x00",
    )
    blob = mfa.build_preamble(pre)
    back = mfa.read_preamble(blob)
    check(back.app_name == "合成工程", "工程名回读", back.app_name)
    check(back.editor_path == pre.editor_path, "编辑器路径回读")
    check(back.thumbnail == pre.thumbnail, "缩略图回读")
    check(back.banks_raw == pre.banks_raw, "库数据回读")
    check(mfa.build_preamble(back) == blob, "再写一遍仍一致")

    # 库标记不对时必须报错，不能当没事
    bad = bytearray(blob)
    idx = blob.find(mfa.TAG_FONTS)
    bad[idx:idx + 4] = b"XXXX"
    try:
        mfa.read_preamble(bytes(bad))
        check(False, "库标记错要报错")
    except mfa.MfaError:
        check(True, "库标记错要报错")

    # 截断也要报错
    try:
        mfa.read_preamble(blob[:20])
        check(False, "截断文件要报错")
    except mfa.MfaError:
        check(True, "截断文件要报错")


def test_samples():
    global SKIPPED
    section("3. 真样本（本机有就验，没有就跳过）")
    if not any(p.exists() for p in SAMPLES):
        SKIPPED += 1
        print("  [skip] 参考 MFA 样本不在本机")
        return

    for path in SAMPLES:
        if not path.exists():
            print(f"  [skip] {path.name}")
            continue
        raw = path.read_bytes()
        pre = mfa.read_file(path)
        check(pre.version in (6, 7), f"{path.name} 版本 {pre.version}")
        check(pre.editor_build == 292, f"{path.name} 编辑器构建 {pre.editor_build}")
        check(pre.language == 1033, f"{path.name} 语言 {pre.language}")
        check(pre.app_name != "", f"{path.name} 工程名非空", pre.app_name)
        check(len(pre.thumbnail) > 0, f"{path.name} 有缩略图（{len(pre.thumbnail)} B）")
        check(mfa.build_preamble(pre) == raw, f"{path.name} 逐字节回环")


def test_banks():
    """四个资源库：字体/音频/音乐按已对齐的布局读，图像只读库头。"""
    section("4. 资源库（字体 / 音频 / 音乐 / 图标与图像的库头）")
    present = [p for p in SAMPLES if p.exists()]
    if not present:
        global SKIPPED
        SKIPPED += 1
        print("  [skip] 参考 MFA 样本不在本机")
        return

    for path in present:
        raw = path.read_bytes()
        pre = mfa.read_preamble(raw)
        banks = mfa.read_banks(raw, len(raw) - len(pre.banks_raw))
        tag = path.name

        check(banks.icons is not None and banks.images is not None, f"{tag} 两个图像库都读到")
        if not (banks.icons and banks.images):
            continue

        check(banks.icons.graphic_mode == 4, f"{tag} 图标库 graphicMode=4",
              banks.icons.graphic_mode)
        check(banks.icons.palette_entries == 256, f"{tag} 调色板 256 条",
              banks.icons.palette_entries)
        check(banks.icons.count > 0, f"{tag} 图标数 {banks.icons.count}")
        check(banks.images.count > 0, f"{tag} 图像数 {banks.images.count}")
        check(len(banks.icons.entries) == banks.icons.count,
              f"{tag} 图标条目全解析（{banks.icons.count} 条）")
        check(len(banks.images.entries) == banks.images.count,
              f"{tag} 图像条目全解析（{banks.images.count} 条）")

        for f in banks.fonts:
            check(f.name != "", f"{tag} 字体名 {f.name!r}")
            check(f.size_px > 0, f"{tag} 字号 {f.size_px}")
            check(f.weight >= 0, f"{tag} 字重 {f.weight}")

    # Platformer 的那条字体是已知的，钉死
    pf = SAMPLES[1]
    if pf.exists():
        raw = pf.read_bytes()
        pre = mfa.read_preamble(raw)
        banks = mfa.read_banks(raw, len(raw) - len(pre.banks_raw))
        if banks.fonts:
            f = banks.fonts[0]
            check(f.name == "Free Pixel", "Platformer 字体名 = Free Pixel", f.name)
            check(f.size_px == 27, "Platformer 字号 = 27", f.size_px)
            check(f.weight == 400, "Platformer 字重 = 400", f.weight)
            check(f.data == b"", "Platformer 字体没内嵌 TTF")

    # 标记被改坏要报错
    raw = bytearray(present[0].read_bytes())
    pre = mfa.read_preamble(bytes(raw))
    start = len(raw) - len(pre.banks_raw)
    i = raw.find(mfa.TAG_SOUNDS, start)
    raw[i:i + 4] = b"ZZZZ"
    try:
        mfa.read_banks(bytes(raw), start)
        check(False, "音频库标记坏掉要报错")
    except mfa.MfaError:
        check(True, "音频库标记坏掉要报错")


def test_write_banks():
    """库的写入：读出来的东西原样写回去，必须和盘上的字节一模一样。"""
    section("5. 库的写入（前段 + 五个库 逐字节重建）")
    present = [p for p in SAMPLES if p.exists()]
    if not present:
        global SKIPPED
        SKIPPED += 1
        print("  [skip] 参考 MFA 样本不在本机")
        return

    for path in present:
        raw = path.read_bytes()
        pre, banks = mfa.read_project_prefix(raw)
        rebuilt = mfa.build_project_prefix(pre, banks)
        orig = raw[:banks.app_offset]
        check(rebuilt == orig, f"{path.name} 前 {banks.app_offset:,} B 逐字节重建",
              f"{len(rebuilt)} vs {len(orig)}")

        # 图像条目：尺寸 / 通道 / 压缩前后长度都对得上
        if banks.images and banks.images.entries:
            img = banks.images.entries[0]
            check(img.width > 0 and img.height > 0, f"{path.name} 图像尺寸 {img.width}x{img.height}")
            check(img.channels in (1, 2, 3, 4), f"{path.name} 通道数 {img.channels}",
                  img.channels)
            check(len(img.payload) == img.data_size - 4, f"{path.name} 压缩长度自洽")
            check(len(img.pixels) == img.decl_size, f"{path.name} 解压长度自洽")

        # 库计数与条目数一致
        if banks.icons:
            check(len(banks.icons.entries) == banks.icons.count,
                  f"{path.name} 图标条目数 = 头里的 {banks.icons.count}")
        if banks.images:
            check(len(banks.images.entries) == banks.images.count,
                  f"{path.name} 图像条目数 = 头里的 {banks.images.count}")

    # Template 的 #5 有多余字节（50x50 却多 100 字节），必须如实记下来而不是当解析失败
    tpl = SAMPLES[0]
    if tpl.exists():
        _, banks = mfa.read_project_prefix(tpl.read_bytes())
        slack = [(i, e.slack) for i, e in enumerate(banks.images.entries) if e.slack]
        check(slack == [(5, 100)], "Template 图像 #5 的 +100 B 记为 slack", str(slack))


def test_app_header():
    """应用设置段：字段值 + 逐字节回写。"""
    section("6. 应用设置段")
    present = [p for p in SAMPLES if p.exists()]
    if not present:
        global SKIPPED
        SKIPPED += 1
        print("  [skip] 参考 MFA 样本不在本机")
        return

    for path in present:
        raw = path.read_bytes()
        pre, banks = mfa.read_project_prefix(raw)
        app, end = mfa.read_app_header(raw, banks.app_offset)
        tag = path.name

        check(app.width == 640 and app.height == 360, f"{tag} 窗口 640x360",
              f"{app.width}x{app.height}")
        check(app.frame_rate == 60, f"{tag} 帧率 60", app.frame_rate)
        check(app.author == "Ambratolm", f"{tag} 作者", app.author)
        check(len(app.players) == 4, f"{tag} 4 个玩家", len(app.players))
        if app.players:
            p0 = app.players[0]
            check(p0.type == 5, f"{tag} 玩家类型 5", p0.type)
            check(len(p0.keys) == 16, f"{tag} 每人 16 个键", len(p0.keys))
            check(p0.keys[:4] == [38, 40, 37, 39], f"{tag} 上下左右键码",
                  str(p0.keys[:4]))
        check(app.binary_count == 0, f"{tag} 没有二进制文件", app.binary_count)

        rebuilt = mfa.build_app_header(app)
        orig = raw[banks.app_offset:end]
        check(rebuilt == orig, f"{tag} 应用设置段逐字节回写（{len(orig)} B）",
              f"{len(rebuilt)} vs {len(orig)}")

    # 字段被改坏：窗口尺寸荒唐时要报错
    raw = bytearray(present[0].read_bytes())
    pre, banks = mfa.read_project_prefix(bytes(raw))
    app, end = mfa.read_app_header(bytes(raw), banks.app_offset)
    app.width = 999999
    app.height = 999999
    try:
        mfa.read_app_header(mfa.build_app_header(app), 0)
        check(False, "窗口尺寸荒唐要报错")
    except mfa.MfaError:
        check(True, "窗口尺寸荒唐要报错")


def test_layout():
    section("7. 整份工程进度尺")
    want = {  # 样本 → (已解析占比下限, 上限)
        "Template.mfa": (98.0, 98.2),
        "Platformer Engine.mfa": (89.3, 89.5),
    }
    try:
        mfa.read_layout(b"")
        check(False, "空文件要报错")
    except mfa.MfaError:
        check(True, "空文件要报错")

    present = [p for p in SAMPLES if p.is_file()]
    if not present:
        print("  [skip] 参考 MFA 不在本机")
        return
    for p in present:
        raw = p.read_bytes()
        lay = mfa.read_layout(raw)
        check(lay.file_size == len(raw), f"{p.name} 文件长度 {len(raw)} B")
        check(lay.banks_offset > 0 and lay.banks_offset < lay.app_offset
              < lay.tail_offset <= lay.file_size,
              f"{p.name} 段偏移递增（库 0x{lay.banks_offset:X} < "
              f"应用 0x{lay.app_offset:X} < 尾巴 0x{lay.tail_offset:X}）")
        check(mfa.build_project_prefix(lay.preamble, lay.banks)
              == raw[:lay.app_offset], f"{p.name} 前段 + 库逐字节回写")
        check(mfa.build_app_header(lay.app)
              == raw[lay.app_offset:lay.tail_offset],
              f"{p.name} 应用设置段逐字节回写（{lay.tail_size} B 尾巴未解析）")
        lo, hi = want.get(p.name, (0.0, 100.0))
        check(lo <= lay.coverage <= hi,
              f"{p.name} 已解析占比 {lay.coverage:.1f}%", f"期望 {lo}~{hi}")
        check("尾巴" in mfa.describe_layout(lay), f"{p.name} 概况串")


def test_menu():
    section("8. 菜单区")
    try:
        mfa.read_menu_region(b"\xff\xff\xff\xff", 0)
        check(False, "菜单块长度越界要报错")
    except mfa.MfaError:
        check(True, "菜单块长度越界要报错")

    m = mfa.MfaMenuRegion(menu_size=4, menu_raw=b"\x01\x02\x03\x04",
                          window_menu=2, menu_images=[(10, 3), (11, 4)])
    check(mfa.read_menu_region(mfa.build_menu_region(m), 0)[0] == m,
          "合成菜单区回环")

    present = [p for p in SAMPLES if p.is_file()]
    if not present:
        print("  [skip] 参考 MFA 不在本机")
        return
    for p in present:
        raw = p.read_bytes()
        lay = mfa.read_layout(raw)
        m, end = mfa.read_menu_region(raw, lay.tail_offset)
        check(m.menu_size == 550, f"{p.name} 菜单块 550 B", f"{m.menu_size}")
        check(m.window_menu == 0, f"{p.name} 窗口菜单 #0", f"{m.window_menu}")
        check(len(m.menu_images) == 1, f"{p.name} 菜单图形 1 项",
              f"{len(m.menu_images)}")
        check(mfa.build_menu_region(m) == raw[lay.tail_offset:end],
              f"{p.name} 菜单区逐字节回写（{end - lay.tail_offset} B）")
        check(b"&\x00F\x00i\x00l\x00e\x00" in m.menu_raw,
              f"{p.name} 菜单块里能找到 \"&File\"")
        check(end > lay.tail_offset and end <= lay.file_size,
              f"{p.name} 菜单区结束偏移 0x{end:X}")


def test_globals():
    section("9. 全局量区 + 帧偏移表")
    g = mfa.MfaGlobals(graphic_mode=4, icon_handles=[0, 1, 2],
                       frame_offsets=[24, 32], return_offset=40)
    check(mfa.read_globals(mfa.build_globals(g), 0)[0] == g, "合成全局量区回环")
    check(mfa.build_globals(mfa.read_globals(mfa.build_globals(g), 0)[0])
          == mfa.build_globals(g), "合成全局量区二次回写一致")
    for bad, what in ((mfa.MfaGlobals(values=[("a", 1)]), "全局值非空要报错"),
                      (mfa.MfaGlobals(strings=[("a", "b")]), "全局字符串非空要报错")):
        try:
            mfa.build_globals(bad)
            check(False, what)
        except mfa.MfaError:
            check(True, what)
    # 越界在读的时候拦：写侧不校验偏移，读侧必须挡住
    try:
        mfa.read_globals(
            mfa.build_globals(mfa.MfaGlobals(frame_offsets=[999999],
                                             return_offset=8)), 0)
        check(False, "帧偏移越界要报错")
    except mfa.MfaError:
        check(True, "帧偏移越界要报错")

    present = [p for p in SAMPLES if p.is_file()]
    if not present:
        print("  [skip] 参考 MFA 不在本机")
        return
    want = {  # 文件名 → (帧偏移, 回程偏移)
        "Template.mfa": (0x2E957, 0x2EEC2),
        "Platformer Engine.mfa": (0x5C88D, 0x66E57),
    }
    for p in present:
        raw = p.read_bytes()
        lay = mfa.read_layout(raw)
        _, menu_end = mfa.read_menu_region(raw, lay.tail_offset)
        g, end = mfa.read_globals(raw, menu_end)
        want_off, want_ret = want.get(p.name, (0, 0))
        check(g.graphic_mode == lay.banks.images.graphic_mode,
              f"{p.name} 图形模式 {g.graphic_mode} 与图像库一致")
        check(not g.values and not g.strings and not g.events_raw,
              f"{p.name} 全局值/字符串/事件都为空")
        check(g.icon_handles == list(range(9)), f"{p.name} 图标句柄 0~8",
              f"{g.icon_handles}")
        check(g.icon_handles[-1] < lay.banks.icons.count,
              f"{p.name} 句柄落在图标库 {lay.banks.icons.count} 条之内")
        check(g.frame_offsets == [want_off],
              f"{p.name} 帧偏移 {hex(want_off)}", f"{[hex(o) for o in g.frame_offsets]}")
        check(g.return_offset == want_ret,
              f"{p.name} 回程偏移 {hex(want_ret)}", hex(g.return_offset))
        check(mfa.build_globals(g) == raw[menu_end:end],
              f"{p.name} 全局量区逐字节回写（{end - menu_end} B）")
        check(end == g.frame_offsets[0], f"{p.name} 段尾正好是第一帧起点")
        check(raw[g.frame_offsets[0]:g.frame_offsets[0] + 4] == b"\x00" * 4,
              f"{p.name} 第一帧前 4 字节为 0（两份样本一致）")


def test_chunks():
    section("10. 对象 chunk 列表")
    cl = mfa.MfaChunkList(chunks=[mfa.MfaChunk(chunk_id=0x30, size=4,
                                               data=b"\x01\x00\x00\x00")],
                          terminated=True)
    buf = mfa.build_chunk_list(cl)
    check(buf == b"\x30\x04\x00\x00\x00\x01\x00\x00\x00\x00", "合成 chunk 列表字节")
    got = mfa.read_chunk_list(buf, 0)
    check([(c.chunk_id, c.data) for c in got.chunks]
          == [(0x30, b"\x01\x00\x00\x00")], "合成 chunk 列表回环")
    check(mfa.build_chunk_list(got) == buf, "合成 chunk 列表逐字节回写")
    check(got.terminated and got.end == len(buf), "收尾字节只占一字节")
    for bad, what in ((b"\x30\x99\x00\x00\x00", "长度越界要报错"),
                      (b"\x30\x04", "长度字段越界要报错")):
        try:
            mfa.read_chunk_list(bad, 0)
            check(False, what)
        except mfa.MfaError:
            check(True, what)

    present = [p for p in SAMPLES if p.is_file()]
    if not present:
        print("  [skip] 参考 MFA 不在本机")
        return
    seqs = {}
    for p in present:
        raw = p.read_bytes()
        lay = mfa.read_layout(raw)
        _, me = mfa.read_menu_region(raw, lay.tail_offset)
        g, _ = mfa.read_globals(raw, me)
        cl = mfa.read_chunk_list(raw, g.return_offset)
        seqs[p.name] = [c.chunk_id for c in cl.chunks]
        check(len(cl.chunks) == 47, f"{p.name} 47 条 chunk",
              f"{len(cl.chunks)}")
        check(cl.chunks[0].chunk_id == 0x30 and cl.chunks[0].size == 4,
              f"{p.name} 首条是 0x30 / 4 B")
        check(cl.terminated, f"{p.name} 收尾字节在 0x{cl.end - 1:X}")
        check(cl.end == len(raw), f"{p.name} chunk 列表正好收到文件尾",
              f"0x{cl.end:X} vs 0x{len(raw):X}")
        check(mfa.build_chunk_list(cl) == raw[g.return_offset:cl.end],
              f"{p.name} chunk 列表逐字节回写")
    if len(seqs) == 2:
        a, b = list(seqs.values())
        check(a == b, "两份样本 chunk id 序列一致")


def test_project():
    section("11. 整份工程重建")
    present = [p for p in SAMPLES if p.is_file()]
    if not present:
        print("  [skip] 参考 MFA 不在本机")
        return
    for p in present:
        raw = p.read_bytes()
        pr = mfa.read_project(raw)
        check(mfa.build_project(pr) == raw,
              f"{p.name} 整份 MFA 逐字节重建（{len(raw)} B）",
              f"{len(mfa.build_project(pr))} vs {len(raw)}")
        check(pr.size == len(raw), f"{p.name} 记录长度等于文件长度")
        check(pr.globals.return_offset > pr.globals.frame_offsets[0],
              f"{p.name} 帧体 {pr.frame_bytes} B 落在帧区之内")
        check(len(pr.chunks.chunks) == 47, f"{p.name} 47 条对象 chunk")
        check("chunk" in mfa.describe_project(pr), f"{p.name} 概况串")
        # 段首尾相接：前段+库+菜单+全局量之后就是第一帧
        head = (mfa.build_project_prefix(pr.layout.preamble, pr.layout.banks)
                + mfa.build_app_header(pr.layout.app)
                + mfa.build_menu_region(pr.menu) + mfa.build_globals(pr.globals))
        check(len(head) == pr.globals.frame_offsets[0],
              f"{p.name} 头三段长度 {len(head)} 正好等于帧偏移",
              f"{len(head)} vs {pr.globals.frame_offsets[0]}")
        check(head == raw[:len(head)], f"{p.name} 头三段逐字节一致")


def test_frames():
    section("12. 帧头部")
    try:
        mfa.read_frame(b"\x00\x00\x00", 0, 3)
        check(False, "帧头截断要报错")
    except mfa.MfaError:
        check(True, "帧头截断要报错")
    f = mfa.MfaFrame(handle=3, name="帧 1", width=320, height=240,
                     background=b"\x01\x02\x03\x00", flags=0x104,
                     max_objects=1000, password="", unk_string=b"\x01\x02",
                     last_x=160, last_y=120,
                     palette=[b"\x00" * 4] * 256, stamp_handle=2,
                     active_layer=1, rest_raw=b"\xAA\xBB")
    reb = mfa.read_frame(mfa.build_frame(f), 0, len(mfa.build_frame(f)))
    check(reb == f, "合成帧头回环（含中文帧名 / 256 色调色板）")
    check(mfa.build_frame(reb) == mfa.build_frame(f), "合成帧头逐字节回写")

    present = [p for p in SAMPLES if p.is_file()]
    if not present:
        print("  [skip] 参考 MFA 不在本机")
        return
    want = {
        "Template.mfa": ("Frame 1", 640, 360, b"\xc0\xc0\xc0\x00", 320, 180, 12),
        "Platformer Engine.mfa": ("Level", 1280, 360, b"\x20\x20\x20\x00",
                                  342, 219, 14),
    }
    for p in present:
        raw = p.read_bytes()
        pr = mfa.read_project(raw)
        f = pr.frames[0]
        name, w, h, bg, lx, ly, stamp = want.get(
            p.name, ("", 0, 0, b"\x00" * 4, 0, 0, 0))
        check(f.name == name, f"{p.name} 帧名 {name!r}", repr(f.name))
        check((f.width, f.height) == (w, h), f"{p.name} 帧尺寸 {w}x{h}",
              f"{f.width}x{f.height}")
        check(f.background == bg, f"{p.name} 背景色 {bg.hex(' ')}")
        check((f.last_x, f.last_y) == (lx, ly),
              f"{p.name} 最后查看 {lx},{ly}", f"{f.last_x},{f.last_y}")
        check(len(f.palette) == 256, f"{p.name} 调色板 256 色",
              f"{len(f.palette)}")
        check(f.stamp_handle == stamp, f"{p.name} 图章句柄 {stamp}",
              f"{f.stamp_handle}")
        check(f.flags == 0x104, f"{p.name} 帧 flags 0x104", hex(f.flags))
        check(f.max_objects == 1000, f"{p.name} 最大对象 1000")
        check(mfa.build_frame(f) ==
              raw[pr.globals.frame_offsets[0]:pr.globals.return_offset],
              f"{p.name} 帧逐字节回写（{len(mfa.build_frame(f))} B）")
        check("帧" in mfa.describe_frame(f), f"{p.name} 帧概况串")
        if p.name == "Template.mfa":
            check(len(f.layers) == 1 and f.layers[0].flags == 1
                  and f.object_count == 0,
                  "Template 1 层 / flags 1 / 0 对象",
                  f"{len(f.layers)} {f.layers[0].flags if f.layers else '-'} "
                  f"{f.object_count}")
        elif p.name == "Platformer Engine.mfa":
            check(len(f.layers) == 2
                  and [ly.flags for ly in f.layers] == [2, 1]
                  and f.object_count == 12,
                  "Platformer 2 层 / flags 2,1 / 12 对象",
                  f"{len(f.layers)} {[ly.flags for ly in f.layers]} "
                  f"{f.object_count}")
            check(abs(f.layers[0].scale_x - 1.0) < 1e-6, "图层缩放读成 1.0",
                  f"{f.layers[0].scale_x}")
        check(f.fade_in == 0 and f.fade_out == 0, f"{p.name} 转场标志为 0")
        if p.name == "Platformer Engine.mfa":
            names = mfa.extract_strings(f.rest_raw)
            check("Enemy" in names, "帧体里捞到对象名 Enemy", f"{names[:4]}")
            check("Jump Strength" in names,
                  "帧体里捞到可改变量名 Jump Strength", f"{names[:6]}")
            check(len(names) >= 8, f"帧体里一共捞到 {len(names)} 个名字")


def test_frame_chunks():
    section("13. 帧尾 chunk 列表与虚拟矩形")
    check(mfa.frame_chunks(b"") is None, "空帧体没有 chunk 列表")
    check(mfa.frame_rect(b"") is None, "空帧体没有虚拟矩形")
    try:
        mfa.set_frame_rect(b"", (0, 0, 1, 1))
        check(False, "没有 chunk 列表时写矩形要报错")
    except mfa.MfaError:
        check(True, "没有 chunk 列表时写矩形要报错")

    want = {
        "Template.mfa": ((0, 0, 640, 360), 12),
        "Platformer Engine.mfa": ((0, 0, 1280, 360), 12),
    }
    present = [p for p in SAMPLES if p.is_file()]
    if not present:
        print("  [skip] 参考 MFA 不在本机")
        return
    for p in present:
        pr = mfa.read_project(p.read_bytes())
        rest = pr.frames[0].rest_raw
        chunks = mfa.frame_chunks(rest)
        rect, count = want.get(p.name, (None, 0))
        check(chunks is not None and len(chunks) == count,
              f"{p.name} 帧尾 {count} 个 chunk",
              f"{len(chunks) if chunks else None}")
        if not chunks:
            continue
        check(chunks[0][0] == 0x21 and chunks[0][2] == 16,
              f"{p.name} 第一条是 16 B 的虚拟矩形", hex(chunks[0][0]))
        check(mfa.frame_rect(rest) == rect, f"{p.name} 虚拟矩形 {rect}",
              str(mfa.frame_rect(rest)))
        patched = mfa.set_frame_rect(rest, (1, 2, 640, 480))
        check(len(patched) == len(rest), "改矩形不改长度")
        check(mfa.frame_rect(patched) == (1, 2, 640, 480), "改完读回来是新值",
              str(mfa.frame_rect(patched)))
        check(mfa.set_frame_rect(patched, rect) == rest, "改回原值逐字节一致")


def test_frame_objects():
    section("14. 帧内对象列表解析")
    present = [p for p in SAMPLES if p.is_file()]
    if not present:
        print("  [skip] 参考 MFA 不在本机")
        return
    want = {
        "Template.mfa": ([], 0, 0),
        "Platformer Engine.mfa": (
            ["Enemy", "Actor", "~ Ground Block", "Obstacle", "Platformer Engine",
             "_ Debug Text", "~ Ground Slope Left", "~ Ground Slope Right",
             "~ Corridor", "~ Room", "Actor Sprite", "Fan"], 8, 82),
    }
    for p in present:
        raw = p.read_bytes()
        pr = mfa.read_project(raw)
        f = pr.frames[0]
        names, folders_want, instances_want = want.get(p.name, ([], 0, 0))
        got = mfa.parse_frame_objects(f.rest_raw, f.object_count)
        check([o["name"] for o in got["objects"]] == names,
              f"{p.name} 对象名逐个对上（{len(names)} 个）",
              f"{[o['name'] for o in got['objects']][:3]}")
        check(len(got["folders"]) == folders_want,
              f"{p.name} 文件夹 {folders_want} 个", str(len(got["folders"])))
        check(len(got["instances"]) == instances_want,
              f"{p.name} 实例 {instances_want} 条", str(len(got["instances"])))
        chunks = mfa.frame_chunks(f.rest_raw)
        check(got["end"] <= chunks[0][1],
              f"{p.name} 解析终点在帧尾 chunk 列表之前",
              f"0x{got['end']:X} vs 0x{chunks[0][1]:X}")
        if p.name == "Platformer Engine.mfa":
            tail = f.rest_raw[got["end"]:got["end"] + 4]
            check(tail == b"\x06\x04\x00\x00",
                  "对象/文件夹/实例之后紧接着是事件段头", tail.hex(" "))


def test_validate():
    section("13. 结构体检")
    present = [p for p in SAMPLES if p.is_file()]
    for bad, what in ((b"", "空文件要报问题"),
                      (b"NOPE" + b"\x00" * 16, "魔数不对要报问题"),
                      (b"MFU2\x06\x00\x00\x00" + b"\x00" * 8, "截断要报解析失败")):
        issues = mfa.validate(bad)
        check(bool(issues), what, f"{issues[:1]}")
    if not present:
        print("  [skip] 参考 MFA 不在本机")
        return
    for p in present:
        raw = p.read_bytes()
        issues = mfa.validate(raw)
        check(issues == [], f"{p.name} 两份真样本体检零问题", f"{issues[:3]}")
        # 直接把图像库头里的条数改大 1：解析会当场对不上，应被抓出来
        pr = mfa.read_project(raw)
        bad = bytearray(raw)
        off = pr.layout.banks.images.entries_offset - 4
        bad[off] = (bad[off] + 1) & 0xFF
        issues = mfa.validate(bytes(bad))
        check(bool(issues), f"{p.name} 条数对不上要被抓出来", f"{issues[:2]}")
        # 改坏帧名：应报「第 1 帧没有名字」
        pr2 = mfa.read_project(raw)
        pr2.frames[0].name = ""
        issues = mfa.validate(mfa.build_project(pr2))
        check(any("没有名字" in s for s in issues),
              f"{p.name} 帧名为空要被抓住", f"{issues[:2]}")


def main() -> int:
    print("=" * 66)
    print("  MFA 前段与资源库自测")
    print("=" * 66)
    test_cstring()
    test_synthetic()
    test_samples()
    test_banks()
    test_write_banks()
    test_app_header()
    test_layout()
    test_menu()
    test_globals()
    test_chunks()
    test_project()
    test_frames()
    test_frame_chunks()
    test_frame_objects()
    test_validate()

    print("\n" + "=" * 66)
    print(f"  PASS {PASS}  FAIL {FAIL}  SKIP {SKIPPED}")
    print("=" * 66)
    if FAILURES:
        print("\n失败项:")
        for f in FAILURES:
            print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
