# -*- coding: utf-8 -*-
"""setup.ini —— 放在程序旁边的配置文件，用来设置「提取结果存到哪」。

为什么用 ini 而不是 json
------------------------
这个文件是给用户拿记事本直接改的。ini 能写 `;` 注释，可以把「这里能填什么、
相对路径从哪算起」直接写在文件里；json 不支持注释，用户打开只看到一堆引号和
大括号，照样不知道能改什么。

行为约定
--------
* 文件不存在 -> 首次运行时自动生成一份带注释的默认配置
* 文件被改坏 -> 不让程序崩，退回默认值继续跑
* 相对路径 -> 一律相对「程序根目录」（源码运行时是项目根，打包后是
  exe 所在目录）。这样整包拷到别的盘、盘符变了都不会失效。
* 命令行 `-o` -> 优先级最高，覆盖这里的所有设置
"""

import configparser
from pathlib import Path

from me.config import ROOT

SETUP_NAME = "setup.ini"
SECTION = "Output"
KEY_PATH = "path"
DEFAULT_OUTPUT = "Extract Resources"

# 自动生成时写进文件的内容。里面的注释就是给用户看的说明书。
_TEMPLATE = """\
; ============================================================
;  True MultiExtractTool 配置文件
;
;  把它放在 MultiExtractor.exe 旁边，用记事本改就行。
;  删掉这个文件，下次运行会自动重新生成一份默认的。
; ============================================================

[Output]
; 提取出来的资源存到哪个目录。
;
;   相对路径 —— 从程序所在目录算起，例如：
;       path = Extract Resources
;   绝对路径 —— 直接写完整路径（换盘、放到别处用这个），例如：
;       path = D:\\拆包输出
;
; 命令行 -o 参数会覆盖这里的设置。
path = {default}
"""


def render() -> str:
    """生成一份带注释的默认配置文本。

    首次运行和打包时都用它，保证用户拿到手的 setup.ini 自带说明书。
    """
    return _TEMPLATE.format(default=DEFAULT_OUTPUT)


def setup_path() -> Path:
    """setup.ini 应该在哪。"""
    return ROOT / SETUP_NAME


def ensure() -> Path:
    """确保 setup.ini 存在；没有就写一份默认的。返回它的路径。"""
    p = setup_path()
    if not p.exists():
        try:
            p.write_text(render(), encoding="utf-8")
        except OSError:
            # 目录只读之类的情况下写不了，那就只用内置默认值，不影响提取
            pass
    return p


def _read_text(p: Path) -> str:
    """把 setup.ini 读成文本，尽量容忍各种编码。

    这里必须宽容，理由是实测出来的：Windows 记事本和不少编辑器
    「另存为 UTF-8」时会**带上 BOM**，而 configparser 碰到 BOM 会直接抛
    MissingSectionHeaderError —— 结果就是用户明明改了路径，程序却当没看见、
    默默用回默认值，还不吭声。中文 Windows 上还可能存成 ANSI(GBK)。
    """
    try:
        raw = p.read_bytes()
    except OSError:
        return ""
    # 记事本「另存为 UTF-16」会带这两个 BOM
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # 兜底：latin-1 不会失败。非 ASCII 会花掉，但路径几乎总是 ASCII。
    return raw.decode("latin-1")


def load() -> dict:
    """读配置。

    返回的字典里除了设置项，还有 `error` 键：
      - 空字符串 = 一切正常
      - 非空 = 配置有问题，已经退回默认值，内容是给用户看的原因
    刻意不在这里直接打印 —— 这个模块也被打包脚本 import，
    不该自己去动界面。
    """
    cfg = {KEY_PATH: DEFAULT_OUTPUT, "error": ""}
    p = ensure()
    if not p.exists():
        cfg["error"] = f"没能读到 {SETUP_NAME}，用的是内置默认保存路径"
        return cfg

    # 关掉 % 插值：Windows 路径里真可能出现 %，开了插值会直接报错
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(_read_text(p))
    except configparser.Error as exc:
        cfg["error"] = (f"{SETUP_NAME} 格式有问题（{type(exc).__name__}），"
                        f"已退回默认保存路径 {DEFAULT_OUTPUT!r}")
        return cfg

    if not parser.has_option(SECTION, KEY_PATH):
        cfg["error"] = (f"{SETUP_NAME} 里没有 [{SECTION}] {KEY_PATH}，"
                        f"已退回默认保存路径 {DEFAULT_OUTPUT!r}")
        return cfg

    val = parser.get(SECTION, KEY_PATH).strip()
    # 用户可能顺手给带空格的路径加了引号，去掉
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1].strip()
    if not val:
        cfg["error"] = (f"[{SECTION}] {KEY_PATH} 是空的，"
                        f"已退回默认保存路径 {DEFAULT_OUTPUT!r}")
        return cfg

    cfg[KEY_PATH] = val
    return cfg


def raw_output() -> str:
    """配置里原样写的保存路径（可能是相对路径）。"""
    return load()[KEY_PATH]


def output_root() -> Path:
    """保存路径的绝对形式。"""
    p = Path(raw_output()).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    return p


def describe() -> str:
    """给用户看的一行摘要。"""
    raw = raw_output()
    resolved = output_root()
    if str(resolved) == raw:
        return raw
    return f"{raw}  (-> {resolved})"
