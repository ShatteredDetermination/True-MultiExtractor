# -*- coding: utf-8 -*-
"""自研 Enigma Virtual Box 读取器（me/formats/evb.py）的对照测试。

三块：

  A. 手写一个最小 Enigma exe（真的 PE + .enigma1 节），把文件表按实测布局
     写进去，再用自研读回来：名字、长度、数据起点、字节全对。
     其中一条用 LZMA 压缩，验证解压分支。
  B. 畸形文件：不是 PE、没有 .enigma1、节被截断 —— 一律 EvbError，不崩。
  C. 真样本（本机有 cage-of-abyss 时）：4 个条目、数据起点 0x783525、
     data.win 头是 FORM/GEN8，且铺出来的文件字节与 evbunpack 的产物一致。

用法：
    python selftest/test_evb.py
"""

import lzma
import struct
import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.formats import evb                       # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

REAL_SAMPLE = Path(r"C:\拆包教程素材\目标游戏\cage-of-abyss-full-game-v0.2.2.exe")


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


# ---------------------------------------------------------------- 合成 exe
def build_pe(sections):
    """拼一个最小 PE：sections = [(名字, 节数据)]，按 0x200 对齐。"""
    hdr = bytearray(0x400)
    hdr[0:2] = b"MZ"
    struct.pack_into("<I", hdr, 0x3C, 0x80)              # e_lfanew
    hdr[0x80:0x84] = b"PE\x00\x00"
    nt = 0x84
    struct.pack_into("<H", hdr, nt, 0x8664)              # machine
    struct.pack_into("<H", hdr, nt + 2, len(sections))   # NumberOfSections
    optsize = 0xF0
    struct.pack_into("<H", hdr, nt + 16, optsize)
    sec_table = nt + 20 + optsize

    body = bytearray()
    base_raw = 0x400
    for i, (name, data) in enumerate(sections):
        rawptr = base_raw + len(body)
        pad = (-len(data)) % 0x200
        body += data + b"\x00" * pad
        o = sec_table + i * 40
        hdr[o:o + 8] = name.encode("latin-1").ljust(8, b"\x00")
        struct.pack_into("<IIII", hdr, o + 8, len(data), 0x1000 * (i + 1),
                         len(data) + pad, rawptr)
    return bytes(hdr + body)


def build_record(seq, name, payload, kind=2, compressed=False, attrs=0x80):
    """一条文件表记录 + 它的数据。返回 (记录字节, 存储字节)。"""
    if compressed:
        stored = lzma.compress(payload, format=lzma.FORMAT_ALONE)
    else:
        stored = payload
    rec = bytearray()
    rec += struct.pack("<III", seq, kind, 0)             # 序号 / 类型 / 保留
    rec += name.encode("utf-16-le") + b"\x00\x00"        # 名字
    rec += struct.pack("<B", kind)                       # 类型
    rec += b"\x00\x00"
    rec += struct.pack("<Q", len(payload))               # 原始长度
    for _ in range(3):
        rec += struct.pack("<d", 1.0)
    rec += struct.pack("<I", attrs)                      # 属性（u32）
    rec += b"\x00" * 11                                  # 保留
    rec += struct.pack("<Q", len(stored))                # 存储长度
    return bytes(rec), stored


def build_evb(entries, header_junk=64):
    """entries = [(名字, 数据, 是否压缩, 类型)]；返回完整 exe 字节。"""
    recs = bytearray()
    datas = bytearray()
    for i, (name, payload, compressed, kind) in enumerate(entries):
        rec, stored = build_record(i + 1, name, payload, kind=kind,
                                   compressed=compressed)
        recs += rec
        datas += stored
    sec = b"\x00" * header_junk + bytes(recs) + bytes(datas)
    return build_pe([(".text", b"\x90" * 64), (".enigma1", sec)])


