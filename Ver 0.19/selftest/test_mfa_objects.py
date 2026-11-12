# -*- coding: utf-8 -*-
"""占位对象 / 实例写手的自测（V0.19 起）。

四段：

  1. CObjectInfo 头 + 各类型加载器（Active / String / Counter）；
  2. 文件夹、实例、帧体拼装；
  3. 模板帧体拆段（事件段 + 帧尾 chunk 列表）；
  4. 端到端：真成品 + `objects=True`，帧头的对象数要与写进去的一致。

样本不在就跳过，不失败。
"""

import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.formats.ctf25 import mfa  # noqa: E402
from me.formats.ctf25 import mfa_build, mfa_objects  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []
SKIPPED = 0

TEMPLATE = Path(r"C:\拆包教程素材\Clickteam MFA 样本\Template.mfa")
CCN = Path(r"C:\拆包教程素材\目标游戏\dtlg-v1.1.0.exe")


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


def test_object_info():
    section("1. CObjectInfo 与加载器")
    blob = mfa_objects.build_object_info(2, 7, "Enemy")
    check(blob is not None and len(blob) == 100, "Active 块 100 B",
          str(len(blob) if blob else None))
    t, h = struct.unpack_from("<ii", blob, 0)
    check((t, h) == (2, 7), "类型 / 句柄", f"{t}/{h}")
    n = struct.unpack_from("<I", blob, 8)[0]
    check(n == 0x80000005, "名字长度带 Unicode 位 + 5 字符", hex(n))
    check(blob[12:22].decode("utf-16-le") == "Enemy", "名字内容")
    flags_off = 8 + 4 + len("Enemy") * 2 + 16
    flags = struct.unpack_from("<I", blob, flags_off)[0]
    check(flags == mfa_objects.OBJECT_FLAGS, "对象标志 0x20", hex(flags))
    check(blob[-1] == 0, "Active 加载器以「0 条动画」收尾", blob[-1:].hex())

    st = mfa_objects.build_object_info(3, 0, "text")
    check(st is not None and len(st) == 125, "String 块 125 B",
          str(len(st) if st else None))
    check(struct.unpack_from("<i", st, 0)[0] == 3, "String 类型 3")

    ct = mfa_objects.build_object_info(7, 0, "counter")
    check(ct is not None and len(ct) == 155, "Counter 块 155 B",
          str(len(ct) if ct else None))
    check(struct.unpack_from("<i", ct, 0)[0] == 7, "Counter 类型 7")

    with_text = mfa_objects.build_object_info(3, 0, "text", text="Hello")
    check(len(with_text) == len(st) + 4 + 2 * len("Hello") + 4,
          "带正文的 String 段落变长", f"{len(with_text)} vs {len(st)}")
    check("Hello".encode("utf-16-le") in with_text, "正文按 UTF-16 写进去")
    check(struct.unpack_from("<I", with_text, len(with_text) - 4)[0] == 0,
          "段落 flags = 0")

    common = {"flags": 0x00040008, "new_flags": 0x0005,
              "qualifiers": [7, -1, -1, -1, -1, -1, -1, -1],
              "back_color": b"\x10\x20\x30\x00"}
    with_flags = mfa_objects.build_loader(2, common=common)
    expected = mfa_objects.loader_flags(common)
    check(struct.unpack_from("<I", with_flags, 0)[0] == expected,
          "加载器用上了成品的对象标志", hex(struct.unpack_from("<I", with_flags, 0)[0]))
    check(struct.unpack_from("<I", with_flags, 4)[0] == 0x0005,
          "新对象标志也来自成品")
    check(with_flags[8:12] == b"\x10\x20\x30\x00", "背景色来自成品",
          with_flags[8:12].hex(" "))
    check(struct.unpack_from("<h", with_flags, 12)[0] == 7,
          "第一个限定符 = 7", str(struct.unpack_from("<h", with_flags, 12)[0]))
    check(struct.unpack_from("<h", with_flags, 26)[0] == -1,
          "末位仍是 -1")

    for unsupported in (0, 1, 4, 32, 33):
        check(mfa_objects.build_object_info(unsupported, 0, "x") is None,
              f"类型 {unsupported} 不写（返回 None）")


