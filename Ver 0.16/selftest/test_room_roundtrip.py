# -*- coding: utf-8 -*-
"""ROOM：Parse -> Serialize 的逐字节验收。

为什么不能只看「UTMT 能不能加载」
--------------------------------
ROOM 整块是**指针寻址**的：条目、四张表、每条表项都有自己的地址，UTMT 顺着
指针跳过去读定长字段，中间空着多少字节它不看。所以「指针全对、表项少写几个
字节」这种错它能 exit 0 通过，但实例属性已经丢了。

实测过这一幕（本模块写之前）：表项长度按「相邻表项地址差」猜，单条目的物件表
猜不出间距，退化成一个 4 字节记录 —— data.win 有 31 个房间的物件表只有一条，
31 个实例的属性就这样被截掉，UTMT 仍然 exit 0。

所以判定分三层，缺一层都发现不了这类错：

1. 长度：重建出来的 ROOM 数据长度必须与原 chunk 相同；
2. 指针：每个指针位置的 4 字节，在新文件里必须指向**同一个模型对象**
   （按对象身份比，不按地址比），且数值等于该对象在新布局里的地址；
3. 字节：把两边的指针位置都涂成同一标记后，其余字节必须完全相同
   —— 等于「除了搬家，一个字节都没动」。

外加两项：

* 交叉引用：表项里的物件/背景/代码**下标**必须落在 OBJT / BGND / CODE 的
  范围里（下标不需要重定位，但越界会让 UTMT 取不到资源）；
* 最小文件：只带 GEN8 + STRG + TPAG + OBJT + ROOM 单独喂 UTMT，尽早失败。

运行：
    python selftest/test_room_roundtrip.py
"""

import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.formats import gamemaker_ast as ast                      # noqa: E402
from me.formats import gamemaker_full as full                   # noqa: E402
from me.formats import gamemaker_serializer as ser              # noqa: E402
from me.formats.gamemaker_win import WinFile                    # noqa: E402

UTMT = ROOT / "tools" / "utmt" / "UndertaleModCli.exe"
DOWNLOADS = Path(r"C:\Users\Administrator\Downloads")
# data.win / data1.win 是 GMS1 布局；data2.win 是 GMS2（头 92 字节 + 图层）
SAMPLES = [DOWNLOADS / "data.win", DOWNLOADS / "data1.win",
           DOWNLOADS / "data2.win"]
MARK = b"\xcc\xcc\xcc\xcc"

PASS = 0
FAIL = 0


def check(ok, what, detail="") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"    [ok] {what}")
    else:
        FAIL += 1
        print(f"    [FAIL] {what}  {detail}")
    return ok


