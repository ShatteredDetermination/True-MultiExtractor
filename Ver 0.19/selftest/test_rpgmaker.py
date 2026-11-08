# -*- coding: utf-8 -*-
"""rpgmaker 引擎 / rgssad 解析器的合成样本往返测试。

做法：用「加密逻辑的反向过程」现场造 RGSSAD / RGSS2A / RGSS3A 三个归档，
再用 me/formats/rgssad.py 解出来，逐字节比对。

⚠️ 这个测试的局限（重要）
    加密和解析出自同一个人对格式的理解，所以**只能证明自洽**，不能证明
    「和真游戏里的 .rgssad 一致」。真要证伪得靠真实样本。
    为此本文件额外做了两件事：
      1. ``test_key_timing_regression``：把「key 前进时机」这一处最容易搞错、
         也最容易自洽地搞错的细节用硬编码的期望值钉死，解析器或生成器任何一方
         改动时机都会立刻报错（而不是两边一起改、测试照样过）。
      2. ``test_reference_vectors``：如果本机能联网，拉公开实现
         （RPGMakerDecrypter 的测试样本存档）做一次交叉校验；拉不到就跳过并说明。

格式依据见 me/formats/rgssad.py 的模块 docstring。
"""

import os
import shutil
import struct
import sys
import tempfile
import urllib.request
from pathlib import Path

# 项目根从本文件位置推，别写死绝对路径：
# 版本归档会把整个目录搬到 Source Code\Ver x.xx\ 下，写死就立刻失效。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from me.collect.writer import OutputWriter            # noqa: E402
from me.engines import rpgmaker                        # noqa: E402
from me.engines.base import Context                    # noqa: E402
from me.formats import rgssad                          # noqa: E402
from me.scanner.detect import Detection                # noqa: E402

# ------------------------------------------------------------------ 造样本
def enc_v1(magic: bytes, version: int, files: list) -> bytes:
    """反向过程：v1 / v2 归档。files 是 [(归档内路径, 原始字节)]。"""
    out = bytearray(magic + bytes([version]))
    key = 0xDEADCAFE
    for name, data in files:
        raw = name.encode("utf-8")
        out += struct.pack("<I", len(raw) ^ key)
        key = (key * 7 + 3) & 0xFFFFFFFF
        for b in raw:
            out.append(b ^ (key & 0xFF))
            key = (key * 7 + 3) & 0xFFFFFFFF
        out += struct.pack("<I", len(data) ^ key)
        key = (key * 7 + 3) & 0xFFFFFFFF
        # 正文用自己的 key 副本加密，**不能**回灌到表的 key 上。
        # 这一点必须和 RPGMakerDecrypter 的 RGSSADv1.ReadRGSSAD() 对齐：
        # 它读完 Size 后只 Seek(Size)，表 key 不动。
        # （如果这里顺手把表 key 也走一遍，往返测试照样通过，但真实归档会错。）
        body = bytearray(data)
        bkey = key
        for i in range(0, len(body) - len(body) % 4, 4):
            struct.pack_into("<I", body, i,
                             struct.unpack_from("<I", body, i)[0] ^ bkey)
            bkey = (bkey * 7 + 3) & 0xFFFFFFFF
        low = struct.pack("<I", bkey)
        for i in range(len(body) - len(body) % 4, len(body)):
            body[i] ^= low[i % 4]
        out += body
    return bytes(out)


def enc_v3(files: list, initial_key: int = 0x12345678) -> bytes:
    """反向过程：v3 归档。files 是 [(归档内路径, 原始字节)]。

    布局：8 字节头（"RGSS3A\\0\\x03"）+ 4 字节 initial_key + 目录表 + 正文数据区。
    """
    base_key = (initial_key * 9 + 3) & 0xFFFFFFFF
    meta = []
    for name, data in files:
        meta.append((name.encode("utf-8"), data, 0xDEADCAFE ^ len(meta)))

    # 表长 = 头 8 字节 + initial_key 4 字节 + 每条 16 字节 + 名字 + 结束标记 4 字节
    off = 8 + 4 + sum(16 + len(raw) for raw, _d, _k in meta) + 4
    table = bytearray(rgssad.MAGIC_AD + b"\x03")
    table += struct.pack("<I", initial_key)
    for idx, (raw, data, magic) in enumerate(meta):
        table += struct.pack("<I", off ^ base_key)
        table += struct.pack("<I", len(data) ^ base_key)
        table += struct.pack("<I", magic ^ base_key)
        table += struct.pack("<I", len(raw) ^ base_key)
        kb = struct.pack("<I", base_key)
        for i, b in enumerate(raw):
            table.append(b ^ kb[i % 4])
        off += len(data)
    table += struct.pack("<I", 0 ^ base_key)   # offset == 0 表示表结束

    body = bytearray()
    for idx, (_raw, data, magic) in enumerate(meta):
        chunk = bytearray(data)
        key = magic
        for i in range(0, len(chunk) - len(chunk) % 4, 4):
            struct.pack_into("<I", chunk, i,
                             struct.unpack_from("<I", chunk, i)[0] ^ key)
            key = (key * 7 + 3) & 0xFFFFFFFF
        low = struct.pack("<I", key)
        for i in range(len(chunk) - len(chunk) % 4, len(chunk)):
            chunk[i] ^= low[i % 4]
        body += chunk
    return bytes(table + body)


