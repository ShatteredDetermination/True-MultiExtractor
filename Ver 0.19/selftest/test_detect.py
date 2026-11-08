# -*- coding: utf-8 -*-
"""引擎识别的合成样本测试。

不需要真实游戏文件 —— 自己造出「目录里有 app.asar」这类最小特征，
验证 detect 是否判对。别人拿到这个项目、没有样本目录时也能跑。

真实样本的端到端回归在 test_samples.py。
"""

import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.formats.ctf25 import container          # noqa: E402
from me.scanner import detect as detect_mod     # noqa: E402

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


def make_dir(tmp: Path, name: str, files: dict) -> Path:
    """按 {相对路径: 内容} 造一个目录。路径以 / 结尾表示空目录。"""
    d = tmp / name
    d.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = d / rel
        if rel.endswith("/"):
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content if isinstance(content, bytes) else b"x")
    return d


# ------------------------------------------------------------------ 目录识别
def test_dir_markers(tmp: Path):
    print("\n== 目录特征识别 ==")

    cases = [
        ("electron_asar", {"app.asar": b"x"}, "electron"),
        ("gamemaker_win", {"data.win": b"x"}, "gamemaker"),
        ("gamemaker_droid", {"game.droid": b"x"}, "gamemaker"),
        ("unity_player", {"UnityPlayer.dll": b"x"}, "unity"),
        ("godot_project", {"project.godot": b"x"}, "godot"),
        ("clickteam_dir", {"Data.ini": b"x", "Modules/": None,
                           "Modules/mmfs2.dll": b"x"}, "clickteam"),
        ("rpgmaker_ini", {"Game.ini": b"x"}, "rpgmaker"),
    ]
    for name, files, want in cases:
        d = make_dir(tmp, name, {k: (v or b"x") for k, v in files.items()})
        det = detect_mod.detect(d)
        check(det.engine == want,
              f"{name}: 期望 {want}，实际 {det.engine}")


def test_ext_markers(tmp: Path):
    print("\n== 后缀特征识别 ==")

    cases = [
        ("godot_pck", {"game.pck": b"GDPC" + b"\x00" * 60}, "godot"),
        ("rpgmaker_rgssad", {"Data/Game.rgssad": b"RGSSAD\x00\x01"},
         "rpgmaker"),
        ("rpgmaker_rgss3a", {"Game.rgss3a": b"RGSS3A\x00\x01"}, "rpgmaker"),
        ("unreal_pak", {"Content/Paks/x.pak": b"\x00" * 16}, "unreal"),
    ]
    for name, files, want in cases:
        d = make_dir(tmp, name, files)
        det = detect_mod.detect(d)
        check(det.engine == want,
              f"{name}: 期望 {want}，实际 {det.engine}")


def test_priority(tmp: Path):
    print("\n== 优先级 ==")

    # Electron 的特征排在前面，同时存在时要判成 Electron
    d = make_dir(tmp, "prio_electron", {"app.asar": b"x", "data.win": b"x"})
    det = detect_mod.detect(d)
    check(det.engine == "electron",
          f"app.asar 与 data.win 同时存在时应判 Electron，实际 {det.engine}")

    # 没有特征时不能瞎猜
    d = make_dir(tmp, "prio_none", {"readme.txt": b"hello"})
    det = detect_mod.detect(d)
    check(det.engine == "unknown",
          f"无特征目录应判 unknown，实际 {det.engine}")


# ------------------------------------------------------------------ CTF 结构
def build_ctf(tag: bytes = b"PAMU", data_size: int = 64) -> bytes:
    """造一个最小但结构合法的 CTF 容器（wwww 包装 + tag + 空 chunk 流）。"""
    buf = bytearray()
    buf += b"wwww"
    buf += b"\x49\x87\x47\x12"          # 无意义的 4 字节
    buf += struct.pack("<I", 32)         # header_size 必须是 32
    buf += struct.pack("<I", data_size)  # data_size
    buf += struct.pack("<I", 3)          # format_version
    buf += struct.pack("<I", 0)          # check
    buf += struct.pack("<I", 0)          # check
    buf += struct.pack("<I", 0)          # pack count
    # 补齐到 tag 位置
    while len(buf) < data_size - 32:
        buf += b"\x00"
    buf += tag                            # CCN 数据流起点
    buf += struct.pack("<HHII", 0x0302, 0, 3, 295)
    return bytes(buf)


