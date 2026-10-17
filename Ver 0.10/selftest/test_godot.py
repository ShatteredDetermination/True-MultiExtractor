# -*- coding: utf-8 -*-
"""Godot PCK 解析器 + 引擎模块自测：合成样本往返。

自己按格式造 .pck（覆盖各种头部布局与 offset 基准），再用 me/formats/godot_pck
解开，逐字节比对内容，并检查 writer 输出目录里的文件个数；最后跑一遍引擎模块的
三条分支（目录里的 pck / 工程形式 / 不解容器）。

直接运行： <项目自带的 python> selftest/test_godot.py
（本文件是脚本，不用 __main__ 保护，导入即执行 —— selftest 目录下不会被当模块引用）
"""

import hashlib
import struct
import sys
import tempfile
from pathlib import Path

ROOT = r"C:\Users\Administrator\Desktop\MultiExtractor"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from me import engines
from me.collect import kinds
from me.collect.writer import OutputWriter
from me.engines import base, godot
from me.formats import godot_pck
from me.scanner.detect import Detection

# ---------------------------------------------------------------- 样本数据
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
OGG = b"OggS\x00\x02" + bytes(range(256)) * 3
JSON = '{"name": "测试关卡", "level": 1}'.encode("utf-8")
TXT = "这是中文说明文件\n".encode("utf-8") * 20
BIG = bytes(range(256)) * 6144                 # 1.5MB，专门用来走分块搬运那条路
EVIL = b"traversal-attempt"

# pck 里的路径 -> 内容
ITEMS = [
    ("res://sprites/player.png", PNG),
    ("res://audio/theme.ogg", OGG),
    ("res://data/level1.json", JSON),
    ("res://text/说明文档.txt", TXT),           # 非 ASCII 路径，长度不是 4 的倍数
    ("res://textures/big.png", BIG),
    ("res://../../evil.png", EVIL),            # 目录穿越，必须被清洗
]
# 期望的输出相对路径 -> 内容（evil 那条按 me.collect.writer.sanitize_rel 的规则压平）
EXPECT = {
    "sprites/player.png": PNG,
    "audio/theme.ogg": OGG,
    "data/level1.json": JSON,
    "text/说明文档.txt": TXT,
    "textures/big.png": BIG,
    "evil.png": EVIL,
}

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"[ OK ] {name}")
    else:
        FAILED.append(name)
        print(f"[FAIL] {name}  {detail}")


# ---------------------------------------------------------------- 造样本
def _align(n, alignment):
    if alignment <= 1:
        return n
    rest = n % alignment
    return n if rest == 0 else n + alignment - rest


def build_pck(dest, items, *, version=2, flags_field=True, file_base_field=True,
              reserved=16, offset_base="file_base", align=32, dir_flags=0,
              padded_len=True):
    """按指定布局造一个合成 pck，返回文件大小。

    头部长度 = 20 + (4 flags) + (8 file_base) + reserved，也就是目录起点：
      真 Godot 3.x: flags 无, file_base 无, reserved 64  -> 84
      真 Godot 4.x: flags 4, file_base 8, reserved 64    -> 96
      简化 v2:      flags 4, file_base 8, reserved 16    -> 48
      简化 v1:      flags 4, file_base 无, reserved 0    -> 24
    offset_base: "file_base" 记录里存相对数据区起点的偏移；"absolute" 存绝对偏移。
    padded_len:  path_len 是否把补齐到 4 字节的字节数算进去（真 Godot 是算进去的）。
    """
    hlen = 32 if version >= 3 else 16
    blob = bytearray()
    blob += struct.pack("<IIIII", 0x43504447, version, 4, 3, 0)
    if flags_field:
        blob += struct.pack("<I", dir_flags)
    if file_base_field:
        blob += struct.pack("<Q", 0)                # 占位，最后回填数据区起点
    blob += b"\x00" * reserved

    # 记录长度只跟路径长度有关，先算出来才能定数据区起点
    recs = []
    for item in items:
        name, data = item[0], item[1]
        fflags = item[2] if len(item) > 2 else 0
        raw = name.encode("utf-8")
        pad = (-len(raw)) % 4
        plen = len(raw) + pad if padded_len else len(raw)
        rec_size = 4 + plen + (0 if padded_len else pad) + 8 + 8 + hlen
        if version >= 2:
            rec_size += 4
        recs.append({"raw": raw, "pad": pad, "plen": plen, "data": data,
                     "flags": fflags, "rec_size": rec_size})

    dir_end = len(blob) + 4 + sum(r["rec_size"] for r in recs)
    data_start = _align(dir_end, align)

    cur = data_start
    for r in recs:
        r["abs"] = cur
        cur = _align(cur + len(r["data"]), align)
    filesize = cur

    base = data_start if offset_base == "file_base" else 0
    blob += struct.pack("<I", len(recs))
    for r in recs:
        blob += struct.pack("<I", r["plen"])
        blob += r["raw"] + b"\x00" * r["pad"]
        blob += struct.pack("<QQ", r["abs"] - base, len(r["data"]))
        blob += (hashlib.sha256(r["data"]).digest() if hlen == 32
                 else hashlib.md5(r["data"]).digest())
        if version >= 2:
            blob += struct.pack("<I", r["flags"])
    blob += b"\x00" * (data_start - len(blob))
    for r in recs:
        blob += r["data"]
        blob += b"\x00" * (_align(len(blob), align) - len(blob))
    assert len(blob) == filesize, (len(blob), filesize)

    if file_base_field:
        struct.pack_into("<Q", blob, 24 if flags_field else 20, data_start)
    dest.write_bytes(bytes(blob))
    return len(blob)