def sample_files() -> list:
    """样本：覆盖尾部余数 0/1/2/3、空文件、反斜杠路径、日文名、嵌套目录。"""
    return [
        ("Graphics\\Pictures\\title.png", bytes(range(256)) * 4),   # 1024，4 对齐
        ("Data\\Scripts.rvdata2", b"X" * 37),                       # 尾部余 1
        ("Audio\\BGM\\theme.ogg", b"OggS" + os.urandom(46)),        # 尾部余 2
        ("Audio\\SE\\blip.wav", b"RIFF" + os.urandom(11)),          # 尾部余 3
        ("empty.txt", b""),                                        # 空文件
        ("Graphics\\\u65e5\u672c\u8a9e\\\u9854.png", b"\x89PNG\r\n\x1a\n" + b"z" * 30),
        ("deep\\a\\b\\c\\d.txt", b"nested" * 3),
    ]


# ------------------------------------------------------------------ 断言工具
class Fail(AssertionError):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


def roundtrip(path: Path, files: list, tag: str):
    """解析归档并与原始字节逐字节比对。返回解析出来的 Entry 列表。"""
    entries = rgssad.read_entries(path)
    check(len(entries) == len(files),
          f"{tag}: 条目数不符，期望 {len(files)}，实际 {len(entries)}")
    for entry, (name, data) in zip(entries, files):
        want = name.replace("\\", "/")
        check(entry.name == want, f"{tag}: 名字不符 {entry.name!r} != {want!r}")
        check(entry.size == len(data),
              f"{tag}: {want} 大小不符 {entry.size} != {len(data)}")
        got = rgssad.read_data(path, entry)
        check(got == data, f"{tag}: {want} 内容不符（{len(got)} vs {len(data)} 字节）")
    print(f"  [OK] {tag}: {len(entries)} 个条目全部逐字节一致")


# ------------------------------------------------------------------ 测试项
def test_roundtrip_all(tmp: Path):
    """三个格式各造一个归档，解出来逐字节比对。"""
    files = sample_files()
    cases = [
        # 真实归档的魔数一律是 "RGSSAD\0" + 版本号（1=v1, 3=v3），
        # 不是字面的 RGSS2A / RGSS3A —— 这点拿真样本验证过。
        ("Game.rgssad", enc_v1(rgssad.MAGIC_AD, 1, files), "rgssad", "RGSSAD(XP)"),
        ("Game.rgss2a", enc_v1(rgssad.MAGIC_AD, 1, files), "rgss2a", "RGSS2A(VX)"),
        ("Game_v2.rgss2a", enc_v1(rgssad.MAGIC_AD, 2, files), "rgss2a", "RGSS2A(版本号2)"),
        ("Game.rgss3a", enc_v3(files), "rgss3a", "RGSS3A(VX Ace)"),
        ("Odd.rgss3a", enc_v3(files, initial_key=0), "rgss3a", "RGSS3A(初始key=0)"),
        # 容错路径：少数第三方工具会写字符面的 RGSS3A
        ("Literal.rgss3a", enc_v3(files).replace(rgssad.MAGIC_AD, rgssad.MAGIC_3A, 1),
         "rgss3a", "RGSS3A(字面魔数容错)"),
    ]
    for filename, blob, kind, tag in cases:
        path = tmp / filename
        path.write_bytes(blob)
        got = rgssad.guess_archive_type(path)
        check(got == kind, f"{tag}: 类型识别错误 {got!r} != {kind!r}")
        roundtrip(path, files, tag)

    # 改名过的归档也要能认出来（按魔数）
    renamed = tmp / "not_really_a_dat.dat"
    renamed.write_bytes(enc_v1(rgssad.MAGIC_AD, 1, files))
    check(rgssad.guess_archive_type(renamed) == "rgssad", "按魔数识别改名归档失败")
    print("  [OK] 改名归档按魔数识别正确")


