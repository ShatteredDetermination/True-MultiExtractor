# -*- coding: utf-8 -*-
"""UnityFS 索引与拆分自测（me/formats/unityfs.py）。

分两块：

  A. 手写一个最小的 **不压缩** UnityFS 包（真按格式拼字节），验证
     头 / 块表 / 目录 / 载荷拆分的整套流程，包括跨块的文件；
  B. 真包（本机 Steam 游戏存在时）：只用索引做断言 —— 块数、文件数、
     名字、每个 node 的类型与尺寸是否自洽（不碰载荷解压）。

载荷为什么只测不压缩的：实测两份 Steam 真包的块数据**不是 LZ4**
（flags 写的是 3 = LZ4HC，但每个块都解不开，块信息却是标准 LZ4），
说明它们的载荷用了另一种编码。那部分还没定标，所以不写进断言。

用法：
    python selftest/test_unityfs.py
"""

import struct
import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.formats import unityfs                    # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

REAL = [
    (Path(r"C:\Program Files (x86)\Steam\steamapps\common\SurroundedByBoys"
          r"\SurroundedByBoys_Data\data.unity3d"), 8),
    (Path(r"C:\Program Files (x86)\Steam\steamapps\common\Goose Goose Duck"
          r"\Goose Goose Duck_Data\data.unity3d"), 67),
]


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


def build_bundle(files, block_size=64, unity="2022.3.1f1"):
    """拼一个不压缩的 UnityFS 包：files = [(名字, 数据)]。

    载荷按 block_size 切块；块表与目录按实测布局写（大端、名字不补齐、
    块信息前 16 字节为 0）。
    """
    payload = b"".join(d for _n, d in files)
    blocks = []
    for i in range(0, len(payload), block_size):
        chunk = payload[i:i + block_size]
        blocks.append((len(chunk), len(chunk), 0))     # 未压缩：两个长度相等

    info = bytearray(b"\x00" * 16)
    info += struct.pack(">I", len(blocks))
    for u, c, f in blocks:
        info += struct.pack(">IIH", u, c, f)
    info += struct.pack(">I", len(files))
    off = 0
    for name, data in files:
        info += struct.pack(">qqI", off, len(data), 4)
        info += name.encode("utf-8") + b"\x00"
        off += len(data)

    head = bytearray()
    head += b"UnityFS\x00"
    head += struct.pack(">I", 8)
    head += b"5.x.x\x00"
    head += unity.encode() + b"\x00"
    size_at = len(head)
    head += struct.pack(">q", 0)                       # size，最后回填
    head += struct.pack(">I", len(info))               # cbinfo
    head += struct.pack(">I", len(info))               # ubinfo（不压缩）
    head += struct.pack(">I", 0)                       # flags = 不压缩
    head += b"\x00" * ((16 - len(head) % 16) % 16)
    body = bytearray(bytes(head) + bytes(info) + payload)
    struct.pack_into(">q", body, size_at, len(body))   # 回填真实大小
    return bytes(body)


def test_synthetic(tmp: Path):
    section("A. 手写不压缩包（真格式拼字节）")

    files = [
        ("globalgamemanagers", b"A" * 100),
        ("level0", b"B" * 50),
        ("resources.assets", b"C" * 150),
    ]
    blob = build_bundle(files, block_size=64)
    pak = tmp / "fake.unity3d"
    pak.write_bytes(blob)

    b = unityfs.read_bundle(pak)
    check(b.version == 8, f"版本 {b.version}")
    check(b.unity_revision == "2022.3.1f1", f"Unity 版本串 {b.unity_revision!r}")
    check(len(b.nodes) == 3, f"3 个文件（实际 {len(b.nodes)}）")
    check([n.name for n in b.nodes] == [n for n, _d in files], "名字与写入顺序一致")
    check([n.size for n in b.nodes] == [len(d) for _n, d in files], "尺寸对")
    check(len(b.blocks) == 5, f"块数 {len(b.blocks)}（300 字节 / 64）")
    check(b.payload_size == 300, f"载荷 {b.payload_size}")

    out = tmp / "out"
    rep = unityfs.extract_to(pak, out)
    check(rep.written == 3 and rep.total == 3, f"拆出 {rep.written}/{rep.total}")
    same = all((out / n).read_bytes() == d for n, d in files)
    check(same, "拆出来的内容逐字节一致（含跨块的文件）")

    # 越界 node 必须被拒
    bad = bytearray(blob)
    idx = bad.find(struct.pack(">qqI", 0, 100, 4))
    struct.pack_into(">q", bad, idx + 8, 10 ** 6)
    p2 = tmp / "bad.unity3d"
    p2.write_bytes(bytes(bad))
    try:
        unityfs.read_bundle(p2)
        check(False, "node 越界应该报错")
    except unityfs.UnityFsError:
        check(True, "node 越界报 UnityFsError")

    # 不是 UnityFS
    p3 = tmp / "not.pak"
    p3.write_bytes(b"nope" * 32)
    try:
        unityfs.read_bundle(p3)
        check(False, "非 UnityFS 应该报错")
    except unityfs.UnityFsError:
        check(True, "非 UnityFS 报 UnityFsError")


