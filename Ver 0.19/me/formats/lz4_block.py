# -*- coding: utf-8 -*-
"""LZ4 块解压（纯标准库，自研）。

为什么自己写
------------
UnityFS 包里绝大多数块是 LZ4 压的，而 Python 标准库没有 lz4。
自己实现块格式（block format，不带帧头）就够了，约 60 行：

    每个序列 = [token][扩展字面量长度][字面量][偏移 2 字节][扩展匹配长度]
        token 高 4 位 = 字面量长度（15 表示还有扩展字节）
        token 低 4 位 = 匹配长度 - 4（15 表示还有扩展字节）
    匹配拷贝允许重叠（offset < 匹配长度 时逐字节前移复制）

这个格式自校验能力很弱，所以解压函数对每个字段都做边界检查，
坏数据一律抛 :class:`Lz4Error`，绝不返回半截结果 —— 上层据此退回第三方。

实测用途：UnityFS 的块信息（block info）和块数据都用它。
"""

MAX_OUTPUT = 1 << 30          # 单块上限 1GB，防畸形数据把内存吃光


class Lz4Error(Exception):
    """LZ4 块格式不合法。"""


def _read_len(src: bytes, pos: int) -> tuple:
    """读扩展长度：255 累加，直到遇到 <255 的字节。"""
    total = 0
    while True:
        if pos >= len(src):
            raise Lz4Error("扩展长度越界")
        b = src[pos]
        pos += 1
        total += b
        if b != 255:
            return total, pos
        if total > MAX_OUTPUT:
            raise Lz4Error("扩展长度离谱")


def decompress_block(src: bytes, expected_size: int = 0) -> bytes:
    """解一个 LZ4 块（无帧头）。

    expected_size > 0 时要求结果长度正好等于它 —— UnityFS 会给出每块的
    未压缩长度，对不上说明数据坏了，直接报错。
    """
    src = bytes(src)
    out = bytearray()
    pos = 0
    n = len(src)

    while pos < n:
        token = src[pos]
        pos += 1

        # ---- 字面量
        lit = token >> 4
        if lit == 15:
            extra, pos = _read_len(src, pos)
            lit += extra
        if lit:
            if pos + lit > n:
                raise Lz4Error(f"字面量越界（需要 {lit}，只剩 {n - pos}）")
            out += src[pos:pos + lit]
            pos += lit

        if pos == n:
            break                      # 最后一个序列没有匹配段

        # ---- 匹配
        if pos + 2 > n:
            raise Lz4Error("匹配偏移越界")
        offset = src[pos] | (src[pos + 1] << 8)
        pos += 2
        if offset == 0:
            raise Lz4Error("匹配偏移为 0")
        if offset > len(out):
            raise Lz4Error(f"匹配偏移 {offset} 超过已输出长度 {len(out)}")

        mlen = token & 0x0F
        if mlen == 15:
            extra, pos = _read_len(src, pos)
            mlen += extra
        mlen += 4

        start = len(out) - offset
        for i in range(mlen):          # 允许重叠：逐字节复制
            out.append(out[start + i])

        if len(out) > MAX_OUTPUT:
            raise Lz4Error("输出过大")

    if expected_size and len(out) != expected_size:
        raise Lz4Error(f"解出 {len(out)} 字节，期望 {expected_size}")
    return bytes(out)