def test_key_timing_regression(tmp: Path):
    """把 key 前进时机钉死，防止「解析器和生成器一起改错」还能过测试。

    v1：nameLen/size 各自用当前 key，用完全各前进一次；数据第一个 4 字节
    用的就是异或 size 之后那一轮的 key（不再额外前进）。
    期望值是从公开实现（mkxp-z rgssad.cpp / RPGMakerDecrypter RGSSADv1.cs）
    的推进顺序手算出来的，不是从本仓库代码里抄的。
    """
    key = 0xDEADCAFE
    seq = []
    for _ in range(4):
        seq.append(key)
        key = (key * 7 + 3) & 0xFFFFFFFF
    want = [0xdeadcafe, 0x16c08cf5, 0x9f43dab6, 0x5adafafd]
    check(seq == want,
          f"key 前进公式不对: {[hex(v) for v in seq]} != {[hex(v) for v in want]}")

    # 单文件、文件名 4 字节、正文 8 字节：手动按顺序算一遍密文
    name = "a.txt".encode("utf-8")
    data = bytes(8)
    k = 0xDEADCAFE
    blob = bytearray(rgssad.MAGIC_AD + b"\x01")
    blob += struct.pack("<I", len(name) ^ k); k = (k * 7 + 3) & 0xFFFFFFFF
    for b in name:
        blob.append(b ^ (k & 0xFF)); k = (k * 7 + 3) & 0xFFFFFFFF
    blob += struct.pack("<I", len(data) ^ k); k = (k * 7 + 3) & 0xFFFFFFFF
    # 到这里 k 就该是数据第一个 dword 的 key（不再前进）
    blob += struct.pack("<I", 0 ^ k); k = (k * 7 + 3) & 0xFFFFFFFF
    blob += struct.pack("<I", 0 ^ k)
    path = tmp / "timing.rgssad"
    path.write_bytes(bytes(blob))
    entries = rgssad.read_entries(path)
    check(len(entries) == 1 and entries[0].name == "a.txt",
          f"key 时机回归: 解析结果 {entries!r}")
    check(rgssad.read_data(path, entries[0]) == data, "key 时机回归: 正文不符")
    print("  [OK] key 前进时机（v1）与公开实现一致")


def test_edge_cases(tmp: Path):
    """坏归档不能把整个引擎带崩，只能报可预期的错。"""
    # 魔数不对
    bad = tmp / "bad.rgssad"
    bad.write_bytes(b"NOTRGSS" + b"\x00" * 32)
    check(rgssad.guess_archive_type(bad) == "", "非归档被误判成归档")
    try:
        rgssad.read_entries(bad)
        raise Fail("坏魔数应当抛 RgssadError")
    except rgssad.RgssadError:
        pass

    # 文件被截断：只能在 0 个条目和「抛 RgssadError」之间二选一，不能抛别的
    files = sample_files()[:2]
    trunc = tmp / "trunc.rgssad"
    trunc.write_bytes(enc_v1(rgssad.MAGIC_AD, 1, files)[:40])
    try:
        entries = rgssad.read_entries(trunc)
        print(f"  [OK] 截断文件被安静地解析出 {len(entries)} 个条目（尾部不完整即止）")
    except rgssad.RgssadError as exc:
        print(f"  [OK] 截断文件抛出可预期的 RgssadError: {exc}")
    print("  [OK] 坏魔数 / 截断文件只抛 RgssadError")

    # 空目录表（只有 magic 的归档）
    empty = tmp / "empty.rgssad"
    empty.write_bytes(rgssad.MAGIC_AD + b"\x01")
    check(rgssad.read_entries(empty) == [], "空归档应解析出 0 个条目")
    print("  [OK] 空归档解析为 0 个条目")