def test_ctf_container(tmp: Path):
    print("\n== Clickteam 容器定位 ==")

    for tag, want_unicode in ((b"PAMU", True), (b"PAME", False)):
        raw = build_ctf(tag)
        loc = container.locate(raw)
        check(loc is not None, f"{tag.decode()}: 能定位到 CCN 流")
        if loc:
            check(loc.unicode == want_unicode,
                  f"{tag.decode()}: unicode 判定正确")
            check(loc.product_build == 295,
                  f"{tag.decode()}: build 解析正确（{loc.product_build}）")

    # 裸 CCN 流（没有 wwww 包装）
    bare = b"PAMU" + struct.pack("<HHII", 0x0302, 0, 3, 292)
    loc = container.locate(bare)
    check(loc is not None and loc.ccn_offset == 0 and loc.product_build == 292,
          "裸 PAMU 流也能定位")

    # header_size 不是 32 的 wwww 必须被拒
    bad = bytearray(build_ctf())
    struct.pack_into("<I", bad, 8, 64)
    check(container.locate(bytes(bad)) is None,
          "header_size != 32 的 wwww 被正确拒绝")

    # tag 位置不是已知 magic 的必须被拒
    bad2 = bytearray(build_ctf())
    bad2[32:36] = b"XXXX"
    check(container.locate(bytes(bad2)) is None,
          "tag 不是 PAMU/PAME/CRUF 时被正确拒绝")


def test_ctf_file(tmp: Path):
    print("\n== Clickteam 文件识别 ==")

    p = tmp / "fake.dat"
    p.write_bytes(build_ctf())
    det = detect_mod.detect(p)
    check(det.engine == "clickteam",
          f"合成的 .dat 应识别为 clickteam，实际 {det.engine}")

    # 目录形式（Data.ini + Modules）也要认
    d = make_dir(tmp, "ctf_dir", {"Data.ini": b"x", "Modules/": None,
                                  "game.dat": build_ctf()})
    det = detect_mod.detect(d)
    check(det.engine == "clickteam",
          f"Clickteam 目录应识别为 clickteam，实际 {det.engine}")


# ------------------------------------------------------------------ 二进制
def test_binary_sigs(tmp: Path):
    print("\n== 二进制特征 ==")

    cases = [
        ("enigma.exe", b"MZ" + b"\x00" * 100 + b".enigma1" + b"\x00" * 64,
         "enigma"),
        ("unity.exe", b"MZ" + b"\x00" * 100 + b"UnityPlayer.dll", "unity"),
        ("gm.exe", b"MZ" + b"\x00" * 100 + b"YYRunnerInterface", "gamemaker"),
    ]
    for name, data, want in cases:
        p = tmp / name
        p.write_bytes(data)
        det = detect_mod.detect(p)
        check(det.engine == want, f"{name}: 期望 {want}，实际 {det.engine}")

    # Enigma 壳 + 内层引擎要分开报
    p = tmp / "both.exe"
    p.write_bytes(b"MZ" + b"\x00" * 64 + b".enigma1" + b"\x00" * 32
                  + b"YYRunnerInterface")
    det = detect_mod.detect(p)
    check(det.engine == "enigma" and det.inner == "gamemaker",
          f"Enigma 壳 + 内层 GameMaker 要分开报"
          f"（engine={det.engine}, inner={det.inner}）")

    # Unreal pak 的魔数是 E1126F5A（小端），不是 5A6F12E1
    p = tmp / "x.pak"
    p.write_bytes(b"\x00" * 32 + bytes.fromhex("E1126F5A") + b"\x00" * 32)
    det = detect_mod.detect(p)
    check(det.engine == "unreal",
          f"pak 魔数 E1126F5A 应识别为 unreal，实际 {det.engine}")


def main() -> int:
    print("=" * 66)
    print("  引擎识别合成样本测试")
    print("=" * 66)

    with tempfile.TemporaryDirectory(prefix="me_detect_") as td:
        tmp = Path(td)
        test_dir_markers(tmp)
        test_ext_markers(tmp)
        test_priority(tmp)
        test_ctf_container(tmp)
        test_ctf_file(tmp)
        test_binary_sigs(tmp)

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
