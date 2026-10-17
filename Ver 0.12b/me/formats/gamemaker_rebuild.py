# -*- coding: utf-8 -*-
"""⚠ 实验性功能 —— GameMaker data.win 重建。

这个模块**不稳定、也不保证能修好**。默认不启用，要看它干活得显式打开
（见 me/engines/gamemaker.py 里的 ME_EXPERIMENTAL_REBUILD）。
出问题不要当成 bug 报，它本来就是在跟已经被破坏的文件较劲。

它解决什么
----------
`data.win` 里每个「列表型」chunk 的结构是::

    u32 count
    u32 pointers[count]     <- **绝对文件偏移**
    条目们（连续存放）

UTMT 读的时候要求第 i+1 个条目正好接在第 i 个结束的位置，否则抛::

    Reading misaligned at <上一个条目结束的位置>, realigning back to <指针里写的值>

而这条报错恰好把**正确的位置**告诉了我们 —— `Reading misaligned at` 后面那个
地址就是下一个条目的真实起点。于是可以拿 UTMT 当**预言机**，一轮修一个指针，
迭代到指针表连续为止。

实测（Downloads/data.win，61MB）：FONT chunk 20 个指针里有 6 个指向 16MB 处
（那些条目被物理挪到了字符串表区间），跑 6 轮全部重建成功，20/20 落在 chunk
内、严格递增，而且**反推出来的值和原本存活的合法指针完全自洽** ——
不是"UTMT 恰好接受"，是真的算对了。

它做不到什么（**重要**）
------------------------
修完指针不等于文件就能读了。上面那个文件接下来死在::

    UndertaleSerializationException: Padding error in STRG

因为 STRG 的声明长度比它的实际内容长 278,465 字节（多出来的就是那 6 个字体
条目）。要修这个只能**缩短 STRG**，而一旦缩短，后面 TXTR / AUDO 全体前移，
文件里所有大于该位置的绝对偏移都要减掉同一个差值 —— 那是个全局重写器，
本模块**没有实现**（实现了也不该默认跑：文件里还有大量碰巧落在该区间的非
偏移数值，全局扫描改它们会引入新的损坏）。

所以本模块的定位是：**诊断 + 能修的部分修掉**，修不完就老实返回失败，
让引擎继续往下一层（抓取兜底）退。
"""

import re
import struct
import sys as _sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# 允许直接当脚本跑：python me/formats/gamemaker_rebuild.py <文件>
# 那样的话项目根不在 sys.path 里，import me 会失败。
if __package__ in (None, ""):
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from me.formats.gamemaker_repair import REPAIRABLE, walk_chunks  # noqa: E402

# UTMT 报错：「条目应该从这里开始, 但指针里写的是那个」
MISALIGNED = re.compile(
    r"Reading misaligned at ([0-9A-Fa-f]+),\s*realigning back to ([0-9A-Fa-f]+)")

# 迭代上限。正常情况下坏指针就那么几个；给大一点是为了防病态文件把我们
# 拖进无限循环（每轮都要跑一次 UTMT，很贵）。
DEFAULT_MAX_ROUNDS = 12


# ------------------------------------------------------------------ 结构
@dataclass
class ListProblem:
    """一个列表型 chunk 的指针问题。"""
    chunk: str
    count: int
    data_start: int
    data_end: int
    dangling: List[int] = field(default_factory=list)   # 数组下标
    values: List[int] = field(default_factory=list)     # 那些越界的值

    @property
    def ok(self) -> bool:
        return not self.dangling


