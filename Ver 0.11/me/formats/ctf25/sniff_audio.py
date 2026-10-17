# -*- coding: utf-8 -*-
"""音频格式嗅探。

Fusion 的音频数据是直接内嵌的完整文件，但记录里不带格式信息，
所以只能靠文件头判断。
"""

# 魔数 -> 扩展名
_SIGS = (
    (b"OggS", ".ogg"),
    (b"RIFF", ".wav"),
    (b"ID3", ".mp3"),
    (b"fLaC", ".flac"),
    (b"FORM", ".aiff"),
    (b"wvpk", ".wv"),
    (b"MThd", ".mid"),
)

# MP3 没有固定魔数，靠帧同步字猜
_MP3_SYNC = (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa", b"\xff\xe3")


def guess_ext(data: bytes) -> str:
    """返回带点的扩展名，猜不出返回 .bin。"""
    if not data or len(data) < 4:
        return ".bin"
    for magic, ext in _SIGS:
        if data[:len(magic)] == magic:
            if magic == b"RIFF" and len(data) >= 12:
                if data[8:12] == b"WAVE":
                    return ".wav"
                if data[8:12] == b"AVI ":
                    return ".avi"
                if data[8:12] == b"WEBP":
                    return ".webp"
            return ext
    for sync in _MP3_SYNC:
        if data[:2] == sync:
            return ".mp3"
    return ".bin"