def u32(buf, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def labels(room) -> dict:
    """对象 id -> 人读的名字；报告差异时靠它定位到具体房间/表/表项。"""
    out = {id(room): "ROOM"}
    for i, it in enumerate(room.items):
        tag = it.name.text[:24] if it.name is not None else "?"
        out[id(it)] = f"Room[{i}] {tag}"
        for key, _off in ast.ROOM_LISTS:
            lp = it.lists.get(key)
            if lp is None:
                continue
            out[id(lp)] = f"Room[{i}].{key} 表"
            for j, r in enumerate(lp.items):
                out[id(r)] = f"Room[{i}].{key}[{j}]"
        if it.layers is None:
            continue
        out[id(it.layers)] = f"Room[{i}].Layers 表"
        for j, L in enumerate(it.layers.items):
            out[id(L)] = f"Room[{i}].layer[{j}]"
            for k, pl in enumerate(L.fills or []):
                out[id(pl)] = f"Room[{i}].layer[{j}].assets[{k}] 表"
                for m, r in enumerate(pl.items):
                    out[id(r)] = f"Room[{i}].layer[{j}].assets[{k}][{m}]"
    return out


def blank_sites(buf: bytes, offs) -> bytes:
    out = bytearray(buf)
    for o in offs:
        out[o:o + 4] = MARK
    return bytes(out)


def dump(buf: bytes, off: int, tag: str) -> None:
    lo = max(0, (off - 16) & ~15)
    hi = min(len(buf), off + 32)
    for at in range(lo, hi, 16):
        cells = " ".join(f"{b:02x}" for b in buf[at:at + 16])
        mark = "<<" if at <= off < at + 16 else "  "
        print(f"      {tag} +{at:#08x} {cells} {mark}")


def compare(src: Path) -> None:
    w = WinFile.load(src)
    data, _rep = full.assemble(w)
    built = ser.build(data)
    room = next(c for c in data.chunks if c.name == "ROOM")

    ps, pe = w.chunks["ROOM"].base, w.chunks["ROOM"].end
    ns, ne = built.chunk_span("ROOM")
    guard = built.guard_of("ROOM")
    # 源文件块尾可能挂着几个没人读的零字节（实测 data2 是 12 字节），
    # 模型把它们记成 tail 并丢掉，所以这里按「去掉 tail 之后」比
    tail = getattr(room, "tail", 0)
    old = w.raw[ps:pe - tail]
    new = built.data[ns:ne - guard]

    print(f"\n[{src.name}]  ROOM 原 {pe - ps:,} 字节"
          f"（末尾零补位 {tail}）-> 新 {len(new):,} 字节（守卫 {guard}）")

    # ---- 1. 长度 ----
    if not check(len(old) == len(new), "长度与原 chunk 一致（去掉末尾零补位）",
                 f"{len(new) - len(old):+,}"):
        return data, room

    # ---- 2. 指针：位置、目标对象、数值 ----
    lab = labels(room)
    old_sites = dict(room.sites)
    new_sites = {}
    for off, oid in built.refs:
        if ns <= off < ne - guard:
            new_sites[off - ns] = oid
    miss = sorted(set(old_sites) - set(new_sites))
    extra = sorted(set(new_sites) - set(old_sites))
    check(not miss, "原文件的指针位置全部写出",
          f"缺 {len(miss)} 处，例如 +{miss[0]:#x} "
          f"{lab.get(id(old_sites[miss[0]]), '?')}" if miss else "")
    check(not extra, "新文件没有多出指针位置",
          f"多 {len(extra)} 处，例如 +{extra[0]:#x}" if extra else "")

    wrong_obj = [(o, old_sites[o], new_sites[o]) for o in sorted(old_sites)
                 if o in new_sites and id(old_sites[o]) != new_sites[o]]
    check(not wrong_obj, f"{len(old_sites):,} 处指针都指向同一个对象",
          f"{len(wrong_obj)} 处不一致，例如 +{wrong_obj[0][0]:#x}"
          if wrong_obj else "")

    # 数值核对：拿模型对象直接查新地址（字符串写的是正文地址，即记录 + 4）
    wrong_val = []
    for o, obj in room.sites:
        want = built.addr_of(obj) + (4 if isinstance(obj, ast.GmString) else 0)
        got = u32(new, o)
        if got != want:
            wrong_val.append((o, got, want, lab.get(id(obj), "?")))
    check(not wrong_val, "指针数值 == 目标对象在新布局里的地址",
          f"{len(wrong_val)} 处不符，例如 +{wrong_val[0][0]:#x} "
          f"{wrong_val[0][3]} {wrong_val[0][1]:,} != {wrong_val[0][2]:,}"
          if wrong_val else "")

    # ---- 3. 字节：涂掉指针后必须完全相同 ----
    a = blank_sites(old, old_sites)
    b = blank_sites(new, new_sites)
    diffs = [i for i in range(len(a)) if a[i] != b[i]]
    if not check(not diffs, f"涂掉 {len(old_sites):,} 处指针后 0 字节差异",
                 f"{len(diffs)} 字节不同，首个 +{diffs[0]:#x}"
                 if diffs else ""):
        show = diffs[:8]
        for off in show:
            print(f"      +{off:#08x}  原 {a[off]:02x}  新 {b[off]:02x}")
        dump(a, diffs[0], "原")
        dump(b, diffs[0], "新")

    # ---- 附：未登记的地址型字（只报告，用于人工复核）----
    # 表项里没有指针（UTMT 的 Background/View/GameObject/Tile 序列化里没有
    # ReadUndertaleObjectPointer），所以这些字只能是实例 ID、坐标、索引。
    # 下面按「哪张表的第几个字段」归类，一眼能看出是哪种数字。
    lo = min(w.chunks[k].base for k in w.order if w.chunks[k].size)
    hi = len(w.raw)
    odd = [(o, u32(new, o)) for o in range(0, len(new) - 3, 4)
           if o not in new_sites and lo <= u32(new, o) < hi]
    tally = {}
    for i, it in enumerate(room.items):
        for key, _off in ast.ROOM_LISTS:
            lp = it.lists.get(key)
            if lp is None:
                continue
            for r in lp.items:
                for f in range(0, len(r.data) - 3, 4):
                    if lo <= u32(r.data, f) < hi:
                        tally[(key, f)] = tally.get((key, f), 0) + 1
    top = sorted(tally.items(), key=lambda kv: -kv[1])[:5]
    print(f"    未登记的地址型字 {len(odd):,} 个（表项字段值，不参与判定）")
    if top:
        print("      按字段: " + "  ".join(f"{k}[+{f}] {n:,}"
                                           for (k, f), n in top))
    return data, room


# 表项里的交叉引用是**下标**，不是绝对地址，所以不用重定位 —— 但需要它们是
# 合的下标，否则 UTMT 会拿越界下标去取资源。这里按 UTMT 的字段表逐项核对：
# ``-1``（0xFFFFFFFF）表示「没有」。
# Tiles 背景下标 -> BGND；GameObjects 物件下标 -> OBJT；
# CreationCode / PreCreateCode -> CODE。房间不引用 TPAG（那是 SPRT 的事）。
ROOM_INDEX_FIELDS = (
    ("GameObjects", 8, "ObjectDefinition", "OBJT"),
    ("GameObjects", 16, "CreationCode", "CODE"),
    ("GameObjects", 36, "PreCreateCode", "CODE"),
    ("Tiles", 8, "BackgroundIndex", "BGND"),
    ("Backgrounds", 8, "BackgroundIndex", "BGND"),
)


def crossref(data, room) -> None:
    """ROOM -> OBJT / BGND / CODE 的交叉引用：下标必须都落在目标块的范围里。"""
    counts = {}
    for c in data.chunks:
        if c.name == "OBJT":
            counts["OBJT"] = len(c.items)
        elif c.name == "CODE":
            counts["CODE"] = getattr(c, "count", None)
        elif c.name == "BGND":
            counts["BGND"] = len(getattr(c, "entries", ()))
    for key, field, label, target in ROOM_INDEX_FIELDS:
        if key == "GameObjects" and field >= room.sizes["GameObjects"]:
            continue                        # 老版本物件表没有 PreCreateCode
        n = counts.get(target)
        if not n:
            continue
        used = bad = 0
        hi = -1
        for it in room.items:
            lp = it.lists.get(key)
            if lp is None:
                continue
            for r in lp.items:
                v = u32(r.data, field)
                if v == 0xFFFFFFFF:
                    continue
                used += 1
                hi = max(hi, v)
                if v >= n:
                    bad += 1
        check(not bad, f"{key}[+{field}] {label} 全在 {target}（0..{n - 1}）里",
              f"{bad} 个越界，最大 {hi}")


def mini(src: Path) -> None:
    """只带 GEN8 + STRG + TPAG + OBJT + ROOM 的最小文件，喂给 UTMT。

    STRG 是 OBJT/ROOM 名字槽位的唯一来源，必须带上；TPAG 是物件表的精灵索引
    空间（纯索引，没有指针）。这几个块凑齐，ROOM 的每一类指针都有落点。
    """
    w = WinFile.load(src)
    names = {"STRG", "TPAG", "OBJT", "ROOM"}
    blob = full.build_bytes_for(w, names)
    out = Path(r"C:\Users\Administrator\AppData\Local\Temp") / \
        f"room_only_{src.stem}.win"
    out.write_bytes(blob)
    print(f"\n[{src.name}]  最小文件 {len(blob):,} 字节"
          f"（GEN8 + {' + '.join(sorted(names))}）")
    if not UTMT.exists():
        print("    （没有 UTMT，跳过）")
        return
    res = subprocess.run([str(UTMT), "load", str(out), "-v"],
                         capture_output=True, text=True, errors="replace",
                         encoding="utf-8")
    text = (res.stdout or "") + (res.stderr or "")
    # UTMT -v 的每块行形如「Chunk X: <n> objects」或「Reading chunk X」
    got = {}
    for ln in text.splitlines():
        ln = ln.strip()
        for name in names | {"GEN8"}:
            if ln.endswith(f"chunk {name}"):
                got[name] = got.get(name, 0) + 1
    err = [ln.strip() for ln in text.splitlines()
           if "at 0" in ln or "Exception" in ln or "never read" in ln]
    check(res.returncode == 0, f"UTMT 加载最小文件（exit {res.returncode}）",
          err[-1][:120] if err else "")
    want = {"STRG", "TPAG", "OBJT", "ROOM"}
    check(want <= set(got), "UTMT 读到 STRG / TPAG / OBJT / ROOM",
          f"读到 {sorted(got)}")
    if err:
        for ln in err[-3:]:
            print(f"      {ln[:140]}")


def main() -> int:
    print("=" * 74)
    print("  V0.15E ROOM：Parse -> Serialize 逐字节验收")
    print("=" * 74)
    for s in SAMPLES:
        if not s.exists():
            print(f"\n[{s.name}] 样本不在，跳过")
            continue
        try:
            data, room = compare(s)
            crossref(data, room)
            mini(s)
        except Exception as exc:                    # noqa: BLE001
            check(False, f"{s.name} 抛异常", f"{type(exc).__name__}: {exc}")
    print("\n" + "=" * 74)
    print(f"  PASS {PASS}   FAIL {FAIL}")
    print("=" * 74)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