def test_instances():
    section("2. 文件夹 / 实例 / 帧体拼装")
    folder = mfa_objects.build_folders([3])
    check(len(folder) == 12, "一个文件夹 12 B", str(len(folder)))
    check(struct.unpack_from("<I", folder, 0)[0] == 1, "文件夹数 1")
    check(folder[4:8] == b"\x70\x00\x00\x03", "隐藏文件夹头 70 00 00 03",
          folder[4:8].hex(" "))
    check(struct.unpack_from("<i", folder, 8)[0] == 3, "子对象句柄 3")

    inst = mfa_objects.build_instance(object_handle=5, x=10, y=-20, layer=1,
                                      handle=2)
    check(len(inst) == 32, "实例 32 B", str(len(inst)))
    x, y, layer, handle = struct.unpack_from("<iiII", inst, 0)
    check((x, y, layer, handle) == (10, -20, 1, 2), "x / y / 图层 / 句柄",
          f"{x} {y} {layer} {handle}")
    check(struct.unpack_from("<I", inst, 24)[0] == 5, "ObjectInfo = 对象句柄")

    C = mfa_build.CcnInstance
    fake = [C(obj_index=3, x=1, y=2, layer=0, object_name="text", object_type=3),
            C(obj_index=3, x=3, y=4, layer=0, object_name="text", object_type=3),
            C(obj_index=9, x=5, y=6, layer=1, object_name="Ini", object_type=40)]
    blobs, handles, insts, skipped = mfa_objects.build_frame_objects(fake)
    check(len(blobs) == 1 and handles == [3], "同对象只写一次定义",
          f"{len(blobs)} {handles}")
    check(len(insts) == 2 and skipped == 1, "两条实例写、扩展那条跳过",
          f"{len(insts)} / {skipped}")

    rest = mfa_objects.build_frame_rest(blobs, handles, insts,
                                        events_raw=b"EV", chunks_raw=b"\x21")
    check(struct.unpack_from("<i", rest, 0)[0] == 3,
          "帧体直接从第一个对象开始（类型 3），不再重复写对象数",
          str(struct.unpack_from("<i", rest, 0)[0]))
    check(rest.endswith(b"EV\x21"), "事件段与帧尾 chunk 原样接在后面")


def test_inject():
    section("2b. 往模板帧里塞对象（最小测试件）")
    if not TEMPLATE.is_file():
        print("  [skip] 骨架不在本机")
        return
    raw = TEMPLATE.read_bytes()
    pr = mfa.read_project(raw)
    f = pr.frames[0]
    rect_before = mfa.frame_rect(f.rest_raw)
    wrote, skipped = mfa_objects.add_objects_to_frame(f, [
        (2, 0, "TestActive", 100, 120, 0),
        (3, 1, "TestString", 320, 240, 0),
        (7, 2, "TestCounter", 500, 300, 0),
        (33, 3, "Unsupported", 0, 0, 0),
    ])
    check(wrote == 3 and skipped == 1, "写 3 个、跳过 1 个扩展对象",
          f"{wrote}/{skipped}")
    check(f.object_count == 3, "帧头对象数 = 3", str(f.object_count))
    check(mfa.frame_rect(f.rest_raw) == rect_before, "虚拟矩形保持不变",
          str(mfa.frame_rect(f.rest_raw)))
    data = mfa.build_project(pr)
    check(mfa.validate(data) == [], "产物结构体检零问题")
    pr2 = mfa.read_project(data)
    check(pr2.frames[0].object_count == 3, "读回来还是 3 个对象",
          str(pr2.frames[0].object_count))
    check(mfa.frame_rect(pr2.frames[0].rest_raw) == rect_before,
          "读回来矩形也还在")


