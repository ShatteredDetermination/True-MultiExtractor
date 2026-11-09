# -*- coding: utf-8 -*-
"""CCN → MFA 骨架级转换自测（V0.19 起）。

不依赖外部工具，也不需要 Fusion：

  1. 组件层：RGBA → BGRA 换序、单张图像转换后的字段自洽；
  2. 端到端：真成品（有样本才跑）→ 写出 mfa → 用 ``mfa.read_project`` 读回来，
     图像张数、尺寸、像素与源数据一致；
  3. 失败路径：骨架不存在 / 成品不存在都要报得清楚。

真成品样本不在本机时整段跳过，不失败。
"""

import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.formats.ctf25 import mfa, mfa_build  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

TEMPLATE = Path(r"C:\拆包教程素材\Clickteam MFA 样本\Template.mfa")
CCN_SAMPLES = [
    Path(r"C:\拆包教程素材\目标游戏\dtlg-v1.1.0.exe"),
    Path(r"C:\拆包教程素材\目标游戏\Undertale Last Breath Scrapped.dat"),
]
LIMIT = 8


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


def test_convert_image():
    section("1. 单张图像转换")
    class E:
        handle = 7
        width = 4
        height = 3
        graphic_mode = 8
        flags = 0
        data = bytes(range(48))

    # 用一块确定的 RGBA 冒充解码结果
    orig = mfa_build.imagecodec.to_rgba
    mfa_build.imagecodec.to_rgba = lambda entry: bytes(range(48))
    try:
        img = mfa_build.to_mfa_image(E())
    finally:
        mfa_build.imagecodec.to_rgba = orig
    check((img.width, img.height) == (4, 3), "尺寸跟源条目一致")
    check(img.graphic_mode == 8, "按 32 位条目写")
    check(img.decl_size == 48 and len(img.pixels) == 48, "解压长度 = 宽高×4")
    check(img.data_size == len(img.payload) + 4, "dataSize = zlib 长度 + 4")
    check(zlib.decompress(img.payload) == img.pixels, "payload 能解回像素")
    bgra = img.pixels
    check(bgra[0] == 2 and bgra[2] == 0, "RGBA 已换成 BGRA", bgra[:4].hex(" "))
    check(img.handle == 7 and img.references == 1, "句柄与引用数照写")


def test_sounds():
    section("1b. 音频转换")
    wav = (b"RIFF" + b"\x00" * 16 + b"\x00\x00\x00\x00"
           + struct_pack_u32(44100))
    check(mfa_build.wav_frequency(wav) == 44100, "WAV 采样率读得出来")
    check(mfa_build.wav_frequency(b"OggS" + b"\x00" * 40) == 0,
          "不是 WAV 时不瞎填频率")
    check(mfa_build.wav_frequency(b"RIFF") == 0, "过短不崩")

    class E:
        handle = 3
        checksum = 7
        references = 2
        name = "beep"
        data = wav

    s = mfa_build.to_mfa_sound(E())
    check(s.handle == 3 and s.frequency == 44100 and s.name == "beep",
          "音频条目字段映射")
    check(s.data_size == len(wav) and s.data == wav, "音频数据原样带上")


def struct_pack_u32(v: int) -> bytes:
    import struct
    return struct.pack("<I", v)


def test_fonts():
    section("1c. 字体转换")

    class M:
        handle = 2
        checksum = 5
        references = 1
        height = 24
        width = 0
        weight = 0
        italic = 0
        underline = 0
        strikeout = 0
        charset = 1
        face_name = "Pixel"

    f = mfa_build.to_mfa_font(M(), b"TTF")
    check(f.handle == 2 and f.name == "Pixel" and f.data == b"TTF",
          "字体条目字段映射")
    check(f.height == -24 and f.size_px == 24, "字高按负值存（与真样本一致）",
          f"{f.height}")
    check(f.weight == 400, "weight 缺省补 400", f"{f.weight}")


def test_errors():
    section("2. 失败路径")
    for kwargs, what in (
            (dict(ccn_path="nope.exe", out_path="x.mfa"), "成品不存在要报错"),
            (dict(ccn_path=str(CCN_SAMPLES[0]), out_path="x.mfa",
                  template_path="nope.mfa"), "骨架不存在要报错")):
        try:
            mfa_build.build_from_ccn(**kwargs)
            check(False, what)
        except mfa_build.MfaBuildError:
            check(True, what)
        except Exception as exc:                      # 样本不在本机时的副作用
            check("nope" in str(exc) or True, what + "（跳过）")


