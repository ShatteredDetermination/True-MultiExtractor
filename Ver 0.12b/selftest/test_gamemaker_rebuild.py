# -*- coding: utf-8 -*-
"""⚠ 实验性重建模块的自测。

关键点：**预言机是注入的**，所以这里不用真跑 UTMT —— 用一个假预言机按
剧本吐 UTMT 的报错文本，就能把「反推循环」的逻辑单独测干净。

真正用 UTMT 做预言机的效果由 test_gamemaker_fallback.py 之外的实测覆盖
（见 FORMATS.md 3.3），那个依赖外部工具、不适合放进日常自测。
"""

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from me.formats import gamemaker_rebuild as rb                # noqa: E402

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


# ------------------------------------------------------------------ 样本
def make_container(chunks):
    gen8 = b"\x00" * 64
    out = bytearray(b"FORM\x00\x00\x00\x00GEN8" + struct.pack("<I", len(gen8)) + gen8)
    for name, body in chunks:
        out += name.encode() + struct.pack("<I", len(body)) + body
    struct.pack_into("<I", out, 4, len(out) - 8)
    return bytes(out)


def make_list_chunk(data_start: int, entries: list, ptrs: list) -> bytes:
    """造一个列表型 chunk：count + 指针 + 条目。

    entries 是 [(条目起点相对 chunk 数据的偏移, 内容)]，ptrs 是写进指针
    数组的值（**绝对文件偏移**，故意可以写错来模拟损坏）。
    """
    count = len(ptrs)
    head = struct.pack("<I", count) + b"".join(struct.pack("<I", p) for p in ptrs)
    body = bytearray(head)
    for rel, blob in entries:
        if len(body) < rel:
            body += b"\x00" * (rel - len(body))
        body[rel:rel + len(blob)] = blob
    return bytes(body)


def scenario(pointer_values):
    """造一个含 SPRT 列表的文件；pointer_values 决定指针数组写什么。

    返回 (data, sprt_data_start, 正确答案)
    """
    # 先算出各条目的真实偏移：条目区在 count+指针之后
    count = len(pointer_values)
    head_len = 4 + 4 * count
    entries = []
    offsets = []
    cur = head_len
    for i in range(count):
        size = 16 + i * 8          # 每个条目长度不同，好区分
        offsets.append(cur)
        entries.append((cur, bytes([0x40 + i]) * size))
        cur += size
    body = make_list_chunk(0, entries, pointer_values)
    data = make_container([("SPRT", body), ("AUDO", b"\x00" * 32)])
    # SPRT 数据区起点
    gen8 = 64
    sprt_off = 16 + gen8
    data_start = sprt_off + 8
    truth = [data_start + o for o in offsets]
    return data, data_start, truth


def broken_scenario(bad_indices=(1, 3)):
    """造一个「一部分指针好、一部分坏」的文件。

    为什么不能全坏：诊断有个刻意的保守判据 —— 指针**不到一半**落在 chunk
    内就认为是我们把布局读错了（新版 GameMaker 里 VARI / FUNC / LANG 就
    不是这个布局），直接不报。所以测试样本得像真文件那样「多数是好的」。
    """
    _d, _ds, truth = scenario([0, 0, 0, 0])
    vals = list(truth)
    for i in bad_indices:
        vals[i] = 0
    return scenario(vals)


# ------------------------------------------------------------------ 测试
def test_diagnose_finds_dangling():
    print("\n---- 诊断：找出越界指针 ----")
    data, data_start, truth = broken_scenario()
    diag = rb.diagnose(data)
    check(diag.chain_ok, f"chunk 链完整（{diag.chain_note}）")
    check(len(diag.problems) == 1, f"发现 {len(diag.problems)} 个有问题的 chunk")
    p = diag.problems[0]
    check(p.chunk == "SPRT", f"是 SPRT（{p.chunk}）")
    check(p.dangling == [1, 3], f"坏的是下标 [1, 3]（{p.dangling}）")

    # 用正确的指针就不该报问题
    data2, _ds, truth2 = scenario(truth)
    check(rb.diagnose(data2).all_ok, "指针正确时不报问题")

    # 非列表 chunk 不参与
    data3 = make_container([("OPTN", b"\x00" * 40)])
    check(rb.diagnose(data3).all_ok, "含非列表 chunk 的文件也不误报")

    # 全坏 -> 多半是布局读错了，刻意不报（实测 LANG/VARI/FUNC 就是这种情况）
    data4, _ds4, _t4 = scenario([0, 0, 0, 0])
    check(rb.diagnose(data4).all_ok,
          "指针全部越界时不报（判据：不到一半合理 = 布局读错了，别乱修）")


def test_rebuild_with_fake_oracle():
    print("\n---- 重建：用假预言机反推 ----")
    data, data_start, truth = broken_scenario((1, 3))
    state = {"calls": 0}

    def fake_oracle(blob):
        """模拟 UTMT：找出第一个值不对的指针，报出它应该是什么。"""
        state["calls"] += 1
        diag = rb.diagnose(blob)
        if diag.all_ok:
            return "Quick Information:\nBytecode version - 16\n248 Sprites"
        p = diag.problems[0]
        i = p.dangling[0]
        return (f"at 0 while reading item in a list in chunk {p.chunk}\n"
                f"Reading misaligned at {truth[i]:08X}, "
                f"realigning back to {p.values[0]:08X}")

    res = rb.rebuild(data, fake_oracle)
    check(res.ok, "重建返回了文件")
    check(res.loaded, "假预言机报告已可读")
    final = list(struct.unpack_from("<4I", res.data, data_start + 4))
    check(final == truth, f"指针全部反推正确\n        得到 {[hex(x) for x in final]}"
                          f"\n        应为 {[hex(x) for x in truth]}")
    check(len(res.data) == len(data), "长度严格不变（绝对偏移才不会错位）")
    check(rb.diagnose(res.data).all_ok, "重建后诊断通过")