def test_split():
    section("3. 模板帧体拆段")
    if not TEMPLATE.is_file():
        global SKIPPED
        SKIPPED += 1
        print("  [skip] 骨架不在本机")
        return
    pr = mfa.read_project(TEMPLATE.read_bytes())
    rest = pr.frames[0].rest_raw
    events_raw, chunks_raw = mfa_objects.split_rest(rest)
    check(len(events_raw) + len(chunks_raw) + 8 == len(rest),
          "拆出来的两段 + 头 8 B = 原长", f"{len(events_raw)}+{len(chunks_raw)}")
    check(events_raw.endswith(b"\x21\x44\x4e\x45"), "事件段以 !DNE 收尾",
          events_raw[-4:].hex(" "))
    chunks = mfa.frame_chunks(chunks_raw)
    check(chunks is not None and len(chunks) == 12, "帧尾 chunk 12 条",
          str(len(chunks) if chunks else None))
    check(mfa.frame_rect(chunks_raw) == (0, 0, 640, 360), "矩形仍是模板的",
          str(mfa.frame_rect(chunks_raw)))


def test_end_to_end():
    section("4. 端到端（objects=True）")
    if not TEMPLATE.is_file() or not CCN.is_file():
        global SKIPPED
        SKIPPED += 1
        print("  [skip] 骨架或成品不在本机")
        return
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "obj.mfa"
        rep = mfa_build.build_from_ccn(CCN, out, TEMPLATE, limit=4, objects=True)
        check(out.is_file() and out.read_bytes()[:4] == b"MFU2", "产物是 MFU2")
        check(rep.objects_written > 0, f"写了 {rep.objects_written} 个占位对象",
              str(rep.objects_written))
        check(rep.instances_written > 0, f"写了 {rep.instances_written} 条实例",
              str(rep.instances_written))
        check(rep.instances_skipped > 0,
              f"跳过 {rep.instances_skipped} 条扩展对象实例")
        pr = mfa.read_project(out.read_bytes())
        total = sum(f.object_count for f in pr.frames)
        check(total == rep.objects_written, "帧头对象数之和 = 写入的对象数",
              f"{total} vs {rep.objects_written}")
        check(any(f.object_count for f in pr.frames), "至少一帧带对象")
        check(mfa.validate(out.read_bytes()) == [], "产物结构体检零问题")
        check(any("占位" in n for n in rep.notes), "报告里写明是占位对象")
        check("GAME CONTROLS".encode("utf-16-le") in out.read_bytes(),
              "String 正文真的写进了 mfa")
        check(any("正文" in n for n in rep.notes), "报告里写了正文条数")
        check(rep.objects_with_common == 867,
              f"867 个对象的属性头都解出来了（{rep.objects_with_common}）")
        parsed_instances = 0
        parsed_objects = 0
        for fr in pr.frames:
            got = mfa.parse_frame_objects(fr.rest_raw, fr.object_count)
            parsed_objects += len(got["objects"])
            parsed_instances += len(got["instances"])
        check(parsed_objects == rep.objects_written,
              f"独立解析器读回 {parsed_objects} 个对象", str(rep.objects_written))
        check(parsed_instances == rep.instances_written,
              f"独立解析器读回 {parsed_instances} 条实例",
              str(rep.instances_written))


def main():
    print("=" * 66)
    print("  MFA 占位对象 / 实例写手自测")
    print("=" * 66)
    test_object_info()
    test_instances()
    test_inject()
    test_split()
    test_end_to_end()
    print("\n" + "=" * 66)
    print(f"  PASS {PASS}  FAIL {FAIL}  SKIP {SKIPPED}")
    print("=" * 66)
    if FAILURES:
        print("\n失败项:")
        for f in FAILURES:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
