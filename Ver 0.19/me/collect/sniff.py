# -*- coding: utf-8 -*-
"""按文件头猜真实格式。

很多引擎把资源塞成没有扩展名的裸数据块，写出前先用这个猜个后缀，
免得一堆 .bin 用户没法看。
"""

# (偏移, 魔数, 扩展名)
_SIGS = (
    (0, b"\x89PNG\r\n\x1a\n", ".png"),
    (0, b"\xff\xd8\xff", ".jpg"),
    (0, b"GIF87a", ".gif"),
    (0, b"GIF89a", ".gif"),
    (0, b"BM", ".bmp"),
    (0, b"RIFF", ".riff"),   # 下面再细分
    (0, b"OggS", ".ogg"),
    (0, b"ID3", ".mp3"),
    (0, b"\xff\xfb", ".mp3"),
    (0, b"\xff\xf3", ".mp3"),
    (0, b"\xff\xf2", ".mp3"),
    (0, b"fLaC", ".flac"),
    (0, b"\x00\x00\x01\x00", ".ico"),
    (0, b"\x00\x00\x02\x00", ".cur"),
    (0, b"OTTO", ".otf"),
    (0, b"ttcf", ".ttc"),
    (0, b"wOFF", ".woff"),
    (0, b"wOF2", ".woff2"),
    (0, b"\x1a\x45\xdf\xa3", ".webm"),
    (0, b"DDS ", ".dds"),
    (0, b"\xabKTX", ".ktx"),
    (0, b"PK\x03\x04", ".zip"),
    (0, b"\x1f\x8b", ".gz"),
    (0, b"SQLite format 3", ".sqlite"),
    (0, b"<?xml", ".xml"),
    (0, b"[", ".json"),
    (0, b"{", ".json"),
)


def guess_ext(data: bytes) -> str:
    """返回猜测的扩展名（含点），猜不出返回空串。"""
    if not data:
        return ""
    if len(data) >= 4 and data[:4] == b"\x00\x01\x00\x00":
        return ".ttf"
    for off, magic, ext in _SIGS:
        if data[off:off + len(magic)] == magic:
            if ext == ".riff" and len(data) >= 12:
                kind = data[8:12]
                return {
                    b"WAVE": ".wav",
                    b"AVI ": ".avi",
                    b"WEBP": ".webp",
                }.get(kind, ".riff")
            return ext
    return ""


def is_probably_text(data: bytes) -> bool:
    """粗略判断是否文本（用于决定要不要按文本写盘）。"""
    if not data:
        return False
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
    return printable / len(sample) > 0.90
