# -*- coding: utf-8 -*-
"""通用兜底提取：按扩展名收集。

任何引擎识别失败、或引擎模块表示「解不开」时都走这里。
对已经解包好的游戏目录，这个方式本身就够用。
"""

from pathlib import Path

from me.collect import gather
from me.engines.base import Context, Outcome

KEY = "generic"
NAME = "通用收集"


def extract(ctx: Context) -> Outcome:
    target = Path(ctx.target)
    out = Outcome()

    if target.is_dir():
        n = gather.gather_tree(target, ctx.writer,
                               origin=str(target),
                               progress=ctx.progress)
        out.written = n
        if n == 0:
            stats = gather.describe_tree(target)
            out.notes.append("目录里没有可识别的资源文件")
            if stats:
                out.notes.append("扩展名分布: " + ", ".join(
                    f"{e}×{c}" for e, c in stats))
        else:
            out.detail = f"按扩展名收集了 {n} 个文件"
        return out

    # 单文件：按魔数猜个后缀再写出去
    from me.collect import sniff
    from me.collect import kinds

    data = target.read_bytes()
    ext = sniff.guess_ext(data)
    rel = target.name if (ext and target.suffix.lower() == ext) else \
        (target.stem + ext if ext else target.name)
    if ctx.writer.write_bytes(kinds.classify(rel), rel, data, str(target)):
        out.written = 1
        out.detail = f"导出为 {rel}"
    return out