def test_engine_end_to_end(tmp: Path):
    """跑一遍 rpgmaker 引擎：归档 + 散落文件 + 分类 + 清单。"""
    game = tmp / "game"
    game.mkdir()
    files = [
        ("Graphics\\Pictures\\title.png", b"\x89PNG\r\n\x1a\n" + bytes(range(200))),
        ("Audio\\BGM\\theme.ogg", b"OggS" + bytes(60)),
        ("Data\\Scripts.rvdata2", b"R" * 21),
    ]
    (game / "Game.rgss3a").write_bytes(enc_v3(files))
    (game / "Game.ini").write_bytes(b"[Game]\nLibrary=RGSS300.dll\n")
    (game / "loose.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"loose")
    (game / "readme.txt").write_bytes(b"hello")

    out_root = tmp / "out"
    writer = OutputWriter(out_root, engine="RPG Maker", source=str(game))
    ctx = Context(target=game, detection=Detection(engine="rpgmaker"),
                  writer=writer, work_dir=tmp / "work", deep=True)
    out = rpgmaker.extract(ctx)

    # 归档里 3 个 + 散落 2 个（loose.png / readme.txt）= 5；
    # 归档清单是给用户核对的报告，不计入 out.written（它写在 text/ 下，单独校验）。
    check(out.written == 5, f"写出数量不符: {out.written}（notes={out.notes}）")
    check(not out.fallback, "不该触发兜底")
    check("RGSS3A" in out.detail, f"detail 没提到归档类型: {out.detail!r}")
    print(f"  [OK] 引擎写出 {out.written} 个文件; detail = {out.detail}")

    # 分类目录（归档内的路径带 Graphics\/Audio\/Data\ 前缀，所以分类目录下还有一层）
    expected = {
        "image/Graphics/Pictures/title.png": files[0][1],
        "audio/Audio/BGM/theme.ogg": files[1][1],
        "other/Data/Scripts.rvdata2": files[2][1],
        "image/loose.png": b"\x89PNG\r\n\x1a\n" + b"loose",
        "text/readme.txt": b"hello",
    }
    for rel, want in expected.items():
        p = out_root / rel
        check(p.exists(), f"缺少输出文件: {rel}（实际 {_tree(out_root)}）")
        check(p.read_bytes() == want, f"{rel} 内容不符")

    # 归档清单
    man = out_root / "text" / "rgssad_manifest.txt"
    check(man.exists(), "缺少归档清单 text/rgssad_manifest.txt")
    lines = [ln for ln in man.read_text(encoding="utf-8").splitlines()
             if ln and not ln.startswith("#")]
    want_lines = [f"{n.replace(chr(92), '/')}\t{len(d)}" for n, d in files]
    check(lines == want_lines, f"清单不符:\n  得到 {lines}\n  期望 {want_lines}")
    tab_ok = all(len(ln.split("\t")) == 2 for ln in lines)
    check(tab_ok, "清单行不是 `相对路径<TAB>大小` 形式")
    print(f"  [OK] 归档清单 {len(lines)} 行，格式为 相对路径<TAB>大小")

    # 「不解容器」模式：只收散落文件
    out2 = tmp / "out2"
    w2 = OutputWriter(out2, engine="RPG Maker", source=str(game))
    ctx2 = Context(target=game, detection=Detection(engine="rpgmaker"),
                   writer=w2, work_dir=tmp / "work2", deep=False)
    res2 = rpgmaker.extract(ctx2)
    check(res2.written == 2, f"deep=False 应只收 2 个散落文件，实际 {res2.written}")
    check(not (out2 / "image" / "Graphics").exists(), "deep=False 不该解归档")
    print(f"  [OK] deep=False 只收集散落文件（{res2.written} 个），未解容器")

    # 单文件形式：直接传一个归档
    single_out = tmp / "out3"
    w3 = OutputWriter(single_out, engine="RPG Maker", source="Game.rgss3a")
    ctx3 = Context(target=game / "Game.rgss3a",
                   detection=Detection(engine="rpgmaker"),
                   writer=w3, work_dir=tmp / "work3", deep=True)
    res3 = rpgmaker.extract(ctx3)
    check(res3.written == 3, f"单归档应写出 3 个文件，实际 {res3.written}")
    print(f"  [OK] 直接传单个归档文件也能解（{res3.written} 个）")

    # 明文目录：没有归档，走通用收集
    plain = tmp / "plain"
    plain.mkdir()
    (plain / "Game.ini").write_bytes(b"[Game]\n")
    (plain / "pic.png").write_bytes(b"\x89PNG")
    w4 = OutputWriter(tmp / "out4", engine="RPG Maker", source=str(plain))
    ctx4 = Context(target=plain, detection=Detection(engine="rpgmaker"),
                   writer=w4, work_dir=tmp / "work4", deep=True)
    res4 = rpgmaker.extract(ctx4)
    check(res4.written == 1, f"明文目录应收集 1 个文件，实际 {res4.written}")
    check("明文" in res4.detail, f"detail 不对: {res4.detail!r}")
    print(f"  [OK] 明文目录兜底收集（{res4.written} 个）: {res4.detail}")


def _tree(root: Path) -> list:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


# 真实归档样本（uuksu/RPGMakerDecrypter 的测试 fixture）。
# 走镜像是因为 raw.githubusercontent.com 在部分网络下被拦。
REAL_BASE = ("https://ghfast.top/https://raw.githubusercontent.com/"
             "uuksu/RPGMakerDecrypter/master/RPGMakerDecrypter.Tests/"
             "EncryptedArchives")
REAL_FILES = (
    ("Game.rgssad", "rgssad", "Data/Actors.rxdata"),
    ("Game.rgss2a", "rgss2a", "Data/Actors.rvdata"),
    ("Game.rgss3a", "rgss3a", "Data/Actors.rvdata2"),
)


def test_reference_vectors(tmp: Path = None):
    """拿公开实现仓库里的**真实归档**做交叉校验。

    样本来自 uuksu/RPGMakerDecrypter 的测试 fixture（EncryptedArchives/），
    是真实游戏导出的归档，不是我们造的 —— 这是唯一能证伪
    「生成器和解析器共享同一个错误理解」的手段。

    要联网的部分：只有夹具缺失时才会去下。仓库里带了
    ``selftest/fixtures/``（见那里的 README），正常情况下离线也能跑。
    """
    bundled = Path(__file__).resolve().parent / "fixtures"
    cache = Path(tempfile.gettempdir()) / "me_rgssad_fixtures"
    cache.mkdir(parents=True, exist_ok=True)

    got = 0
    from_bundle = 0
    for name, want_kind, want_entry in REAL_FILES:
        local = bundled / name
        if local.exists() and local.stat().st_size > 1024:
            dst = local
            from_bundle += 1
        else:
            dst = cache / name
            if not dst.exists() or dst.stat().st_size < 1024:
                try:
                    req = urllib.request.Request(
                        f"{REAL_BASE}/{name}",
                        headers={"User-Agent": "MultiExtractor-selftest"})
                    with urllib.request.urlopen(req, timeout=40) as resp:
                        dst.write_bytes(resp.read())
                except Exception as exc:    # noqa: BLE001 - 网络问题一律跳过
                    print(f"  [SKIP] {name}: 下载失败（{type(exc).__name__}: {exc}）")
                    continue
        got += 1

        head = dst.read_bytes()[:8]
        print(f"        {name}: {dst.stat().st_size} 字节, 头 {head.hex(' ')}")
        kind = rgssad.guess_archive_type(dst)
        check(kind == want_kind, f"{name}: 魔数识别为 {kind!r}")

        entries = rgssad.read_entries(dst)
        check(len(entries) >= 1, f"{name}: 解析到 {len(entries)} 个条目")
        names = [e.name for e in entries]
        check(want_entry in names, f"{name}: 条目表里有 {want_entry}")

        target = next((e for e in entries if e.name == want_entry), entries[0])
        data = rgssad.read_data(dst, target)
        check(len(data) == target.size,
              f"{name}: {target.name} 长度对得上（{len(data)}/{target.size}）")
        # RPG Maker 的数据文件是 Ruby Marshal：\x04\x08 + 长度，紧跟类名
        check(data[:2] == b"\x04\x08",
              f"{name}: {target.name} 是 Ruby Marshal 数据")
        text = data[:96].decode("latin-1")
        check("RPG::" in text, f"{name}: 明文里出现 RPG:: 类名")
        print(f"        -> {target.name}: {len(data)} 字节, "
              f"头 {data[:16].hex(' ')}")

    if got == 0:
        print("  [SKIP] 三个真实样本一个都没拿到（夹具缺失且网络不通），本项未验证")
    else:
        src = "随包夹具" if from_bundle == got else (
            "随包夹具" if from_bundle else "在线下载")
        print(f"  [INFO] 用 {got} 个真实归档做了交叉校验（来源: {src}）")


# ------------------------------------------------------------------ 入口
def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="rgssad_selftest_"))
    print(f"临时目录: {tmp}")
    print(f"Python: {sys.version.split()[0]}  ({sys.executable})")
    failed = 0
    try:
        for fn in (test_roundtrip_all, test_key_timing_regression,
                   test_edge_cases, test_engine_end_to_end,
                   test_reference_vectors):
            print(f"\n== {fn.__name__} ==")
            try:
                if fn is test_reference_vectors:
                    fn()
                else:
                    fn(tmp)
            except Exception as exc:              # noqa: BLE001 - 测试要报告全部失败
                failed += 1
                import traceback
                print(f"  [FAIL] {type(exc).__name__}: {exc}")
                traceback.print_exc()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    if failed:
        print(f"结果：{failed} 个测试项失败")
        return 1
    print("结果：全部通过")
    print("说明：往返测试只能证明自洽；")
    print("      带 [INFO] 用 N 个真实归档 的那一步才是真正的交叉验证")
    print("      （用 selftest/fixtures/ 里的真实归档，离线可复现）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