@dataclass
class Diagnosis:
    """整个文件的体检结果。"""
    chunks: List[Tuple[str, int, int, int]] = field(default_factory=list)
    chain_ok: bool = False
    chain_note: str = ""
    problems: List[ListProblem] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return self.chain_ok and all(p.ok for p in self.problems)

    def lines(self) -> List[str]:
        out = []
        out.append(f"chunk 链: {self.chain_note}")
        if not self.problems:
            out.append("没有发现列表型 chunk 的指针问题")
        for p in self.problems:
            if p.ok:
                out.append(f"{p.chunk}: {p.count} 个指针全部正常")
            else:
                vals = ", ".join(f"{v:,}" for v in p.values[:6])
                more = f" 等 {len(p.values)} 个" if len(p.values) > 6 else ""
                out.append(f"{p.chunk}: {p.count} 个指针里 {len(p.dangling)} 个越界"
                           f"（下标 {p.dangling[:8]}；指向 {vals}{more}）")
                out.append(f"    chunk 数据区 [{p.data_start:,}, {p.data_end:,})")
        return out


def list_problem(data, name: str, off: int, ln: int) -> Optional[ListProblem]:
    """看某个 chunk 的指针数组里有没有越界的。

    只对「大多数指针落在 chunk 内」的情况报问题 —— 这是个刻意的保守判据：
    新一点的 GameMaker 里有些 chunk（VARI / FUNC / LANG …）**不是**
    「count + 绝对指针 + 条目」这个布局，硬按这个读会得到一堆明显荒谬的
    数值（全 0、4294967295、指向别的 chunk）。实测过：那样诊断会把
    LANG 报成「1 个指针里 1 个越界」，然后重建就去修一个根本不是指针
    数组的 chunk，白跑一轮。
    """
    if name not in REPAIRABLE or ln < 4:
        return None
    d0 = off + 8
    end = off + 8 + ln
    count = struct.unpack_from("<I", data, d0)[0]
    # 数组本身必须放得下，否则是我们理解错了布局，别乱报
    if count == 0 or d0 + 4 + 4 * count > end:
        return None
    ptrs = struct.unpack_from(f"<{count}I", data, d0 + 4)
    inside = sum(1 for p in ptrs if d0 <= p < end)
    if inside * 2 < count:          # 不到一半合理 -> 多半是布局读错了
        return None
    prob = ListProblem(name, count, d0, end)
    for i, p in enumerate(ptrs):
        if not (d0 <= p < end):
            prob.dangling.append(i)
            prob.values.append(p)
    return prob


def diagnose(data) -> Diagnosis:
    """走一遍 chunk 链，检查每个列表型 chunk 的指针。"""
    diag = Diagnosis()
    try:
        chunks = walk_chunks(data)
    except Exception as exc:  # noqa: BLE001
        diag.chain_note = f"走不通（{exc}）"
        return diag
    diag.chunks = chunks
    if not chunks:
        diag.chain_note = "一个 chunk 都没走出来"
        return diag
    end = chunks[-1][3]
    diag.chain_ok = end == len(data)
    diag.chain_note = (f"{len(chunks)} 个 chunk，"
                       + ("一路走到文件末尾（容器完整）" if diag.chain_ok
                          else f"停在 {end:,}，文件是 {len(data):,}（不完整）"))
    for name, off, ln, _e in chunks:
        prob = list_problem(data, name, off, ln)
        if prob is not None and not prob.ok:
            diag.problems.append(prob)
    return diag