def _files_under(root):
    root = Path(root)
    return sorted(p.relative_to(root).as_posix()
                  for p in root.rglob("*") if p.is_file())


def make_ctx(target, out_dir, deep=True):
    target = Path(target)
    det = Detection(engine="godot", name="Godot", entry=str(target))
    writer = OutputWriter(out_dir, engine="Godot", source=str(target))
    return base.Context(target=target, detection=det, writer=writer,
                        work_dir=Path(out_dir).parent / "work", deep=deep)


# ---------------------------------------------------------------- 各项测试
def roundtrip(tmp, tag, **layout):
    """造包 -> 解析 -> 逐字节比对。"""
    pck = tmp / f"{tag}.pck"
    size = build_pck(pck, ITEMS, **layout)
    out_dir = tmp / f"{tag}_out"
    writer = OutputWriter(out_dir, engine="Godot", source=str(pck))
    rep = godot_pck.extract(pck, writer, origin=str(pck))
    idx = rep.index
    print(f"       pck {size} 字节, 版本 {idx.version}, 目录起点 0x{idx.dir_pos:X}, "
          f"file_base 0x{idx.file_base:X}, 基准: {idx.base_kind}")

    check(f"{tag}: 目录读出 {len(ITEMS)} 条", rep.total == len(ITEMS),
          f"实际 {rep.total}")
    check(f"{tag}: 写出 {len(ITEMS)} 个文件", rep.written == len(ITEMS),
          f"实际 {rep.written}, 跳过 {rep.skipped_broken}")
    check(f"{tag}: offset 基准自洽", idx.base_confident, idx.base_kind)
    check(f"{tag}: 没有文件被跳过", (rep.skipped_broken, rep.skipped_encrypted) == (0, 0),
          f"broken={rep.skipped_broken} enc={rep.skipped_encrypted}")
    check(f"{tag}: 报出了目录穿越", rep.escaped == ["res://../../evil.png"],
          str(rep.escaped))

    bad = []
    for rel, data in EXPECT.items():
        got = out_dir / kinds.classify(rel) / rel
        if not got.exists():
            bad.append(f"{rel} 缺失")
        elif got.read_bytes() != data:
            bad.append(f"{rel} 内容不一致")
    check(f"{tag}: {len(EXPECT)} 个文件逐字节一致", not bad, "; ".join(bad))

    files = _files_under(out_dir)
    check(f"{tag}: writer 记账 {len(EXPECT)} 个", writer.total() == len(EXPECT),
          f"实际 {writer.total()}")
    check(f"{tag}: 输出目录正好 {len(EXPECT)} 个文件", len(files) == len(EXPECT),
          f"实际 {len(files)}: {files}")
    return rep


