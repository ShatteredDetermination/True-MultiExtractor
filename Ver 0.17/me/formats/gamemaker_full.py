# -*- coding: utf-8 -*-
"""整份 data.win 的全量重建。

按 GameMaker 的块序，给每个 chunk 选一个可用模型，交给 Serializer 写出：

* 有真模型的：STRG / FONT / TXTR / AUDO / SPRT / FUNC / VARI / TPAG / CODE；
* 条目型（字符串槽位能自动发现）：SOND / BGND / PATH / SCPT / OBJT / ROOM / AGRP；
* 纯结构块（不含绝对偏移）：OPTN / LANG / EXTN / GLOB / SHDR / TMLN / DAFL / EMBI
  先按原字节搬运 —— 这些块里没有指针，搬字节是安全的。

块序固定成 GameMaker 自己的顺序，不沿用原文件的顺序：原文件的顺序可能被
改写工具打乱过，而块序本身没有语义（除了 GEN8 必须最前）。

已知缺口（会让 UTMT 在对应块报错，见 ROADMAP）：
OBJT 的事件指针对、ROOM 的实例/图块指针还没建模。
"""

from pathlib import Path
from typing import Dict, List, Optional, Sequence

from me.formats import gamemaker_ast as ast
from me.formats import gamemaker_serializer as ser

# GameMaker 的块序（data.win 里除 GEN8 外的顺序）
CHUNK_ORDER = (
    "OPTN", "LANG", "EXTN", "SOND", "AGRP", "SPRT", "BGND", "PATH", "SCPT",
    "GLOB", "SHDR", "FONT", "TMLN", "OBJT", "ROOM", "DAFL", "EMBI", "TPAG",
    "CODE", "VARI", "FUNC", "STRG", "TXTR", "AUDO",
)

# 只含定长结构、没有绝对偏移的块：原字节搬运
RAW_SAFE = {"OPTN", "LANG", "EXTN", "GLOB", "SHDR", "TMLN", "DAFL", "EMBI"}

# 用 VariableListChunk（自动发现字符串槽位）的块
RECORDED = {"SOND", "BGND", "PATH", "SCPT", "OBJT", "ROOM", "AGRP"}


class FullReport:
    """重建过程中用到了哪些模型、跳过了哪些块。"""

    def __init__(self):
        self.modeled: List[str] = []
        self.raw: List[str] = []
        self.skipped: List[str] = []
        self.notes: List[str] = []

    def __repr__(self) -> str:                       # pragma: no cover
        return (f"<FullReport 建模 {len(self.modeled)} 块, "
                f"原样 {len(self.raw)} 块>")


def assemble(w) -> (ast.DataFile, FullReport):
    """把 WinFile 组装成完整的 DataFile（不写出）。"""
    rep = FullReport()
    table = ast.StringTable.from_win(w)
    tpag = ast.OpaqueListChunk.from_win(w, "TPAG") \
        if "TPAG" in w.chunks else None
    code = ast.RebasedListChunk.from_win(w, "CODE", table=table) \
        if "CODE" in w.chunks else None

    made: Dict[str, ast.Chunk] = {"STRG": table}

    if tpag is not None:
        made["TPAG"] = tpag
    if code is not None:
        made["CODE"] = code
        rep.notes.append("CODE 整块搬运（内部有 5 MB 没有条目指向的字节）")

    if "FONT" in w.chunks:
        made["FONT"] = ast.FontTable.from_win(w, strings=table, tpag=tpag)
    if "SPRT" in w.chunks and tpag is not None:
        made["SPRT"] = ast.SpriteTable.from_win(w, table=table, tpag=tpag)
    if "TXTR" in w.chunks:
        made["TXTR"] = ast.TextureTable.from_win(w)
    if "AUDO" in w.chunks:
        made["AUDO"] = ast.AudioTable.from_win(w)
    if "FUNC" in w.chunks and code is not None:
        made["FUNC"] = ast.FunctionTable.from_win(w, table=table, code=code)
    if "VARI" in w.chunks and code is not None:
        made["VARI"] = ast.VariableTable.from_win(w, table=table, code=code)
    if "OBJT" in w.chunks:
        made["OBJT"] = ast.ObjectTable.from_win(w, table=table)
    if "ROOM" in w.chunks:
        made["ROOM"] = ast.RoomTable.from_win(w, table=table)
    # BGND 的条目里有指向 TPAG 的指针，得把 TPAG 的条目地址表交给它
    if "BGND" in w.chunks and tpag is not None:
        made["BGND"] = ast.VariableListChunk.from_win(
            w, "BGND", strings=table,
            objects={"TPAG": {r.origin: r for r in tpag.entries}})
    for name in RECORDED - {"OBJT", "ROOM", "BGND"}:
        if name in w.chunks and w.chunks[name].size >= 8:
            made[name] = ast.VariableListChunk.from_win(w, name, strings=table)
    for name in RAW_SAFE:
        if name in w.chunks and name not in made:
            c = w.chunks[name]
            count = w.u32(c.base) if c.size >= 4 else -1
            if count == 0 and c.size <= 8:
                # 空表：结构就是一个 count(4)，源文件后面多出来的那几字节是补位。
                # UTMT 的 UndertaleListChunk 读 count 就收工，多出来的字节会让它报
                # “specified length 8, but read only 4 (4 padding?)”，所以这里按
                # 结构长度写 4 字节，而不是把补位一起搬过去。
                made[name] = ast.VariableListChunk(name, [])
                rep.notes.append(f"{name}: 空表，去掉 {c.size - 4} 字节补位")
            else:
                made[name] = ast.FlatStructChunk.from_win(w, name, table=table)

    # 有块引用了 STRG 表里没有的字符串记录（原文件被改写工具切过），
    # 这些字符串按文本重新造出来并追加到表尾 —— 追加不改已有下标
    gen8 = ast.FlatStructChunk.from_win(w, "GEN8", table=table)
    by_text = {s.text: s for s in table.strings}
    for c in list(made.values()) + [gen8]:
        for off, (s, kind) in list(getattr(c, "refs", {}).items()):
            if kind != "str":
                continue
            same = by_text.get(s.text)
            if same is None:
                table.strings.append(s)
                by_text[s.text] = s
                rep.notes.append(f"补回表外字符串: {s.text[:32]!r}")
            elif same is not s:
                c.refs[off] = (same, kind)

    chunks: List[ast.Chunk] = [gen8]
    for name in CHUNK_ORDER:
        c = made.get(name)
        if c is None and name in w.chunks:
            # 兜底：还没写模型（或本来就是空块）的用「定长结构」处理
            c = ast.FlatStructChunk.from_win(w, name, table=table)
            made[name] = c
        if c is None:
            continue
        chunks.append(c)
        (rep.modeled if not isinstance(c, ast.RawChunk) else rep.raw).append(name)
    return ast.DataFile(chunks), rep