# ------------------------------------------------------------------ 预言机
def utmt_oracle(exe, work_dir: Path, timeout: int = 1800) -> Callable:
    """造一个「把文件喂给 UTMT、把它的输出拿回来」的函数。

    UTMT 的报错文本就是这个模块的全部信息来源，所以这里必须用真实调用。
    """
    import subprocess

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    scratch = work_dir / "rebuild_oracle.win"
    counter = {"n": 0}

    def ask(data: bytes) -> str:
        counter["n"] += 1
        scratch.write_bytes(data)
        try:
            r = subprocess.run([str(exe), "info", str(scratch)],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"<oracle 调用失败: {exc}>"
        return (r.stdout or "") + (r.stderr or "")

    return ask


# ------------------------------------------------------------------ 重建
@dataclass
class RebuildResult:
    data: Optional[bytes] = None
    rounds: int = 0
    fixed: int = 0
    notes: List[str] = field(default_factory=list)
    loaded: bool = False       # 预言机最后是否报告成功

    @property
    def ok(self) -> bool:
        return self.data is not None


# UTMT 报错里会出现 chunk 名，尽量都认出来
IN_CHUNK = re.compile(
    r"(?:in chunk|while reading chunk|Padding error in|Error in chunk)\s+"
    r"([A-Z0-9]{4})")


def failing_chunk_names(text: str) -> List[str]:
    """从 UTMT 输出里抠出它读失败的 chunk 名（可能有多个）。"""
    out: List[str] = []
    for m in IN_CHUNK.finditer(text):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def find_problem(data, name: str) -> Optional[ListProblem]:
    """按名字取某个 chunk 当前的指针问题（每轮都重新算，因为文件在变）。"""
    for cname, off, ln, _e in walk_chunks(data):
        if cname == name:
            return list_problem(data, cname, off, ln)
    return None


def rebuild(data: bytes, oracle: Callable, max_rounds: int = DEFAULT_MAX_ROUNDS,
            chunk_hint: Optional[str] = None) -> RebuildResult:
    """拿 UTMT 当预言机，把越界的列表指针一个个反推回正确值。

    data   —— 原始文件内容（不会被修改）
    oracle —— 收 bytes、返回 UTMT 输出文本的函数
    """
    res = RebuildResult()
    data = bytearray(data)

    diag = diagnose(data)
    if not diag.chain_ok:
        res.notes.append(f"chunk 链不完整（{diag.chain_note}），重建没有意义")
        return res
    if diag.all_ok:
        res.notes.append("没有发现越界指针，不需要重建")
        res.data = bytes(data)
        res.loaded = True      # 本来就健康，等同于「可读」
        return res

    res.notes.append(
        "诊断到 " + "、".join(f"{p.chunk}({len(p.dangling)}/{p.count})"
                             for p in diag.problems))

    for rnd in range(1, max_rounds + 1):
        text = oracle(bytes(data))
        m = MISALIGNED.search(text)
        if not m:
            res.rounds = rnd - 1
            if _looks_loaded(text):
                res.loaded = True
                res.notes.append(f"第 {rnd} 轮：UTMT 不再报错，文件已可读")
            else:
                first = _first_error(text)
                res.notes.append(f"第 {rnd} 轮：指针已连续，但文件还有别的问题"
                                 + (f" —— {first}" if first else ""))
            break

        should, said = int(m.group(1), 16), int(m.group(2), 16)

        # 目标 chunk **必须以 UTMT 报的为准**，不能用我们自己的诊断顺序。
        # 实测踩过：诊断把 LANG 排在第一个（其实那压根不是指针数组），
        # 结果跑去修 LANG，而 UTMT 报的明明是 FONT 的地址。
        names = failing_chunk_names(text)
        if chunk_hint:
            names = [chunk_hint] + [n for n in names if n != chunk_hint]
        prob = None
        for nm in names:
            cand = find_problem(data, nm)
            if cand is not None and said in cand.values:
                prob = cand
                break
        if prob is None:
            # UTMT 没点名，或点名的 chunk 里找不到这个值 —— 退回逐个试
            for cand_name in [p.chunk for p in diag.problems]:
                cand = find_problem(data, cand_name)
                if cand is not None and said in cand.values:
                    prob = cand
                    break
        if prob is None:
            res.notes.append(
                f"第 {rnd} 轮：报错里的 {said:#x} 在已知的指针表里都找不到"
                f"（UTMT 点名 {names or '无'}），停")
            break

        if not (prob.data_start <= should < prob.data_end):
            res.notes.append(
                f"第 {rnd} 轮：{prob.chunk} 反推出的位置 {should:#x} 落在 chunk 外"
                f"（数据区 [{prob.data_start:#x}, {prob.data_end:#x})），"
                f"说明不只是指针写错，停")
            break

        ptr_base = prob.data_start + 4
        ptrs = list(struct.unpack_from(f"<{prob.count}I", data, ptr_base))
        # 每轮只改**一个**槽位。
        # 别贪心把「值等于 said 的槽位」全改掉：UTMT 报的是读取顺序上
        # 第一个不对的那个，而多个槽位完全可能写着同一个坏值（比如全 0），
        # 那样一次全改会把后面几个槽位一起填成同一个值 —— 实测踩过。
        cands = [i for i, p in enumerate(ptrs) if p == said]
        dang = [i for i in cands
                if not (prob.data_start <= ptrs[i] < prob.data_end)]
        pick = (dang or cands)[:1]
        for i in pick:
            struct.pack_into("<I", data, ptr_base + 4 * i, should)
        res.fixed += len(pick)
        res.rounds = rnd
        res.notes.append(
            f"第 {rnd} 轮：{prob.chunk} 槽位 {pick} 由 {said:#x} 修正为 {should:#x}"
            + (f"（值相同的槽位共 {len(cands)} 个，每轮只改一个）"
               if len(cands) > 1 else ""))
    else:
        res.notes.append(f"达到 {max_rounds} 轮上限，仍没收敛")

    res.data = bytes(data)
    return res


def _looks_loaded(text: str) -> bool:
    """从输出里判断 UTMT 到底读成功没有。"""
    if "exception" in text.lower() or "error" in text.lower():
        return False
    return ("Sprites" in text or "Quick Information" in text
            or "Bytecode version" in text)


def _first_error(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if not s or "Trying to load" in s or s.startswith("[WARNING]"):
            continue
        if "exception" in s.lower() or "error" in s.lower():
            return s[:160]
    return ""


# ------------------------------------------------------------------ CLI
def _main(argv=None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        description="⚠ 实验性：诊断并尝试重建 GameMaker data.win")
    ap.add_argument("target", help="data.win / game.droid 等")
    ap.add_argument("-o", "--out", help="重建结果写到哪（默认 <原名>.rebuilt.win）")
    ap.add_argument("--utmt", help="UTMT CLI 路径（默认用 tools/ 里的）")
    ap.add_argument("--diagnose-only", action="store_true", help="只诊断不重建")
    ap.add_argument("--rounds", type=int, default=DEFAULT_MAX_ROUNDS)
    args = ap.parse_args(argv)

    src = Path(args.target)
    if not src.is_file():
        print(f"不是文件: {src}")
        return 2
    data = src.read_bytes()

    print("=" * 70)
    print("  ⚠ 实验性功能：data.win 重建")
    print("=" * 70)
    print(f"文件: {src}   {len(data):,} 字节\n")

    diag = diagnose(data)
    print("--- 诊断 ---")
    for line in diag.lines():
        print("  " + line)
    if args.diagnose_only or diag.all_ok:
        return 0 if diag.all_ok else 1

    exe = args.utmt
    if not exe:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            from me.tools import paths as tool_paths
            exe = tool_paths.find("utmt")
        except Exception:  # noqa: BLE001
            exe = None
    if not exe:
        print("\n找不到 UTMT，没法做预言机。用 --utmt 指定路径。")
        return 2

    print(f"\n--- 重建（预言机: {Path(exe).name}）---")
    oracle = utmt_oracle(exe, Path(__file__).resolve().parents[2] / "work")
    res = rebuild(data, oracle, max_rounds=args.rounds)
    for line in res.notes:
        print("  " + line)

    if not res.ok:
        print("\n重建失败，没有产出文件。")
        return 1

    out = Path(args.out) if args.out else src.with_suffix(".rebuilt.win")
    out.write_bytes(res.data)
    print(f"\n写出 {out}   {len(res.data):,} 字节")
    if not res.loaded:
        print("注意：UTMT 仍然读不通这个文件 —— 见模块开头的「它做不到什么」。")
        return 1
    print("UTMT 已能读取该文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
