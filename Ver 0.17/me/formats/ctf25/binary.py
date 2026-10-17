# -*- coding: utf-8 -*-
"""CTF 用的字节读取器。

CTF 里的整数全是小端。字符串有两种：
  - ASCII（老版本，PAME）
  - UTF-16LE（Fusion 2.5，PAMU）
具体用哪种由文件头的 magic 决定，所以读取函数都要带 unicode 开关。
"""

import struct


class Reader:
    """在一段 bytes 上按偏移读取。"""

    __slots__ = ("data", "pos", "_len")

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos
        self._len = len(data)

    # -------------------------------------------------------- 位置
    def tell(self) -> int:
        return self.pos

    def seek(self, pos: int) -> None:
        self.pos = pos

    def skip(self, n: int) -> None:
        self.pos += n

    def size(self) -> int:
        return self._len

    def remaining(self) -> int:
        return self._len - self.pos

    def eof(self) -> bool:
        return self.pos >= self._len

    # -------------------------------------------------------- 整数
    def u8(self) -> int:
        v = self.data[self.pos]
        self.pos += 1
        return v

    def i8(self) -> int:
        v = struct.unpack_from("<b", self.data, self.pos)[0]
        self.pos += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def i16(self) -> int:
        v = struct.unpack_from("<h", self.data, self.pos)[0]
        self.pos += 2
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def peek_i16(self) -> int:
        return struct.unpack_from("<h", self.data, self.pos)[0]

    def peek_u16(self) -> int:
        return struct.unpack_from("<H", self.data, self.pos)[0]

    def peek_i32(self) -> int:
        return struct.unpack_from("<i", self.data, self.pos)[0]

    def peek_bytes(self, n: int) -> bytes:
        return self.data[self.pos:self.pos + n]

    # -------------------------------------------------------- 字节/字符串
    def read(self, n: int) -> bytes:
        if n <= 0:
            return b""
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def ascii(self, n: int) -> str:
        return self.read(n).decode("latin-1", "replace")

    def ascii_z(self) -> str:
        """以 \\0 结尾的 ASCII 串。"""
        end = self.data.find(b"\x00", self.pos)
        if end < 0:
            end = self._len
        out = self.data[self.pos:end].decode("latin-1", "replace")
        self.pos = end + 1
        return out

    def wide(self, n: int) -> str:
        """读 n 个 UTF-16LE 字符。"""
        return self.read(n * 2).decode("utf-16-le", "replace")

    def wide_z(self) -> str:
        """以 \\0\\0 结尾的 UTF-16 串。"""
        start = self.pos
        while self.pos + 1 < self._len:
            if self.data[self.pos] == 0 and self.data[self.pos + 1] == 0:
                out = self.data[start:self.pos].decode("utf-16-le", "replace")
                self.pos += 2
                return out
            self.pos += 2
        return self.data[start:].decode("utf-16-le", "replace")

    def yuniversal(self, n: int, unicode: bool) -> str:
        """按文件类型读字符串：unicode 文件读 UTF-16，否则读 ASCII。"""
        return self.wide(n) if unicode else self.ascii(n)

    def yuniversal_z(self, unicode: bool) -> str:
        return self.wide_z() if unicode else self.ascii_z()