def build_full(w) -> (bytes, FullReport):
    """整份重建，返回 (新字节, 报告)。"""
    data, rep = assemble(w)
    return ser.build(data).data, rep


def build_bytes_for(w, names) -> Optional[bytes]:
    """只重建指定的几个块（自测用），缺模型就返回 None。"""
    data, _rep = assemble(w)
    keep = [data.chunks[0]] + [c for c in data.chunks[1:] if c.name in set(names)]
    return ser.build(ast.DataFile(keep)).data


# ══════════════════════════════════════════════════════════════════
#  对外的「重建一份文件」
# ══════════════════════════════════════════════════════════════════


class RebuildReport:
    """一次重建的结果：写到哪里、进出多少字节、用了哪些模型。"""

    def __init__(self, src: Path, dst: Path, old_bytes: int, new_bytes: int,
                 chunks: Sequence[str], modeled: Sequence[str],
                 raw: Sequence[str], notes: Sequence[str]):
        self.src = src
        self.dst = dst
        self.old_bytes = old_bytes
        self.new_bytes = new_bytes
        self.chunks = list(chunks)
        self.modeled = list(modeled)
        self.raw = list(raw)
        self.notes = list(notes)

    def __repr__(self) -> str:                       # pragma: no cover
        return (f"<RebuildReport {self.old_bytes:,} -> {self.new_bytes:,} "
                f"{len(self.chunks)} 块>")


def _chunk_counts(w) -> Dict[str, tuple]:
    """各块的条目数（空表也按条目数算，不按字节数算）。"""
    out = {}
    for name in w.order:
        ch = w.chunks[name]
        if ch.size < 4:
            out[name] = ("size", ch.size)
            continue
        cnt = w.u32(ch.base)
        if 4 + 4 * cnt <= ch.size and cnt < 1000000:
            out[name] = ("count", cnt)
        else:
            out[name] = ("size", ch.size)
    return out


def rebuild_bytes(w, src: Optional[Path] = None) -> bytes:
    """把 WinFile 重建成一整份新文件的字节，并**读回来核对条目数**。

    核对这一步是硬性的：新文件必须能被自己的解析器读成同样的块序、同样的
    条目数。对不上就抛 ``ModelError``，不给半对半错的文件。
    """
    blob, _rep, _names = _rebuild(w, src)
    return blob


def _rebuild(w, src: Optional[Path] = None):
    """内部：重建 + 读回核对，返回 (字节, FullReport)。"""
    from me.formats.gamemaker_win import WinFile

    data, rep = assemble(w)
    blob = ser.build(data).data

    back = WinFile(blob)
    if not back.chain_ok:
        raise ast.ModelError("重建结果的 chunk 链不完整")
    expect = [c for c in CHUNK_ORDER if c in back.chunks]
    if back.order != expect:
        raise ast.ModelError("重建结果的块序不是 GameMaker 标准顺序")
    before, after = _chunk_counts(w), _chunk_counts(back)
    bad = [(k, before[k], after.get(k)) for k in before
           if after.get(k) != before[k]]
    if bad:
        raise ast.ModelError(f"重建后条目数与原文不一致: {bad[:3]}")
    return blob, rep, [c.name for c in data.chunks]


def rebuild_file(src, dst=None) -> RebuildReport:
    """读一份 data.win → 整份重建 → 写出。返回报告。"""
    from me.formats.gamemaker_win import WinFile

    src = Path(src)
    w = WinFile.load(src)
    blob, rep, names = _rebuild(w, src)
    if dst is None:
        dst = src.with_name(src.stem + "_rebuilt" + src.suffix)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(blob)
    return RebuildReport(src, dst, len(w.raw), len(blob), names,
                         rep.modeled, rep.raw, rep.notes)
