# -*- coding: utf-8 -*-
"""Electron asar 归档解析（纯 Python，无需 node）。

asar 布局（chromium Pickle 序列化）::

    偏移 0   u32 = 4                     pickle#1 载荷长度（固定 4）
    偏移 4   u32 = headerBuf 长度
    偏移 8   u32 = headerBuf 载荷长度  = 4 + 对齐后的 json 长度
    偏移 12  u32 = json 原始长度
    偏移 16  json（4 字节对齐，不足补 0）
    之后    文件数据区

json 是一棵目录树，文件节点形如::

    {"size": 123, "offset": "0"}          # offset 是相对数据区的字符串
    {"size": 123, "unpacked": true}       # 数据在同级 <名字>.unpacked/ 目录里

参考 electron/asar 的 disk.ts / pickle 实现。
"""

import json
import os
import struct
from pathlib import Path

FLAG_PICKLE_SIZE = 4


class AsarError(Exception):
    pass


def read_header(archive_path):
    """读取 asar 头部。

    返回 (header_dict, data_offset, header_used)。
    """
    with open(archive_path, "rb") as f:
        first = f.read(16)
        if len(first) < 16:
            raise AsarError("文件太小，不是 asar")

        p1_size, header_buf_len, p2_size, json_len = struct.unpack("<IIII", first)

        if p1_size != FLAG_PICKLE_SIZE:
            # 头部尺寸字段不在预期位置，退回到「扫描 JSON 起点」的兜底方案
            return _read_header_fallback(archive_path)

        if json_len <= 0 or json_len > 256 * 1024 * 1024:
            raise AsarError(f"json 长度异常: {json_len}")

        padded = (json_len + 3) & ~3
        if p2_size != padded + 4:
            # 长度对不上，同样走兜底
            return _read_header_fallback(archive_path)

        f.seek(16)
        raw = f.read(padded)
        if len(raw) < json_len:
            raise AsarError("头部被截断")

        try:
            header = json.loads(raw[:json_len].decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise AsarError(f"头部 JSON 解析失败: {exc}") from exc

        return header, 8 + header_buf_len, header_buf_len


def _read_header_fallback(archive_path):
    """兜底：直接找一个能解析成 JSON 的起点。"""
    with open(archive_path, "rb") as f:
        buf = f.read(4 * 1024 * 1024)
    start = buf.find(b'{"files":')
    if start < 0:
        raise AsarError("找不到 asar 头部 JSON")
    decoder = json.JSONDecoder()
    text = buf[start:].decode("utf-8", "replace")
    header, used = decoder.raw_decode(text)
    return header, start + used, used


def iter_entries(header):
    """遍历头部，yield (相对路径, 节点 dict, 是否目录)。"""
    stack = [("", header)]
    while stack:
        prefix, node = stack.pop()
        files = node.get("files")
        if not isinstance(files, dict):
            continue
        for name, child in files.items():
            rel = f"{prefix}/{name}" if prefix else name
            if isinstance(child, dict) and "files" in child:
                stack.append((rel, child))
            else:
                yield rel, child, False


def summarize(archive_path):
    """返回 (文件数, 总字节, 目录数)。"""
    header, _, _ = read_header(archive_path)
    n = 0
    total = 0
    for _rel, node, _isdir in iter_entries(header):
        n += 1
        total += int(node.get("size") or 0)
    return n, total, _count_dirs(header)


def _count_dirs(node):
    n = 0
    for child in (node.get("files") or {}).values():
        if isinstance(child, dict) and "files" in child:
            n += 1 + _count_dirs(child)
    return n


def extract(archive_path, writer, origin: str = "", progress=None,
            all_categories: bool = True):
    """把 asar 里的文件写进 writer。

    writer 是 me.collect.writer.OutputWriter。
    返回实际写出的文件数。
    """
    archive_path = Path(archive_path)
    header, data_off, _ = read_header(archive_path)
    entries = list(iter_entries(header))
    total = len(entries)
    written = 0

    unpacked_root = Path(str(archive_path) + ".unpacked")

    with open(archive_path, "rb") as f:
        for i, (rel, node, _isdir) in enumerate(entries):
            if progress:
                progress(i + 1, total, os.path.basename(rel)[:32])
            size = int(node.get("size") or 0)

            if node.get("unpacked"):
                src = unpacked_root / rel
                if src.exists():
                    if writer.copy_from(_cat(rel), rel, src, origin or "asar:unpacked"):
                        written += 1
                continue

            if node.get("link"):
                continue  # 符号链接，跳过

            offset = node.get("offset")
            if offset is None:
                continue
            offset = int(offset)

            f.seek(data_off + offset)
            data = f.read(size)
            if writer.write_bytes(_cat(rel), rel, data, origin or "asar"):
                written += 1

    if progress:
        progress(total, total, "完成")
    return written


def _cat(rel: str) -> str:
    from me.collect import kinds
    return kinds.classify(rel)