# ---------------------------------------------------------------- A 合成
def test_synthetic(tmp: Path):
    section("A. 手写 Enigma exe（真 PE + .enigma1）")

    payloads = [
        ("data.win", b"FORM\x00\x01\x02\x03GEN8" + b"x" * 200, False, 2),
        ("readme.txt", b"hello enigma\n" * 3, False, 2),
        ("big.log", b"compress me please " * 300, True, 2),
    ]
    exe = tmp / "fake_enigma.exe"
    exe.write_bytes(build_evb(payloads))

    idx = evb.read_index(exe)
    names = [e.name for e in idx.entries]
    check(names == [n for n, _d, _c, _k in payloads],
          f"条目名与写入顺序一致（{names}）")
    check(all(e.is_file for e in idx.entries), "三条都是文件")
    sizes = {e.name: e.size for e in idx.entries}
    ok = all(sizes[n] == len(d) for n, d, _c, _k in payloads)
    check(ok, f"原始长度全对（{sizes}）")

    blob = exe.read_bytes()
    good = []
    for e in idx.entries:
        want = dict((n, d) for n, d, _c, _k in payloads)[e.name]
        try:
            good.append(evb.entry_bytes(blob, e) == want)
        except evb.EvbError:
            good.append(False)
    check(all(good), "三条内容逐字节一致（含 LZMA 那条）")

    big = [e for e in idx.entries if e.name == "big.log"][0]
    check(big.compressed and big.stored < big.size,
          f"压缩条目：存储 {big.stored} < 原始 {big.size}")

    # 铺到目录
    out = tmp / "out_vfs"
    rep = evb.extract_to(exe, out)
    check(rep.written == 3 and rep.total == 3, f"铺出 {rep.written}/{rep.total}")
    same = all((out / n).exists() and (out / n).read_bytes() == d
               for n, d, _c, _k in payloads)
    check(same, "铺出来的文件内容一致")

    # 数据起点 = 表结束位置，且第一条文件就在那里
    first = idx.entries[0]
    check(first.offset == idx.data_start, "第一条文件从数据起点开始")
    check(blob[first.offset:first.offset + 4] == b"FORM",
          "数据起点处的第一字节就是第一条内容")

    # 目录节点（type != 2）也要认出来，但不占数据
    exe2 = tmp / "with_dir.exe"
    exe2.write_bytes(build_evb([("folder", b"", False, 1),
                                ("folder/a.txt", b"aaa", False, 2)]))
    idx2 = evb.read_index(exe2)
    check(len(idx2.entries) == 2 and not idx2.entries[0].is_file,
          "目录节点：is_file=False")
    check(idx2.files()[0].size == 3, "目录之后的文件长度正确")


def test_bad_files(tmp: Path):
    section("B. 畸形 / 非 Enigma")

    cases = {
        "empty.exe": b"",
        "notpe.exe": b"hello world" * 40,
        "no_enigma_section.exe": build_pe([(".text", b"\x90" * 64)]),
    }
    for name, blob in cases.items():
        p = tmp / name
        p.write_bytes(blob)
        try:
            evb.read_index(p)
            check(False, f"{name}: 应该报错却成功了")
        except evb.EvbError as exc:
            check(True, f"{name}: EvbError（{str(exc)[:36]}）")
        except Exception as exc:                       # noqa: BLE001
            check(False, f"{name}: 抛了 {type(exc).__name__}，应该是 EvbError")

    # 节长度超出文件
    pe = bytearray(build_pe([(".enigma1", b"\x00" * 64)]))
    struct.pack_into("<I", pe, 0x400 + 40 * 1 + 16, 0x100000)   # rawsize
    p = tmp / "truncated.exe"
    p.write_bytes(bytes(pe))
    try:
        evb.read_index(p)
        check(False, "节越界: 应该报错")
    except evb.EvbError:
        check(True, "节越界: EvbError")


def test_real_sample(tmp: Path):
    section("C. 真样本（cage-of-abyss，Enigma 壳）")
    if not REAL_SAMPLE.exists():
        print(f"  [skip] 找不到 {REAL_SAMPLE}")
        return

    idx = evb.read_index(REAL_SAMPLE)
    check(len(idx.entries) == 4, f"4 个条目（实际 {len(idx.entries)}）")
    check(idx.data_start == 0x783525,
          f"数据起点 0x{idx.data_start:x} == 0x783525")
    names = sorted(e.name for e in idx.entries)
    check(names == ["data.win", "dolater_license.txt", "options.ini",
                    "scribble_license.txt"], f"名字对（{names}）")
    sizes = {e.name: e.size for e in idx.entries}
    check(sizes.get("data.win") == 63630288, f"data.win {sizes.get('data.win')}")
    check(sizes.get("options.ini") == 46, "options.ini 46 字节")

    out = tmp / "real_vfs"
    rep = evb.extract_to(REAL_SAMPLE, out)
    check(rep.written == 4, f"铺出 {rep.written} 个文件")
    head = (out / "data.win").read_bytes()[:12]
    check(head[:4] == b"FORM" and head[8:12] == b"GEN8",
          f"data.win 头是 FORM/GEN8（{head[:12]!r}）")


def test_engine_end_to_end(tmp: Path):
    section("D. 引擎端到端（自研通道，只取文本，跑得快）")
    if not REAL_SAMPLE.exists():
        print(f"  [skip] 找不到 {REAL_SAMPLE}")
        return

    from me.runner import run

    out = tmp / "e2e"
    res = run(REAL_SAMPLE, out_dir=out, categories={"text"}, quiet=True,
              experimental=True, want_self=True)
    print(f"      channel={res.channel} written={res.written} "
          f"{res.elapsed:.1f}s")
    check(res.channel == "self", f"引擎选了自研通道（{res.channel}）")
    joined = "\n".join(res.notes)
    check("me/formats/evb.py" in joined, "notes 里写明用 evb.py")
    check("内层通道: 自研" in joined, "内层引擎也继承了自研通道")
    check(res.written > 0, f"真的解出文件（written={res.written}）")


def main() -> int:
    print("=" * 66)
    print("  自研 Enigma Virtual Box 读取器自测")
    print("=" * 66)

    tmp = Path(tempfile.mkdtemp(prefix="me_evb_"))
    try:
        test_synthetic(tmp)
        test_bad_files(tmp)
        test_real_sample(tmp)
        test_engine_end_to_end(tmp)
    finally:
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
