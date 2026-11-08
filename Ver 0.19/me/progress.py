# -*- coding: utf-8 -*-
"""全局进度账本。整个进程一个总数、一个单调计数器。

0.14 的进度条要能显示全局百分比，前提是 total 全程不变。但每一层都在
自己算 total 然后直接往 GUI 报：

    asar 解包报 3990 个文件  ->  done 3990 / total 3990   （100%）
    收尾的散落文件再报 3     ->  done    3 / total    3   （掉回 0.1%）

GUI 拿到的是「谁最后报谁说了算」，百分比会一路往回跳。账本把「层」
消化掉：谁报了自己的一段（stage），账本就把这一段折进全局总数，
送出去的永远是单调的全局读数。

规则：
  * 一段内 total 不变 —— 段内读数原样透传；
  * 换段时上一段按已报到的量结账，新段从 0 起 —— 全局读数只增不减；
  * 全局总数只会变大（新段的活是新发现的），永不回退；
  * 收工调 finish()，强制补一条 done == total —— 有些子解析器最后
    差几个单位不报（实测 Clickteam 停在 2188/2211），进度条会永远
    差一截。

账本只做算术，不碰渲染；真正画进度条的是 bind() 进来的 sink。
"""

from typing import Callable, Optional, Tuple


class Ledger:
    def __init__(self, sink: Optional[Callable] = None) -> None:
        self.sink = sink
        self.base_done = 0
        self.base_total = 0
        self.stage_total = 0
        self.stage_done = 0
        self.stages = 0
        self.last: Tuple[int, int, str] = (0, 0, "")

    def bind(self, sink: Callable) -> None:
        """接上渲染函数。签名与各层报数一致：(done, total, label)。"""
        self.sink = sink

    def reset(self) -> None:
        """每次 run() 开头清一次。"""
        sink = self.sink
        self.__init__(sink)

    def report(self, done: int, total: int, label: str = "") -> None:
        """收某一层的读数，折算成全局读数后送出去。"""
        done = int(done or 0)
        total = int(total or 0)
        if done < 0:
            done = 0

        if total != self.stage_total:
            # 换段
            if self.stage_total > 0:
                self.base_total += self.stage_total
                self.base_done += min(self.stage_done, self.stage_total)
            self.stage_total = total
            self.stage_done = 0
            self.stages += 1

        if done < self.stage_done:
            done = self.stage_done          # 段内也不许回退
        self.stage_done = done

        g_done = self.base_done + self.stage_done
        g_total = self.base_total + self.stage_total
        if g_total < g_done:
            g_total = g_done                # 这一层没报总数，按已报的量兜住
        if g_total <= 0:
            g_total = max(g_done, 1)

        self.last = (g_done, g_total, label)
        if self.sink:
            self.sink(g_done, g_total, label)

    def finish(self) -> Tuple[int, int]:
        """收工补一刀：保证最后一条是 done == total。"""
        g_done, g_total, _ = self.last
        if g_total > 0 and g_done < g_total:
            self.last = (g_total, g_total, "")
            if self.sink:
                self.sink(g_total, g_total, "")
        return self.last[0], self.last[1]


# 全进程一个。各引擎不用自己 new，报数仍然走 ctx.progress。
ledger = Ledger()