def test_real_bundles():
    section("B. 真包索引（Steam 上的两款 Unity 游戏）")
    for path, want_nodes in REAL:
        if not path.exists():
            print(f"  [skip] 找不到 {path}")
            continue
        b = unityfs.read_bundle(path)
        print(f"      {unityfs.describe(b)}")
        check(b.version == 8, f"{path.name}: 版本 8")
        check(len(b.nodes) == want_nodes,
              f"{path.name}: {len(b.nodes)} 个文件（期望 {want_nodes}）")
        names = [n.name for n in b.nodes]
        check("globalgamemanagers" in names, f"{path.name}: 有 globalgamemanagers")
        check(all(n.size > 0 for n in b.nodes), f"{path.name}: 所有 node 尺寸为正")
        last = max(b.nodes, key=lambda n: n.offset + n.size)
        check(last.offset + last.size <= b.payload_size,
              f"{path.name}: 最后一个 node 不越出载荷")
        check(len(b.blocks) > 100, f"{path.name}: 块数 {len(b.blocks)} > 100")


def test_engine(tmp: Path):
    section("C. 引擎自研通道（未压缩包能拆 / 压缩包明确退回）")
    from me.engines import unity as unity_engine
    from me.runner import run

    files = [("globalgamemanagers", b"X" * 200), ("resources.assets", b"Y" * 300)]
    game = tmp / "Game"
    data = game / "Game_Data"
    data.mkdir(parents=True)
    (data / "data.unity3d").write_bytes(build_bundle(files, block_size=128))

    out = tmp / "out_self"
    res = run(game, out_dir=out, quiet=True, engine_key="unity",
              experimental=True, want_self=True)
    print(f"      channel={res.channel} written={res.written}")
    check(res.channel == "self", f"走自研通道（{res.channel}）")
    check(any("me/formats/unityfs.py" in n for n in res.notes), "notes 写明 unityfs")
    check(res.written >= 2, f"拆出文件（written={res.written}）")

    # 把 flags 改成 lz4（3）：引擎应当明确退回第三方
    blob = bytearray(build_bundle(files, block_size=128))
    # flags 字段：版本串之后 + size(i64) + cbinfo(u32) + ubinfo(u32)
    head_end = blob.find(b"2022.3.1f1\x00") + len(b"2022.3.1f1\x00") + 8 + 4 + 4
    struct.pack_into(">I", blob, head_end, 3)
    (data / "data.unity3d").write_bytes(bytes(blob))
    res2 = run(game, out_dir=tmp / "out_fb", quiet=True, engine_key="unity",
               experimental=True, want_self=True)
    joined = " | ".join(res2.notes)
    check(any(("退回 AssetStudio" in n) or ("缺少 AssetStudio" in n) for n in res2.notes),
          f"压缩包明确不假装能拆（{joined[:90]}）")


def main() -> int:
    print("=" * 66)
    print("  UnityFS 索引 / 拆分自测")
    print("=" * 66)

    tmp = Path(tempfile.mkdtemp(prefix="me_ufs_"))
    try:
        test_synthetic(tmp)
        test_real_bundles()
        test_engine(tmp)
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