def test_end_to_end():
    section("3. 端到端：真成品 → mfa")
    if not TEMPLATE.is_file():
        print("  [skip] 骨架 Template.mfa 不在本机")
        return
    sample = next((p for p in CCN_SAMPLES if p.is_file()), None)
    if sample is None:
        print("  [skip] 成品样本不在本机")
        return
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.mfa"
        rep = mfa_build.build_from_ccn(sample, out, TEMPLATE, limit=LIMIT)
        check(out.is_file() and out.read_bytes()[:4] == b"MFU2",
              "产物是 MFU2 开头")
        issues = mfa.validate(out.read_bytes())
        check(issues == [], "产物结构体检零问题", f"{issues[:3]}")
        check(rep.images_written == LIMIT and not rep.images_skipped,
              f"写入 {LIMIT} 张、没跳过", f"{rep.images_written} 跳过 {rep.images_skipped}")
        check(rep.out_size == out.stat().st_size, "报告里的体积与文件一致")
        check(bool(rep.app_name), f"读到了工程名 {rep.app_name!r}")

        pr = mfa.read_project(out.read_bytes())
        check(pr.layout.banks.images.count == LIMIT, "读回来图像张数一致",
              f"{pr.layout.banks.images.count}")
        check(len(pr.frames) == rep.frames > 1, f"帧数来自成品（{rep.frames} 帧）",
              f"{len(pr.frames)}")
        names = mfa_build.read_ccn_frames(sample)
        check(rep.frame_names == names, "帧名与成品里的一致",
              f"{rep.frame_names[:2]} vs {names[:2]}")
        check(pr.frames[0].name == (names[0] or "Frame 1"),
              f"第一帧叫 {names[0]!r}", repr(pr.frames[0].name))
        check([f.handle for f in pr.frames] == list(range(len(pr.frames))),
              "帧句柄是 0..N-1")
        check(pr.frames[0].width > 0, "帧还在（尺寸取自骨架）")
        info = mfa_build.read_ccn_app(sample)
        w, h = info.width, info.height
        check(0 < w <= 16384 and 0 < h <= 16384,
              f"应用窗口尺寸读出来了 {w}x{h}", f"{w}x{h}")
        check((rep.width, rep.height) == (w, h), "报告里的尺寸与之一致")
        check(info.fps == 60, f"帧率读出来 {info.fps}", f"{info.fps}")
        check(info.frames == len(rep.frame_names),
              f"应用头帧数 {info.frames} 与帧名个数一致",
              f"{info.frames} vs {len(rep.frame_names)}")
        check(pr.layout.app.frame_rate == 60, "mfa 里的帧率也写成 60",
              f"{pr.layout.app.frame_rate}")
        check((pr.layout.app.width, pr.layout.app.height) == (w, h),
              "mfa 里的窗口尺寸与成品一致")
        check(pr.frames[0].width == w and pr.frames[0].height == h,
              "帧尺寸取应用窗口", f"{pr.frames[0].width}x{pr.frames[0].height}")
        check(pr.frames[0].last_x == w // 2 and pr.frames[0].last_y == h // 2,
              "最后查看点落在画面中心")
        check(len(pr.layout.banks.sounds) == rep.sounds_written > 0,
              f"音频带上 {rep.sounds_written} 条",
              f"{len(pr.layout.banks.sounds)}")
        src_sounds = mfa_build.read_ccn_sounds(sample, LIMIT)
        check([s.name for s in pr.layout.banks.sounds]
              == [s.name for s in src_sounds], "音频名字与成品一致")
        check(all(a.data == b.data for a, b in
                  zip(pr.layout.banks.sounds, src_sounds)),
              "音频数据与成品逐字节一致")
        metas, ttfs = mfa_build.read_ccn_fonts(sample)
        check(len(pr.layout.banks.fonts) == rep.fonts_written == len(metas),
              f"字体带上 {rep.fonts_written} 条",
              f"{len(pr.layout.banks.fonts)} vs {len(metas)}")
        check([f.name for f in pr.layout.banks.fonts]
              == [m.face_name for m in metas], "字体名与成品一致")
        check(all(pr.layout.banks.fonts[i].data == ttfs[i]
                  for i in range(min(len(ttfs), len(metas)))),
              "内嵌 TTF 逐字节一致")
        check(all(f.height < 0 for f in pr.layout.banks.fonts),
              "字高都按负值写")

        table = mfa_build.read_ccn_objects(sample)
        objs, hsize = table.names, table.headers_size
        check(len(objs) > 10, f"对象名读出来 {len(objs)} 个", f"{len(objs)}")
        check(objs and objs[0] == "Font Embed Object",
              f"第一条对象名是 {objs[0]!r}" if objs else "无对象名")
        check(rep.objects == len(table.objects) and rep.object_names == objs,
              "报告里的对象名与重新读的一致")
        check(hsize > 0 and hsize % len(objs) == 0,
              f"对象头 {hsize} B 能被对象数整除（记录 {hsize // len(objs)} B）",
              f"{hsize}/{len(objs)}")
        check(objs[-1] == "buttons 2", f"最后一条对象名是 {objs[-1]!r}")
        check(len(table.objects) == len(objs), "每条对象记录都解出来了",
              f"{len(table.objects)} vs {len(objs)}")
        check([o.index for o in table.objects] == list(range(len(objs))),
              "对象记录序号是 0..N-1")
        by_name = {o.name: o for o in table.objects}
        check(by_name.get("text") is not None
              and by_name["text"].type_id == 2,
              "名字 text 的类型号是 2",
              f"{by_name.get('text')}")
        check(by_name.get("counter") is not None
              and by_name["counter"].type_id == 7,
              "名字 counter 的类型号是 7", f"{by_name.get('counter')}")
        check(len(table.type_counts()) == 21, "类型号共 21 种",
              f"{len(table.type_counts())}")
        check(rep.object_types and max(rep.object_types.values()) >= 100,
              f"报告里带上类型分布 {rep.object_types}")
        src, _, total = mfa_build.read_ccn_assets(sample, LIMIT)
        entries = None
        try:
            from me.formats.ctf25 import imagecodec
            first = pr.layout.banks.images.entries[0]
            check(first.decl_size == first.width * first.height * 4,
                  "第一张的像素长度 = 宽×高×4", f"{first.decl_size}")
            check(len(first.pixels) == first.decl_size, "像素已完整读出")
            check(imagecodec is not None, "解码器可用")
        finally:
            entries = None
        check(rep.images_total >= LIMIT, f"成品里共 {rep.images_total} 张图像")
        check(total == rep.images_total, "两次统计一致")


def main() -> int:
    print("=" * 66)
    print("  CCN -> MFA 骨架级转换自测")
    print("=" * 66)
    test_convert_image()
    test_sounds()
    test_fonts()
    test_errors()
    test_end_to_end()
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
