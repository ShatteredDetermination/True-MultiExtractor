# -*- coding: utf-8 -*-
"""自研 Unreal .pak 读取器（me/formats/ue_pak.py）的对照测试。

三块：

  A. 手写合成 pak —— 不依赖 repak，专门验解析器本身：字段宽度组合、
     UTF-16 文件名（FString 负长度）、尾部缺字段、坏文件。
  B. 真 repak 对照 —— 用 repak 打包，自研读回来，条目名与
     ``repak list`` 一字不差，解出来的字节与源文件逐字节相同。
     覆盖版本 V0–V9、未压缩 / zlib / gzip。
  C. 边界 —— UE5（v10/v11）明确报 UnsupportedVersion，畸形 pak 报 PakError。

用法：
    python selftest/test_ue_pak.py
"""

import struct
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.collect.writer import OutputWriter        # noqa: E402
from me.formats import ue_pak                     # noqa: E402
from me.tools import paths as tool_paths          # noqa: E402

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


def section(title):
    print("\n== " + title + " ==")


# ---------------------------------------------------------------- 合成 pak
def fstr(text: str, utf16: bool = False) -> bytes:
    """UE 的 FString：正长度 = UTF-8（含 NUL），负长度 = UTF-16。"""
    if utf16:
        raw = text.encode("utf-16-le") + b"\x00\x00"
        return struct.pack("<i", -(len(raw) // 2)) + raw
    raw = text.encode("utf-8") + b"\x00"
    return struct.pack("<i", len(raw)) + raw


def build_pak(items, version=3, mount="../../../", utf16=False,
              comp_size=4, ts_size=0):
    """手写一个最小合法 pak（全部条目未压缩）。

    items: [(名字, 数据), ...]
    comp_size / ts_size 用来造不同版本的字段宽度（V8A 的压缩方式是 1 字节、
    V1 有 8 字节时间戳）。
    """
    has_tail = version >= 3
    body = bytearray()
    metas = []

    for name, data in items:
        off = len(body)
        header = struct.pack("<qqq", 0, len(data), len(data))
        if comp_size == 4:
            header += struct.pack("<I", 0)
        elif comp_size == 1:
            header += struct.pack("<B", 0)
        if ts_size:
            header += struct.pack("<q", 0)
        header += b"\x00" * 20
        if has_tail:
            header += struct.pack("<I", 0) + b"\x00"
        body += header + data
        metas.append((name, off, len(data)))

    idx = bytearray()
    idx += fstr(mount)
    idx += struct.pack("<I", len(metas))
    for name, off, size in metas:
        idx += fstr(name, utf16=utf16)
        idx += struct.pack("<qqq", off, size, size)
        if comp_size == 4:
            idx += struct.pack("<I", 0)
        elif comp_size == 1:
            idx += struct.pack("<B", 0)
        if ts_size:
            idx += struct.pack("<q", 0)
        idx += b"\x00" * 20
        if has_tail:
            idx += struct.pack("<I", 0) + b"\x00"

    index_off = len(body)
    blob = bytes(body) + bytes(idx)
    blob += struct.pack("<I", ue_pak.MAGIC)
    blob += struct.pack("<I", version)
    blob += struct.pack("<qq", index_off, len(idx))
    blob += b"\x00" * 20
    return blob


# ---------------------------------------------------------------- A 合成
def test_synthetic(tmp: Path):
    section("A. 手写合成 pak（不依赖 repak）")

    payloads = [("Game/a.txt", b"alpha"), ("Game/b.bin", bytes(range(200)))]
    for version, comp_size, ts_size, tag in [
            (3, 4, 0, "v3 常见布局"),
            (3, 1, 0, "v8A 式 1 字节压缩方式"),
            (1, 4, 8, "v1 带时间戳"),
            (0, 4, 0, "v0 无尾部"),
    ]:
        blob = build_pak(payloads, version=version,
                         comp_size=comp_size, ts_size=ts_size)
        pak = tmp / f"syn_{tag}.pak"
        pak.write_bytes(blob)
        try:
            idx = ue_pak.read_index(pak)
        except Exception as exc:                       # noqa: BLE001
            check(False, f"{tag}: 解析抛了 {type(exc).__name__}: {exc}")
            continue
        check(sorted(idx.names()) == sorted(n for n, _ in payloads),
              f"{tag}: 条目名对（{idx.names()}）")
        check(idx.mount == "../../../", f"{tag}: 挂载点 {idx.mount!r}")
        ok = True
        for e in idx.entries:
            want = dict(payloads)[e.name]
            if ue_pak.entry_bytes(blob, e) != want:
                ok = False
        check(ok, f"{tag}: 条目字节与写入时一致")

    # UTF-16 文件名：FString 负长度那一支
    blob = build_pak([("关卡/地图.unity3d", b"x" * 16)], version=3, utf16=True)
    pak = tmp / "syn_utf16.pak"
    pak.write_bytes(blob)
    idx = ue_pak.read_index(pak)
    check(idx.names() == ["关卡/地图.unity3d"],
          f"UTF-16 文件名读得出来（{idx.names()}）")
    check(ue_pak.entry_bytes(blob, idx.entries[0]) == b"x" * 16,
          "UTF-16 名字的条目数据也对")

    # 写进 writer：分类、路径前缀都对
    out = tmp / "out_syn"
    w = OutputWriter(out, engine="unreal")
    rep = ue_pak.extract(pak, w)
    check(rep.written == 1 and rep.total == 1, f"extract 写出 {rep.written}/{rep.total}")
    check((out / "other" / "syn_utf16" / "关卡" / "地图.unity3d").exists(),
          "落盘路径 = <pak 名>/<条目名>（分类 other）")


def test_bad_files(tmp: Path):
    section("A2. 畸形 / 非 pak")

    for name, blob in [
            ("empty.pak", b""),
            ("short.pak", b"abc"),
            ("nopak.pak", b"\x00" * 512),
            ("badmagic.pak", struct.pack("<I", 0xDEADBEEF) * 16),
    ]:
        p = tmp / name
        p.write_bytes(blob)
        try:
            ue_pak.read_index(p)
            check(False, f"{name}: 应该报错却解析成功")
        except ue_pak.PakError as exc:
            check(True, f"{name}: 报 PakError（{str(exc)[:40]}）")
        except Exception as exc:                       # noqa: BLE001
            check(False, f"{name}: 抛了 {type(exc).__name__}，应该是 PakError")

    # 索引偏移越界：footer 指向文件外
    good = build_pak([("a.txt", b"0123456789")], version=3)
    broken = bytearray(good)
    #  最后 44 字节是 footer：magic(4) version(4) off(8) size(8) hash(20)
    struct.pack_into("<q", broken, len(broken) - 36, len(good) * 4)
    p = tmp / "oob.pak"
    p.write_bytes(bytes(broken))
    try:
        ue_pak.read_index(p)
        check(False, "索引越界: 应该报错")
    except ue_pak.PakError:
        check(True, "索引越界: 报 PakError")


def test_unsupported(tmp: Path):
    section("C. UE5（v10/v11）明确不支持")

    for version in (10, 11):
        blob = build_pak([("a.txt", b"x")], version=3)
        blob = bytearray(blob)
        struct.pack_into("<I", blob, len(blob) - 40, version)
        p = tmp / f"ue5_{version}.pak"
        p.write_bytes(bytes(blob))
        try:
            ue_pak.read_index(p)
            check(False, f"v{version}: 应该抛 UnsupportedVersion")
        except ue_pak.UnsupportedVersion as exc:
            check(f"{version}" in str(exc), f"v{version}: UnsupportedVersion 带版本号")
        except ue_pak.PakError:
            check(False, f"v{version}: 应该是 UnsupportedVersion，不是普通 PakError")


# ---------------------------------------------------------------- B 真 repak
CASES = [
    ("V0", ""), ("V1", ""), ("V2", ""), ("V3", ""), ("V4", ""),
    ("V5", ""), ("V6", ""), ("V7", ""), ("V8A", ""), ("V8B", ""), ("V9", ""),
    ("V3", "Zlib"), ("V5", "Zlib"), ("V7", "Gzip"), ("V8B", "Zlib"),
    ("V9", "Zlib"), ("V9", "Gzip"),
]


def test_against_repak(tmp: Path):
    section("B. 真 repak 对照（取名 + 取字节）")

    repak = tool_paths.find("repak")
    if repak is None:
        print("  [skip] 没有 repak.exe，跳过对照")
        return

    src = tmp / "src"
    files = {
        "hello.txt": b"hello unreal pak\n",
        "Sub/a.bin": bytes(range(256)) * 4,
        "Sub/big.log": b"compress me please " * 500,
        "Sub/rand.dat": bytes((i * 37 + 11) & 0xFF for i in range(5000)),
        "Sub/deep/z.txt": b"z" * 10,
    }
    for rel, data in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    good = 0
    for version, comp in CASES:
        tag = f"{version}+{comp or 'none'}"
        pak = tmp / f"repak_{version}_{comp or 'none'}.pak"
        argv = [str(repak), "pack", str(src), str(pak), "--version", version]
        if comp:
            argv += ["--compression", comp]
        proc = subprocess.run(argv, capture_output=True)
        if not pak.exists() or pak.stat().st_size == 0:
            check(False, f"{tag}: repak 没产出（退出码 {proc.returncode}）")
            continue
        blob = pak.read_bytes()

        # 1) 条目名以 repak list 为准
        lst = subprocess.run([str(repak), "list", str(pak)],
                             capture_output=True)
        want = sorted(ln.strip().replace("\\", "/")
                      for ln in lst.stdout.decode("utf-8", "replace").splitlines()
                      if ln.strip())
        try:
            idx = ue_pak.read_index(pak)
        except Exception as exc:                       # noqa: BLE001
            check(False, f"{tag}: 解析抛 {type(exc).__name__}: {str(exc)[:80]}")
            continue
        got = sorted(idx.names())
        if got != want:
            check(False, f"{tag}: 条目名不一致 repak={want} 自研={got}")
            continue

        # 2) 字节逐个对
        bad = []
        for e in idx.entries:
            try:
                if ue_pak.entry_bytes(blob, e) != files[e.name]:
                    bad.append(e.name)
            except Exception as exc:                   # noqa: BLE001
                bad.append(f"{e.name}({type(exc).__name__})")
        if bad:
            check(False, f"{tag}: 解出来的字节不对 {bad}")
            continue

        # 3) extract() 走 writer 也一致
        out = tmp / f"out_{version}_{comp or 'none'}"
        w = OutputWriter(out, engine="unreal")
        rep = ue_pak.extract(pak, w)
        same = all((out / "other" / pak.stem / rel).read_bytes() == data
                   for rel, data in files.items()
                   if (out / "other" / pak.stem / rel).exists())
        if rep.written != len(files) or not same:
            check(False, f"{tag}: extract 写出 {rep.written}/{len(files)}")
            continue

        good += 1
        print(f"  [ok] {tag:<12} {len(idx.entries)} 条目 / {len(blob):,} 字节")

    check(good == len(CASES), f"repak 对照 {good}/{len(CASES)} 组全对")


def main() -> int:
    print("=" * 66)
    print("  自研 Unreal .pak 读取器自测")
    print("=" * 66)

    tmp = Path(tempfile.mkdtemp(prefix="me_uepak_"))
    try:
        test_synthetic(tmp)
        test_bad_files(tmp)
        test_unsupported(tmp)
        test_against_repak(tmp)
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
