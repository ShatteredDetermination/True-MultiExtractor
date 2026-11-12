# -*- coding: utf-8 -*-
"""Clickteam 成品里 flag=3（zlib + 加密）块的处理。

算法整理自公开反编译器对 flag=3 的处理流程（NebulaFD 的 ``Decryption`` 类，
CTFAK 同源），Python 侧为独立重写：只用了标准库，没有复制其源码、也没有链接其库
（见 THIRD_PARTY.md「参考资料」）。流程是：

* 密钥：把若干字符串按 ``key_string`` 规则拆成字节（每个字符只在非 0 时
  贡献低字节 / 高字节），再用 ``make_key_combined`` 撑到 256 字节，
  并在 ``data[长度 + 1]`` 写入 ``校验和 × 2``；
* 换表：256 字节密钥跑一遍 RC4 风格的 KSA（遇到密钥里的 0 字节就回到开头）；
* 加解密：每个块都用同一张表从头跑一遍 RC4 风格的 PRGA，与字节流异或；
* 块体：``u32 解压后长度``（明文）+ 加密数据；解密后先读 ``u32 压缩长度``，
  再把后面的字节 zlib 解压；
* id 为奇数的块（0x3337 调色板、0x3341 图层、0x333D 事件等）在解密前
  还要把加密数据的第 0 字节异或 ``(id & 0xFF) ^ (id >> 8)``，
  这个「补丁」只在编译号 > 285 的成品里出现。

**还差的一步**：密钥到底由哪些字符串拼出来，样本上还没对上
（候选试过「应用名」「应用名 + Fusion」「Fusion + 应用名」以及带版本号的一批，
全部解不出）。所以本模块目前对真样本会抛 ``CryptoError``，等密钥定下来，
帧头 / 图层 / 实例 / 事件 / 对象属性就能全部读出来了。

块体布局本身已经确认：两支样本的 0x3337 调色板块声明长度恒为
``1028 = 256 × 4 + 4``（调色板 256 色 + 一个 u32 计数），0x3341 图层块恒为 40、
0x3342 虚拟矩形块恒为 16 —— 这几个数字只能是明文长度，说明「u32 长度前缀」
这一层读对了。
"""

import struct
import zlib

__all__ = [
    "CryptoError", "DEFAULT_KEY", "key_string", "make_key_combined",
    "init_table", "transform", "decrypt_chunk_body", "encrypt_chunk_body",
    "build_encryption", "KEY_STATUS",
]

#: 编译号 > 285 的成品，奇数 id 的块要先把第 0 字节异或一下
XOR_FIX_BUILD = 285

#: 占位密钥（全 0）。真样本的密钥见模块说明，还没定下来。
DEFAULT_KEY = b""

#: 给文档/CLI 用的一句话状态
KEY_STATUS = "密钥推导未定，flag=3 块暂解不开（布局已确认）"


class CryptoError(Exception):
    """flag=3 块解不开：密钥不对、块头不对，或者不是 zlib。"""


def key_string(text):
    """把字符串拆成密钥字节：只保留非 0 的低字节和高字节。"""
    out = bytearray()
    for ch in text:
        code = ord(ch)
        low = code & 0xFF
        high = (code >> 8) & 0xFF
        if low:
            out.append(low)
        if high:
            out.append(high)
    return bytes(out)


def make_key_combined(data):
    """把字节撑到 256（原实现先扩到 128 再扩到 256），并写校验和。"""
    data = bytearray(data)
    length = len(data)
    checksum = 0
    for b in data:
        checksum = (checksum + b) & 0xFF
    if len(data) < 256:
        data.extend(b"\x00" * (256 - len(data)))
    if length < 255:
        data[length + 1] = (checksum * 2) & 0xFF
    return bytes(data[:256])


def build_encryption(texts):
    """把字符串列表拼成 256 字节密钥（``MakeKey`` 的等价物）。"""
    raw = bytearray()
    for text in texts:
        raw.extend(key_string(text or ""))
    return make_key_combined(bytes(raw))


def init_table(key):
    """RC4 风格换表：返回 256 个 0~255 的排列。"""
    key = bytes(make_key_combined(key))
    buf = list(range(256))
    j = 0
    pos = 0
    for i in range(256):
        if key[pos] == 0:
            pos = 0
        j = (j + key[pos] + buf[i]) & 0xFF
        pos += 1
        buf[i], buf[j] = buf[j], buf[i]
    return buf


def transform(data, table):
    """RC4 风格异或流；同一张表上再做一遍就还原。"""
    temp = list(table)
    i = 0
    j = 0
    out = bytearray(data)
    for n in range(len(out)):
        i = (i + 1) & 0xFF
        j = (j + temp[i]) & 0xFF
        temp[i], temp[j] = temp[j], temp[i]
        out[n] ^= temp[(temp[i] + temp[j]) & 0xFF]
    return bytes(out)


def _xor_fix(data, chunk_id):
    """奇数 id 的块在解密前要把第 0 字节异或一下。"""
    buf = bytearray(data)
    if buf and (chunk_id & 1):
        buf[0] ^= (chunk_id & 0xFF) ^ ((chunk_id >> 8) & 0xFF)
    return bytes(buf)


def decrypt_chunk_body(body, chunk_id, key=None, build=300):
    """解一个 flag=3 块的块体，返回 ``(声明长度, 明文)``。

    ``body`` 是块体原始字节（``u32 声明长度`` + 加密数据）。
    解不开就抛 :class:`CryptoError`，不返回半成品。
    """
    if len(body) < 8:
        raise CryptoError(f"块体只有 {len(body)} B，短于头部")
    declared = struct.unpack_from("<I", body, 0)[0]
    encrypted = body[4:]
    if build > XOR_FIX_BUILD:
        encrypted = _xor_fix(encrypted, chunk_id)
    table = init_table(DEFAULT_KEY if key is None else key)
    raw = transform(encrypted, table)
    compressed_size = struct.unpack_from("<I", raw, 0)[0]
    if compressed_size == 0 or compressed_size > len(raw) - 4:
        raise CryptoError(f"压缩长度 {compressed_size} 越界（块体 {len(raw)} B）")
    payload = raw[4:4 + compressed_size]
    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            return declared, zlib.decompress(payload, wbits)
        except zlib.error:
            continue
    raise CryptoError("zlib 解压失败：密钥不对，或者这个块不是 flag=3 的布局")


def encrypt_chunk_body(plain, chunk_id, key=None, build=300):
    """``decrypt_chunk_body`` 的逆操作（自测用，真实成品只会读）。"""
    compressed = zlib.compress(plain, 9)
    table = init_table(DEFAULT_KEY if key is None else key)
    payload = struct.pack("<I", len(compressed)) + compressed
    encrypted = transform(payload, table)
    if build > XOR_FIX_BUILD:
        encrypted = _xor_fix(encrypted, chunk_id)
    return struct.pack("<I", len(plain)) + encrypted