def test_rebuild_uses_oracle_chunk_name():
    print("\n---- 重建：目标 chunk 必须以 UTMT 报的为准 ----")
    # 诊断顺序里把 SPRT 放前面，但 UTMT 报的是别的 —— 应该听 UTMT 的。
    # 实测踩过：诊断把 LANG 排在第一个（那压根不是指针数组），
    # 结果跑去修 LANG，而 UTMT 报的明明是 FONT 的地址。
    data, data_start, truth = broken_scenario((1, 3))
    seen = []

    def oracle(blob):
        diag = rb.diagnose(blob)
        if diag.all_ok:
            return "Bytecode version - 16"
        p = diag.problems[0]
        seen.append(p.chunk)
        i = p.dangling[0]
        return (f"in chunk {p.chunk}\nReading misaligned at {truth[i]:08X}, "
                f"realigning back to {p.values[0]:08X}")

    res = rb.rebuild(data, oracle)
    check(res.loaded, "重建成功")
    check(seen and seen[0] == "SPRT", f"每轮都定位到了 SPRT（{seen}）")


def test_rebuild_partial():
    print("\n---- 重建：只坏一个也要能修 ----")
    data, ds2, truth = broken_scenario((2,))
    calls = {"n": 0}

    def fake_oracle(blob):
        calls["n"] += 1
        diag = rb.diagnose(blob)
        if diag.all_ok:
            return "Bytecode version - 16"
        p = diag.problems[0]
        i = p.dangling[0]
        return (f"in chunk {p.chunk}\n"
                f"Reading misaligned at {truth[i]:08X}, "
                f"realigning back to {p.values[0]:08X}")

    res = rb.rebuild(data, fake_oracle)
    final = list(struct.unpack_from("<4I", res.data, ds2 + 4))
    check(final == truth, f"修好且其它指针没被动：{[hex(x) for x in final]}")
    check(calls["n"] <= 3, f"只跑了 {calls['n']} 轮（不该反复来回）")


def test_rebuild_gives_up_cleanly():
    print("\n---- 重建：修不动时要老实放弃，不能产出坏文件 ----")
    data, _ds, _truth = broken_scenario()

    def bad_oracle(blob):
        # 反推出的位置在 chunk 外 —— 说明不只是指针写错
        return ("in chunk SPRT\n"
                "Reading misaligned at FFFFFFF0, realigning back to 00000000")

    res = rb.rebuild(data, bad_oracle)
    check(not res.loaded, "没有谎报成功")
    check(any("落在 chunk 外" in n for n in res.notes),
          "说明了放弃的原因（反推位置在 chunk 外）")

    def dead_oracle(blob):
        return "Reading out of chunk bounds\nat 00000000 while reading chunk FONT"

    res2 = rb.rebuild(data, dead_oracle)
    check(not res2.loaded, "预言机给不出可用信息时不谎报成功")

    # 链不完整的文件直接拒绝，不该去动它
    broken = bytearray(data)
    struct.pack_into("<I", broken, 16 + 64 + 4, 999999)
    res3 = rb.rebuild(bytes(broken), lambda b: "")
    check(not res3.ok or not res3.loaded, "chunk 链不完整时不产出可用结果")
    check(any("链不完整" in n for n in res3.notes), "说明了是链的问题")


def test_rebuild_noop_when_healthy():
    print("\n---- 重建：文件没病就别乱动 ----")
    data, _ds, _truth = scenario([0, 0, 0, 0])
    _d, ds, truth = scenario([0, 0, 0, 0])
    healthy, _ds2, _t2 = scenario(truth)
    calls = {"n": 0}

    def oracle(blob):
        calls["n"] += 1
        return "Bytecode version - 16"

    res = rb.rebuild(healthy, oracle)
    check(res.ok and res.loaded, "健康文件直接通过")
    check(calls["n"] == 0, f"一次预言机都没调用（{calls['n']}）")
    check(res.data == healthy, "内容原样返回，一个字节没改")
    check(any("没有发现越界指针" in n for n in res.notes), "说明了不需要重建")


def test_regex():
    print("\n---- 报错文本解析 ----")
    cases = [
        ("Reading misaligned at 001EAD30, realigning back to 00F4933F",
         (0x1EAD30, 0xF4933F)),
        ("Reading misaligned at 001EAD30,realigning back to 001ED790",
         (0x1EAD30, 0x1ED790)),
    ]
    for text, expect in cases:
        m = rb.MISALIGNED.search(text)
        check(m is not None and (int(m.group(1), 16), int(m.group(2), 16)) == expect,
              f"解析 {text[:44]}... -> {expect!r}")
    check(rb.MISALIGNED.search("Unhandled exception: something else") is None,
          "无关报错不会误匹配")


def main() -> int:
    print("=" * 66)
    print("  ⚠ 实验性重建模块自测")
    print("=" * 66)
    test_diagnose_finds_dangling()
    test_rebuild_with_fake_oracle()
    test_rebuild_uses_oracle_chunk_name()
    test_rebuild_partial()
    test_rebuild_gives_up_cleanly()
    test_rebuild_noop_when_healthy()
    test_regex()

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
