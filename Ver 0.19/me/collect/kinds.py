# -*- coding: utf-8 -*-
"""扩展名 -> 资源分类。

想新增识别类型（比如 .webp、.ogg 之外的格式），只改这个文件的表格。
"""

import os

IMAGE_EXT = {
    ".png", ".jpg", ".jpeg", ".jpe", ".bmp", ".gif", ".tga", ".webp",
    ".dds", ".ktx", ".ktx2", ".astc", ".pvr", ".tif", ".tiff", ".ico",
    ".psd", ".qoi", ".jp2", ".pcx",
}

AUDIO_EXT = {
    ".ogg", ".oga", ".wav", ".mp3", ".flac", ".m4a", ".aac", ".wma",
    ".opus", ".aiff", ".aif", ".mod", ".xm", ".it", ".s3m", ".mid", ".midi",
}

VIDEO_EXT = {
    ".mp4", ".webm", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".mpg", ".mpeg",
    ".ogv", ".bik", ".usm",
}

TEXT_EXT = {
    ".txt", ".json", ".xml", ".csv", ".tsv", ".ini", ".cfg", ".config",
    ".yaml", ".yml", ".toml", ".md", ".log", ".html", ".htm", ".css",
    ".js", ".ts", ".lua", ".gml", ".cs", ".py", ".sh", ".bat", ".sql",
    ".properties", ".lang", ".po", ".strings", ".resx", ".nfo", ".srt",
    ".vtt", ".asm", ".h", ".c", ".cpp", ".hpp", ".java", ".rb", ".pl",
}

FONT_EXT = {
    ".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2", ".pfb", ".pfm", ".fnt",
}

# 分类顺序有讲究：先判字体/音频等特征强的，避免 .fnt 之类被误判
_TABLE = (
    ("image", IMAGE_EXT),
    ("audio", AUDIO_EXT),
    ("video", VIDEO_EXT),
    ("font", FONT_EXT),
    ("text", TEXT_EXT),
)


def ext_of(path) -> str:
    """取小写扩展名（含点）。"""
    return os.path.splitext(str(path))[1].lower()


def classify(path) -> str:
    """返回 'image' / 'audio' / 'text' / 'font' / 'video' / 'other'。"""
    ext = ext_of(path)
    for name, table in _TABLE:
        if ext in table:
            return name
    return "other"


def is_wanted(path, wanted) -> bool:
    """wanted 为 None 表示全要，否则只保留集合里的分类。"""
    if not wanted:
        return True
    return classify(path) in wanted