def test_bad_pck(tmp):
    pck = tmp / "not_a_pck.pck"
    pck.write_bytes(b"NOPE" + b"\x00" * 200)
    try:
        godot_pck.read_index(pck)
        check("魔数不对时报错", False, "没有抛异常")
    except godot_pck.PckError as exc:
        check("魔数不对时报错", "不是 Godot PCK" in str(exc), str(exc))
        print(f"       报错内容: {exc}")


def test_encrypted_dir(tmp):
    pck = tmp / "enc_dir.pck"
    build_pck(pck, ITEMS[:2], version=2, reserved=16, dir_flags=1)
    try:
        godot_pck.read_index(pck)
        check("加密目录时报错", False, "没有抛异常")
    except godot_pck.PckError as exc:
        check("加密目录时报错", "加密" in str(exc), str(exc))
        print(f"       报错内容: {exc}")


def test_truncated(tmp):
    """尾部被砍掉的包：不许写出错位数据，只许少写。"""
    full = tmp / "full.pck"
    build_pck(full, ITEMS, version=2, reserved=64, offset_base="file_base")
    raw = full.read_bytes()
    cut = tmp / "cut.pck"
    cut.write_bytes(raw[:len(raw) - len(BIG) // 2])       # 砍掉大文件的一部分

    out_dir = tmp / "cut_out"
    writer = OutputWriter(out_dir, engine="Godot")
    rep = godot_pck.extract(cut, writer, origin=str(cut))
    check("截断包: 不崩，跳过越界条目", rep.skipped_broken > 0,
          f"broken={rep.skipped_broken} written={rep.written}")
    wrong = [rel for rel in _files_under(out_dir)
             if rel not in [kinds.classify(r) + "/" + r for r in EXPECT]]
    check("截断包: 只写该写的文件", not wrong, str(wrong))
    check("截断包: 报出基准不自洽", not rep.index.base_confident and bool(rep.warnings),
          str(rep.warnings))
    print(f"       写出 {rep.written} 个，跳过 {rep.skipped_broken} 个越界条目")


def test_file_flags(tmp):
    """记录级 flags：加密 / 标记删除的条目要跳过，分类过滤要生效。"""
    items = [("res://a.png", PNG, godot_pck.FILE_ENCRYPTED),
             ("res://b.ogg", OGG, godot_pck.FILE_REMOVAL),
             ("res://c.json", JSON, 0)]
    pck = tmp / "flags.pck"
    build_pck(pck, items, version=2, reserved=16, offset_base="file_base")

    out_dir = tmp / "flags_out"
    rep = godot_pck.extract(pck, OutputWriter(out_dir, engine="Godot"),
                            origin=str(pck))
    check("文件 flags: 加密 1 / 删除 1 / 写出 1",
          (rep.skipped_encrypted, rep.skipped_removed, rep.written) == (1, 1, 1),
          f"enc={rep.skipped_encrypted} removed={rep.skipped_removed} "
          f"written={rep.written}")
    check("文件 flags: 只写出没被标记的那个",
          _files_under(out_dir) == [f"{kinds.classify('c.json')}/c.json"],
          str(_files_under(out_dir)))

    # 分类过滤：只要音频
    only_audio = tmp / "flags_audio_out"
    writer = OutputWriter(only_audio, categories={"audio"}, engine="Godot")
    items2 = [("res://a.png", PNG, 0), ("res://b.ogg", OGG, 0)]
    build_pck(pck, items2, version=2, reserved=16, offset_base="file_base")
    rep = godot_pck.extract(pck, writer, origin=str(pck))
    check("分类过滤: 只要音频就只写 1 个",
          rep.written == 1 and rep.skipped_filtered == 1,
          f"written={rep.written} filtered={rep.skipped_filtered}")
    check("分类过滤: 输出目录里只有 ogg",
          _files_under(only_audio) == ["audio/b.ogg"], str(_files_under(only_audio)))


def test_engine(tmp):
    # 1) 目录里（子目录里）有 pck，外面还有散落资源
    game = tmp / "game_dir"
    (game / "bin").mkdir(parents=True)
    build_pck(game / "bin" / "game.pck", ITEMS, version=2, reserved=64)
    (game / "readme.txt").write_text("hello", encoding="utf-8")
    out = godot.extract(make_ctx(game, tmp / "eng_dir_out"))
    check("引擎: 目录形式写出 6+1", out.written == 7, f"实际 {out.written}")
    check("引擎: detail 有说明", "1 个 .pck" in out.detail, out.detail)
    for line in out.notes:
        print(f"       note: {line}")

    # 2) 工程形式（有 project.godot）
    proj = tmp / "project_form"
    (proj / "assets").mkdir(parents=True)
    (proj / "project.godot").write_text('[application]\nconfig/name="t"\n',
                                       encoding="utf-8")
    (proj / "assets" / "icon.png").write_bytes(PNG)
    out = godot.extract(make_ctx(proj, tmp / "eng_proj_out"))
    check("引擎: 工程形式收集 2 个", out.written == 2, f"实际 {out.written}")
    check("引擎: 工程形式 detail", "工程形式" in out.detail, out.detail)
    for line in out.notes:
        print(f"       note: {line}")

    # 3) 不解容器
    out = godot.extract(make_ctx(game, tmp / "eng_shallow_out", deep=False))
    check("引擎: 不解容器时只提示 + fallback",
          out.fallback and out.written == 0 and any("不解容器" in n for n in out.notes),
          f"written={out.written} notes={out.notes}")

    # 4) target 直接就是 .pck 文件
    solo = tmp / "solo.pck"
    build_pck(solo, ITEMS, version=2, reserved=16)
    out = godot.extract(make_ctx(solo, tmp / "eng_solo_out"))
    check("引擎: 直接给 .pck 写出 6", out.written == 6, f"实际 {out.written}")

    # 5) 坏 pck 不能把整次提取弄崩，散落资源照收
    broken = tmp / "broken_dir"
    broken.mkdir()
    (broken / "bad.pck").write_bytes(b"junk" * 100)
    (broken / "note.txt").write_text("x", encoding="utf-8")
    out = godot.extract(make_ctx(broken, tmp / "eng_broken_out"))
    check("引擎: 坏 pck 不崩且仍收散落文件", out.written == 1, f"实际 {out.written}")
    check("引擎: 坏 pck 有说明", any("bad.pck" in n for n in out.notes),
          str(out.notes))
    for line in out.notes:
        print(f"       note: {line}")

    # 6) 引擎注册表靠 KEY 自动加载
    check("引擎注册表自动加载了 godot", engines.get("godot") is godot,
          str(engines.LOAD_ERRORS))


def main():
    print(f"python {sys.version.split()[0]}  ({sys.executable})")
    tmp = Path(tempfile.mkdtemp(prefix="godot_selftest_"))
    print(f"临时目录: {tmp}")
    print("=" * 74)
    print("[1] 合成 pck 往返（6 种头部布局 / offset 基准）")
    print("=" * 74)
    roundtrip(tmp, "spec_v2", version=2, flags_field=True, file_base_field=True,
              reserved=16, offset_base="file_base")
    roundtrip(tmp, "godot4_real", version=2, flags_field=True, file_base_field=True,
              reserved=64, offset_base="file_base")
    roundtrip(tmp, "spec_v1", version=1, flags_field=True, file_base_field=False,
              reserved=0, offset_base="absolute")
    roundtrip(tmp, "godot3_real", version=1, flags_field=False, file_base_field=False,
              reserved=64, offset_base="absolute")
    roundtrip(tmp, "v2_absolute", version=2, flags_field=True, file_base_field=True,
              reserved=64, offset_base="absolute")
    roundtrip(tmp, "v2_nopad_len", version=2, flags_field=True, file_base_field=True,
              reserved=16, offset_base="file_base", padded_len=False)

    print("=" * 74)
    print("[2] 异常路径")
    print("=" * 74)
    test_bad_pck(tmp)
    test_encrypted_dir(tmp)
    test_truncated(tmp)
    test_file_flags(tmp)

    print("=" * 74)
    print("[3] 引擎模块 me/engines/godot.py")
    print("=" * 74)
    test_engine(tmp)

    print("=" * 74)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    for name in FAILED:
        print(f"  失败: {name}")
    print(f"合成的样本留在: {tmp}")
    return 1 if FAILED else 0


sys.exit(main())
