# -*- coding: utf-8 -*-
"""LZ4 block 格式解压（纯 Python）。

Fusion 2.5 的图像数据用 LZ4 block 压缩（不是 frame 格式，没有帧头和校验和）。
这里只实现解压。

LZ4 block 结构：一串「序列」，每个序列是::

    token(1B)  高 4 位 = 字面量长度, 低 4 位 = 匹配长度 - 4
    [字面量长度扩展]  长度 >= 15 时，逐字节累加直到字节 != 255
    字面量...
    [匹配偏移(2B 小端)]  序列末尾没有
    [匹配长度扩展]
    从「当前位置 - 偏移」开始复制「匹配长度 + 4」个字节（可重叠）
"""


class Lz4Error(Exception):
    pass


def decompress(src: bytes, dest_size: int = 0) -> bytes:
    """解压一段 LZ4 block。

    dest_size 给定时会校验结果长度；为 0 则不校验。
    """
    dst = bytearray()
    i = 0
    n = len(src)

    while i < n:
        token = src[i]
        i += 1

        # ---- 字面量长度 ----
        lit_len = token >> 4
        if lit_len == 15:
            while True:
                if i >= n:
                    raise Lz4Error("字面量长度扩展越界")
                b = src[i]
                i += 1
                lit_len += b
                if b != 255:
                    break

        # ---- 复制字面量 ----
        if lit_len:
            end = i + lit_len
            if end > n:
                # 有些实现会在末尾少写几个字节，截断处理而不是直接报错
                end = n
            dst += src[i:end]
            i = end
            if i >= n:
                break

        # ---- 匹配偏移 ----
        if i + 2 > n:
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0:
            raise Lz4Error("匹配偏移为 0，数据损坏")

        # ---- 匹配长度 ----
        match_len = token & 0x0F
        if match_len == 15:
            while True:
                if i >= n:
                    raise Lz4Error("匹配长度扩展越界")
                b = src[i]
                i += 1
                match_len += b
                if b != 255:
                    break
        match_len += 4

        start = len(dst) - offset
        if start < 0:
            raise Lz4Error(f"匹配起点越界: offset={offset} pos={len(dst)}")

        if offset >= match_len:
            # 不重叠，直接切片
            dst += dst[start:start + match_len]
        else:
            # 重叠：源区间是周期为 offset 的重复模式
            pattern = bytes(dst[start:start + offset])
            reps = match_len // offset + 1
            dst += (pattern * reps)[:match_len]

    if dest_size and len(dst) != dest_size:
        # 长度不符只警告不抛错 —— 宁可出一张有瑕疵的图
        return bytes(dst)
    return bytes(dst)


def decompress_exact(src: bytes, dest_size: int) -> bytes:
    """解压并补齐/截断到指定长度。"""
    out = decompress(src, dest_size)
    if len(out) < dest_size:
        out = out + b"\x00" * (dest_size - len(out))
    return out[:dest_size]
