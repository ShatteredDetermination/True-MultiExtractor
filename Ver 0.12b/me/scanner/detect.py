# -*- coding: utf-8 -*-
"""引擎判定。

输入一个文件或目录，输出 Detection（引擎、壳、证据）。
原来的 MultiExtractor.py 判定逻辑有几个坑，这里一并修掉：
  - Unreal .pak 魔数字节序写反了（0x5A6F12E1 小端应是 E1 12 6F 5A）
  - 只扫第一个 exe，多个 exe 时漏检
  - ".pck" 之类用字符串包含判断太宽，会误命中 .pckxxx
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from me.config import SCAN_LIMIT
from me.scanner import dirscan, signatures


@dataclass
class Detection:
    """识别结果。"""
    engine: str = "unknown"      # 引擎键
    name: str = "未识别"          # 显示名
    shell: str = ""              # 保护壳显示名（如 Enigma Virtual Box）
    inner: str = ""              # 壳内的引擎键
    inner_name: str = ""         # 壳内引擎显示名
    entry: str = ""              # 主数据文件（data.win / app.asar / .dat …）
    evidence: List[str] = field(default_factory=list)
    truncated: bool = False      # 目录太大被截断

    @property
    def is_known(self) -> bool:
        return self.engine not in ("unknown", "")

    def describe(self) -> str:
        if self.shell:
            if self.inner_name:
                return f"{self.shell} 壳（内层: {self.inner_name}）"
            return f"{self.shell} 壳（内层未识别）"
        return self.name


# ------------------------------------------------------------------ 二进制
def scan_bytes(data: bytes) -> tuple:
    """扫一段二进制，返回 (引擎键, 显示名, 证据列表)。"""
    evidence: List[str] = []

    # 先看 4 字节魔数（更精确）
    for magic, key, label in signatures.MAGIC_SIGS:
        if magic in data:
            evidence.append(f"魔数 {magic.hex(' ')} -> {label}")
            return key, label, evidence

    for sig, key, label in signatures.ENGINE_SIGS:
        if sig in data:
            evidence.append(f"特征 {sig.decode('latin1', 'replace')!r} -> {label}")
            return key, label, evidence

    return "unknown", "未识别", evidence


def scan_file(path, limit: int = SCAN_LIMIT) -> Detection:
    """扫描单个文件。"""
    path = Path(path)
    det = Detection()
    data = dirscan.read_head(path, limit)
    if not data:
        return det

    # GameMaker 的数据文件优先看容器头。用户直接把 data.win / game.droid
    # 粘进来是很常见的用法，而这类文件**没有 4 字节魔数**，只能靠
    # FORM....GEN8 这个容器头认。
    if looks_like_gamemaker(path, data):
        det.engine, det.name = "gamemaker", "GameMaker"
        det.evidence.append("FORM/GEN8 容器头 —— GameMaker 数据文件")
        det.entry = str(path)
        return det

    # 非 PE 文件（比如裸 data.win / .dat）也要能识别
    key, label, evidence = scan_bytes(data)
    det.evidence = evidence

    # 打包壳优先：Enigma 壳里往往还能搜到内层引擎特征，要分开报
    if key == "enigma":
        det.shell = label
        inner_key, inner_label, ev2 = _scan_inner(data)
        det.inner, det.inner_name = inner_key, inner_label
        det.engine, det.name = "enigma", label
        det.evidence += ev2
        return det

    if key != "unknown":
        det.engine, det.name = key, label
        return det

    # Clickteam 没有任何 ASCII 特征，只能靠结构判断：
    # wwww 包装头 + 尾部 PAMU/PAME/CRUF 标记
    if looks_like_ctf(data):
        det.engine = "clickteam"
        det.name = "Clickteam Fusion"
        det.evidence.append("检测到 CCN 数据流（PAMU/PAME/CRUF 结构）")
        det.entry = str(path)

    return det


def looks_like_ctf(data: bytes) -> bool:
    """判断这段数据是不是 Clickteam 的 CCN 容器。"""
    try:
        from me.formats.ctf25 import container
        return container.locate(data) is not None
    except Exception:  # noqa: BLE001 - 识别阶段的异常一律当作「不是」
        return False


# GameMaker 数据文件的典型文件名。用户直接把这些文件粘进来时，
# 靠文件名也能认出来（老版本或被改过的 data.win 未必是 FORM/GEN8）。
GAMEMAKER_DATA_NAMES = frozenset({
    "data.win", "game.droid", "game.ios", "game.unx", "game.emu",
    "game.win", "data.win.unx",
})


def looks_like_gamemaker(path, data: bytes) -> bool:
    """判断这个文件是不是 GameMaker 的数据文件。

    两条判据，任一成立即可：
      1. 容器头是 FORM....GEN8 —— 最可靠，且和文件名无关
      2. 文件名是 data.win / game.droid 之类 —— 兜底
         （有些文件被工具改过，容器头可能不是标准的 FORM/GEN8）
    """
    if len(data) >= 12 and data[:4] == b"FORM" and data[8:12] == b"GEN8":
        return True
    return Path(path).name.lower() in GAMEMAKER_DATA_NAMES


def _scan_inner(data: bytes) -> tuple:
    """在壳里找内层引擎特征。"""
    for sig, key, label in signatures.ENGINE_SIGS:
        if key in ("enigma", "pyinstaller"):
            continue
        if sig in data:
            return key, label, [f"壳内层特征 {sig.decode('latin1', 'replace')!r}"]
    return "", "", []


# ------------------------------------------------------------------ 目录
def scan_dir(path) -> Detection:
    """扫描目录结构。"""
    path = Path(path)
    det = Detection()
    idx = dirscan.scan(path)
    det.truncated = idx.truncated

    # 1) 特定文件名
    for marker, also, key, label in signatures.DIR_MARKERS:
        if not idx.has(marker):
            continue
        if also and not idx.has(*also):
            continue
        det.engine, det.name = key, label
        det.evidence.append(f"目录含 {marker}")
        det.entry = marker
        break

    # 2) 特定后缀
    if not det.is_known:
        for ext, key, label in signatures.DIR_EXT_MARKERS:
            n = idx.count_ext(ext)
            if n:
                det.engine, det.name = key, label
                det.evidence.append(f"目录含 {n} 个 {ext} 文件")
                break

    # 3) 细分：Unity 的 Mono / IL2CPP
    if det.engine == "unity":
        if idx.has_dir("il2cpp_data") or idx.has("il2cpp"):
            det.name = "Unity (IL2CPP)"
        elif idx.has_dir("managed") or idx.count_ext(".dll") > 0:
            det.name = "Unity (Mono)"
        if det.name != "Unity":
            det.evidence.append("按脚本后端细分为 " + det.name)

    # 4) 目录里没有明显标记：先看 Clickteam 数据文件（快且准），再看 exe
    if not det.is_known:
        for p in _ctf_candidates(path):
            try:
                with open(p, "rb") as f:
                    head = f.read(4 * 1024 * 1024)
            except OSError:
                continue
            if looks_like_ctf(head):
                det.engine, det.name = "clickteam", "Clickteam Fusion"
                det.evidence.append(f"数据文件 {p.name} 含 CCN 数据流")
                det.entry = str(p)
                det.truncated = idx.truncated
                return det

    if not det.is_known and idx.exes:
        for exe in idx.exes[:8]:
            sub = scan_file(exe)
            if sub.shell:
                det.engine, det.name = "enigma", sub.shell
                det.shell = sub.shell
                det.inner, det.inner_name = sub.inner, sub.inner_name
                det.evidence = [f"{exe.name}: {e}" for e in sub.evidence]
                det.entry = str(exe)
                return det
            if sub.is_known:
                sub.evidence = [f"{exe.name}: {e}" for e in sub.evidence]
                sub.truncated = idx.truncated
                sub.entry = sub.entry or str(exe)
                return sub

    # 5) 单文件游戏：目录里只有一个大 exe
    if not det.is_known and idx.exes and len(idx.top) <= 6:
        det.evidence.append("目录结构无明显标记，仅有 exe")

    return det


# ------------------------------------------------------------------ 统一入口
def _ctf_candidates(root) -> list:
    """目录里可能是 Clickteam 数据文件的候选，按文件大小从大到小。"""
    root = Path(root)
    out = []
    for pattern in ("*.dat", "*.ccn", "*.cct"):
        for p in root.glob(pattern):
            try:
                out.append((p.stat().st_size, p))
            except OSError:
                continue
    out.sort(key=lambda t: -t[0])
    return [p for _size, p in out]


def detect(path) -> Detection:
    """识别文件或目录。"""
    path = Path(path)
    if not path.exists():
        d = Detection()
        d.evidence.append("路径不存在")
        return d
    if path.is_dir():
        return scan_dir(path)

    det = scan_file(path)

    # 单个 .exe 可能是自解压/打包器，尝试在里面找内层
    if det.is_known and not det.entry:
        det.entry = str(path)
    return det
